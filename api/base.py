# -*- coding: utf-8 -*-
import functools
import random
import re
import threading
import time
from difflib import SequenceMatcher
from enum import Enum, IntEnum
from hashlib import md5
from typing import Self, Optional, Literal

import requests
from loguru import logger
from requests import RequestException
from requests.adapters import HTTPAdapter
from tqdm import tqdm

from api.answer import *
from api.answer_check import cut
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.cookies import save_cookies, use_cookies
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)
from api.exceptions import LoginError, MaxRetryExceeded
# 平台抽象层：Account / RateLimiter / StudyResult 的权威定义已迁到
# api/platform/contract.py 与 api/platform/account.py，这里 re-export
# 是为了兼容既有 `from api.base import Account, Chaoxing, StudyResult` 的写法。
from api.platform.contract import (
    ChapterResult,
    Platform,
    PlatformCapability,
    PlatformMeta,
    RateLimiter,
    StudyResult,
    normalize_course,
    normalize_point,
)
from api.platform.account import Account


def get_timestamp():
    return str(int(time.time() * 1000))


class SessionManager:
    """平台隔离的会话管理器。

    历史实现是全局单例，多平台接入后会互相污染 cookie。现在按
    ``platform_key`` 分别持有 session：``get_session()`` 默认取当前
    活动平台（chaoxing），因此既有调用点无需改动即保持原行为。
    """

    _instances: dict[str, "SessionManager"] = {}
    _init_lock = threading.Lock()
    _active_key = "chaoxing"

    def __new__(cls, platform_key: str = "chaoxing", *args, **kwargs):
        key = (platform_key or "chaoxing").lower()
        if key not in cls._instances:
            instance = super().__new__(cls)
            instance._platform_key = key
            instance._initialized = False
            cls._instances[key] = instance
        return cls._instances[key]

    def __init__(self, platform_key: str = "chaoxing", create: bool = True):
        key = (platform_key or "chaoxing").lower()
        if getattr(self, "_initialized", False):
            return
        if not create:
            # 惰性占位：避免 import 阶段就建连接
            return

        with self.__class__._init_lock:
            if getattr(self, "_initialized", False):
                return
            self._session = requests.Session()
            self._session.mount("https://", HTTPAdapter(max_retries=10))
            self._session.mount("http://", HTTPAdapter(max_retries=10))
            self._session.request = functools.partial(self._session.request, timeout=5)
            # For debug purposes
            # self._session.verify=False
            self._session.headers.clear()
            self._session.headers.update(gc.HEADERS)
            self._session.cookies.update(use_cookies(key))
            self._initialized = True

    # -- 新增：按平台取会话 -------------------------------------------
    @classmethod
    def for_platform(cls, platform_key: str) -> "SessionManager":
        """取（必要时创建）指定平台的会话管理器。"""
        key = (platform_key or "chaoxing").lower()
        instance = cls(key)
        if not getattr(instance, "_initialized", False):
            instance.__init__(key, create=True)
        return instance

    @classmethod
    def set_active_platform(cls, platform_key: str) -> None:
        """切换 get_session() 默认指向的平台。"""
        cls._active_key = (platform_key or "chaoxing").lower()

    @classmethod
    def get_instance(cls) -> Self:
        key = cls._active_key
        instance = cls(key)
        if not getattr(instance, "_initialized", False):
            instance.__init__(key, create=True)
        return instance

    @classmethod
    def get_session(cls, platform_key: str | None = None) -> requests.Session:
        if platform_key is None:
            return cls.get_instance()._session
        return cls.for_platform(platform_key)._session

    @classmethod
    def update_cookies(cls, platform_key: str | None = None):
        key = platform_key or cls._active_key
        cls.for_platform(key)._session.cookies.update(use_cookies(key))


class SignType(IntEnum):
    NORMAL = 0
    GESTURE = 3
    LOCATION = 4


class ActivityStatus(IntEnum):
    ACTIVE = 1
    INACTIVE = 2


class ActivityType(IntEnum):
    SIGNIN = 2


class Chaoxing(Platform):
    """超星/学习通平台实现。

    继承 ``api.platform.contract.Platform`` 后，它就自动被识别为一个
    平台 adapter；同时保留全部历史方法名与返回值，旧代码零改动可用。
    """

    key = "chaoxing"
    meta = PlatformMeta(
        key="chaoxing",
        name="超星学习通",
        home="https://i.chaoxing.com",
        account_hint="手机号",
        password_hint="密码",
        cookie_hint="超星 Cookie 字符串（key=value; key2=value2）",
        notes="内置视频/音频/文档/阅读/作业/直播/自动签到全量支持",
    )

    def __init__(self, account: Account = None, tiku: Tiku = None, **kwargs):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.kwargs = kwargs
        self.config = kwargs.pop("config", None) or {}
        # 平台身份：cookie 落盘路径、SessionManager 都以它区分
        self.platform_key = kwargs.pop("platform_key", None) or getattr(
            self, "key", "chaoxing"
        )
        self.rollback_times = 0
        self.rate_limiter = RateLimiter(0.3)  # 其他接口速率限制比较松
        self.video_log_limiter = RateLimiter(1.5)  # 上报进度极其容易卡验证码，限制1.5s一次
        self.read_duration = kwargs.get("read_duration", 300)  # 阅读任务默认模拟时长（秒）
        # 让超星会话成为默认活动会话（历史行为：SessionManager 全局单例）
        SessionManager.set_active_platform(self.platform_key)

    # -- Platform 契约 ---------------------------------------------------
    def _create_session(self):
        """超星使用 SessionManager 全局会话，不单独建 session。"""
        return SessionManager.get_session(self.platform_key)

    def _login_with_password(self):
        return self.login(login_with_cookies=False)

    def _login_with_cookies(self):
        return self.login(login_with_cookies=True)

    def validate_session(self) -> bool:
        return self._validate_cookie_session()

    def login(self, login_with_cookies=False):
        try:
            return self._do_login(login_with_cookies)
        except LoginError:
            raise
        except Exception as e:
            if login_with_cookies:
                raise LoginError(f"Cookie 登录异常: {e}") from e
            raise LoginError(f"账号密码登录异常: {e}") from e

    def _do_login(self, login_with_cookies=False):
        if login_with_cookies:
            logger.info("Logging in with cookies")
            SessionManager.update_cookies(self.platform_key)
            logger.debug(f"Logged in with cookies: {SessionManager.get_instance()._session.cookies}")
            if not self._validate_cookie_session():
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}

        _session = requests.Session()
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        resp = _session.post(_url, headers=gc.HEADERS, data=_data)
        if resp and resp.json()["status"] == True:
            save_cookies(_session, self.platform_key)
            SessionManager.update_cookies(self.platform_key)
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}
        else:
            return {"status": False, "msg": str(resp.json()["msg2"])}

    def _validate_cookie_session(self) -> bool:
        session = SessionManager.get_instance()._session
        if not session.cookies.get("_uid"):
            return False

        test_session = requests.Session()
        test_session.headers.update(gc.HEADERS)
        test_session.cookies.update(session.cookies.get_dict())

        try:
            resp = test_session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException as exc:
            logger.debug("Cookie validation request failed: {}", exc)
            return False

        if resp.status_code != 200:
            return False

        if "passport2.chaoxing.com" in resp.text or "login" in resp.text.lower():
            return False

        return True

    def get_fid(self):
        _session = SessionManager.get_session()
        return _session.cookies.get("fid", 1024)

    def get_uid(self):
        s = SessionManager.get_session()
        if "_uid" in s.cookies:
            return s.cookies["_uid"]
        if "UID" in s.cookies:
            return s.cookies["UID"]
        raise ValueError("Cannot get uid !")

    def get_course_list(self):
        _session = SessionManager.get_session(self.platform_key)
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")

        # 接口突然抽风, 增加headers
        # 有可能只是referer的问题
        _headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
        }
        _resp = _session.post(_url, headers=_headers, data=_data)
        # logger.trace(f"原始课程列表内容:\n{_resp.text}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url)
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = _session.post(_url, data=_data)
            course_list += decode_course_list(_resp.text)
        # 补齐规范化字段（platform / 字符串化 ID），供多平台通用流程使用
        return [normalize_course(c, self.platform_key) for c in course_list]

    def get_activity_list(self, course: dict) -> list[dict]:
        s = SessionManager.get_session()
        url = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
        params = {
            "fid": self.get_fid(),
            "courseId": course["courseId"],
            "classId": course["clazzId"],
            "showNotStartedActive": 0,
            "_": get_timestamp()
        }
        resp = s.get(url, params=params, allow_redirects=False)
        if resp.status_code != 200:
            logger.error("Failed to get activity list, return code: " + str(resp.status_code))
            logger.debug("Request url: " + resp.url)
            return []

        data = resp.json()
        if data["result"] != 1:
            logger.error("Unknown status: {} {}", data["result"], data["errorMsg"])
            logger.debug("Request url: " + resp.url)
            return []

        return data["data"]["activeList"]


    def pre_sign(self, course: dict, activity_id):
        s = SessionManager.get_session()
        params = {
            "general": 1,
            "sys": 1,
            "ls": 1,
            "appType": 15,
            "tid": '',
            "ut": 's',
            "uid": self.get_uid(),
            "activePrimaryId": activity_id,
            "courseId": course["courseId"],
            "classId": course["clazzId"],
        }
        resp = s.get('https://mobilelearn.chaoxing.com/newsign/preSign', params=params)
        resp_txt = resp.text
        logger.debug("Request url" + resp.url)
        if resp.status_code != 200:
            logger.error("Failed to get sign in, return code: " + str(resp.status_code) + "message: " + resp_txt)

        return resp_txt


    def sign_in_normal(self, course: dict, activity_id, name="", obj_id="aaa", lat=-1, lon=-1, type_=SignType.NORMAL):
        s = SessionManager.get_session()
        params = {
            "activeId": activity_id,
            "uid": self.get_uid(),
            "fid": self.get_fid(),
            "courseId": course["courseId"],
            "classId": course["clazzId"],
            "clientip": "",
            "objectId": obj_id,
            "name": name,
            "useragent": "",
            "latitude": lat,
            "longitude": lon,
            "appType": "15",
        }

        resp = s.get("https://mobilelearn.chaoxing.com/pptSign/stuSignajax", params=params)

        resp_txt = resp.text
        if resp.status_code != 200:
            logger.error("Failed to get sign in, return code: " + str(resp.status_code) + "message: " + resp_txt)

        if type_ != SignType.LOCATION:
            return resp_txt

        pattern = r"[^0-9\.]*(.+)米[^0-9\.]*"
        msg = re.match(pattern, resp_txt)
        logger.warning(f"距离签到位置 {msg}m")
        # TOD0: Implement triangulation for location signs
        return resp_txt


    def get_course_point(self, _courseid=None, _clazzid=None, _cpi=None, course=None):
        """读取课程章节。

        兼容两种调用方式：
        * 旧写法 ``get_course_point(courseid, clazzid, cpi)``（web/主流程历史调用）
        * 新写法 ``get_course_point(course)``（Platform 契约，参数是课程字典）
        """
        if course is not None:
            _courseid = _courseid or course.get("courseId")
            _clazzid = _clazzid or course.get("clazzId")
            _cpi = _cpi or course.get("cpi")
        _session = SessionManager.get_session(self.platform_key)
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("URL: " + _url)
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)

        logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        result = decode_course_point(_resp.text)
        return {
            **result,
            "points": [normalize_point(p) for p in result.get("points", [])],
        }

    def process_job(self, course: dict, job: dict, job_info: dict,
                    speed: float = 1.0, stop_event=None) -> StudyResult:
        """执行单个任务点（Platform 契约）。

        从 main.py 的 ``process_job`` 下沉而来，这样每个平台可以自由决定
        "一个任务点需要走几段请求"，而主流程只负责调度和重试。
        """
        job_type = job.get("type")
        # 视频任务
        if job_type == "video":
            logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
            video_result = self.study_video(
                course, job, job_info, _speed=speed, _type="Video",
                stop_event=stop_event,
            )
            if video_result == StudyResult.FORBIDDEN:
                # 进度上报接口403与媒体类型无关, 直接判定失败, 不再尝试音频
                logger.warning("进度上报被服务端拒绝(403), 任务跳过 -> 任务章节: {} 任务ID: {}",
                               course["title"], job["jobid"])
                return video_result
            if video_result.is_failure():
                logger.warning(
                    f"视频任务未通过 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
                )
            return video_result
        # 文档任务
        if job_type == "document":
            logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
            return self.study_document(course, job)
        # 章节检测 / 作业
        if job_type in ("workid", "work"):
            logger.trace(f"识别到章节检测/作业任务, 任务章节: {course['title']}")
            return self.study_work(course, job, job_info, stop_event=stop_event)
        # 阅读任务
        if job_type == "read":
            logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
            return self.study_read(course, job, job_info, _speed=speed, stop_event=stop_event)
        # 直播任务
        if job_type == "live":
            logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
            try:
                from api.live import Live
                from api.live_process import LiveProcessor
                defaults = {
                    "userid": self.get_uid(),
                    "clazzId": course.get("clazzId"),
                    "knowledgeid": job_info.get("knowledgeid"),
                }
                live = Live(
                    attachment=job,
                    defaults=defaults,
                    course_id=course.get("courseId"),
                )
                thread = threading.Thread(
                    target=LiveProcessor.run_live,
                    args=(live, speed),
                    daemon=True,
                )
                thread.start()
                thread.join()
                return StudyResult.SUCCESS
            except Exception as e:
                logger.error(f"处理直播任务时出错: {str(e)}")
                return StudyResult.ERROR

        logger.error(f"未知任务类型: {job_type}")
        return StudyResult.ERROR

    def get_job_list(self, course: dict, point: dict) -> tuple[list[dict], dict]:
        _session = SessionManager.get_session(self.platform_key)
        self.rate_limiter.limit_rate()
        job_list = []
        job_info = {}
        cards_params = {
            "clazzid": course["clazzId"],
            "courseid": course["courseId"],
            "knowledgeid": point["id"],
            "ut": "s",
            "cpi": course["cpi"],
            "v": "2025-0424-1038-3",
            "mooc2": 1
        }

        # 学习界面任务卡片数, 很少有3个的, 但是对于章节解锁任务点少一个都不行, 可以从API /mooc-ans/mycourse/studentstudyAjax获取值, 或者干脆直接加, 但二者都会造成额外的请求
        _empty_streak = 0
        for _possible_num in "0123456":

            logger.trace("开始读取章节所有任务点...")

            cards_params.update({"num": _possible_num})
            _resp = _session.get("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards", params=cards_params)
            if _resp.status_code != 200:
                logger.error(f"未知错误: {_resp.status_code} 正在跳过")
                logger.error(_resp.text)
                return [], {}

            _job_list, _job_info = decode_course_card(_resp.text)
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info

            if not _job_list:
                _empty_streak += 1
                if _empty_streak >= 2 and job_list:
                    # 连续2个空响应且已有任务点，提前退出
                    break
            else:
                _empty_streak = 0

            job_list += _job_list
            job_info.update(_job_info)

        if not job_list:
            _has_marg = "mArg" in _resp.text
            if _has_marg:
                logger.debug(f"章节含mArg但无任务点")
            else:
                logger.info(f"章节无任务点, 长度: {len(_resp.text)}")
            self.study_emptypage(course, point)

        logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info("章节任务点读取成功...")

        return job_list, job_info

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]"
            .encode()).hexdigest()

    def video_progress_log(
            self,
            _session,
            _course,
            _job,
            _job_info,
            _dtoken,
            _duration,
            _playingTime,
            _type: str = "Video",
            _isdrag: int = 3,
            headers: Optional[dict] = None,
    ) -> tuple[bool, int]:

        if headers is None:
            logger.warning("null headers")
            headers = gc.VIDEO_HEADERS

        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        if "courseId" in _job["otherinfo"]:
            logger.error(_job["otherinfo"])
            raise RuntimeError("this is not possible")

        enc = self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, self.get_uid())
        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": _isdrag,
            "view": "pc",
            "enc": enc,
            "dtype": _type
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/"
            f"{_dtoken}"
        )

        face_capture_enc = _job["videoFaceCaptureEnc"]
        att_duration = _job["attDuration"]
        att_duration_enc = _job["attDurationEnc"]

        if face_capture_enc:
            params["videoFaceCaptureEnc"] = face_capture_enc
        if att_duration:
            params["attDuration"] = att_duration
        if att_duration_enc:
            params["attDurationEnc"] = att_duration_enc

        rt = _job['rt']
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job['otherinfo'])
            if rt_search:
                rt_char = rt_search.group(1)
                rt = "0.9" if rt_char == "d" else "1"
                logger.trace(f"Got rt from otherinfo: {rt}")

        if rt:
            logger.trace(f"Got rt: {rt}")
            _job['rt'] = rt
            params.update({"rt": rt,
                           "_t": get_timestamp()})
            resp = _session.get(_url, params=params, headers=headers, timeout=(5, 15))
        else:
            logger.warning("Failed to get rt")
            for rt in [0.9, 1]:
                params.update({"rt": rt,
                               "_t": get_timestamp()})
                resp = _session.get(_url, params=params, headers=headers, timeout=(5, 15))
                if resp.status_code == 200:
                    logger.trace(resp.text)
                    return resp.json()["isPassed"], 200
                # elif resp.ok:
                #    # TODO: 处理验证码
                #    pass
                elif resp.status_code == 403:
                    logger.warning("出现403报错, 正常尝试切换rt")

                else:
                    logger.warning("未知错误 jobid={}, status_code={}, 摘要:\n{}",
                                   _job.get("jobid"),
                                   resp.status_code,
                                   resp.text[:200])
                    break

        if resp.status_code == 200:
            logger.trace(resp.text)
            return resp.json()["isPassed"], 200

        elif resp.status_code == 403:
            logger.debug(
                "视频进度上报返回403, jobid={}, 摘要={}",
                _job.get("jobid"),
                resp.text[:200],
            )

            # 若出现两个rt参数都返回403的情况, 则跳过当前任务
            logger.error("出现403报错, 尝试修复无效, 正在跳过当前任务点...")
            logger.error("请求url: {}", resp.url)
            logger.error("请求头: {}", dict(_session.headers) | headers)
            return False, 403

        logger.error(f"未知错误: {resp.status_code}")
        logger.error("请求url: {}", resp.url)
        logger.error("请求头: {}", dict(_session.headers) | headers)
        return False, resp.status_code

    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]) \
            -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?"
            f"k={self.get_fid()}&flag=normal"
        )
        try:
            resp = session.get(info_url, timeout=8, headers=headers)
        except RequestException as exc:
            logger.debug("刷新视频状态失败: {}", exc)
            return None

        if resp.status_code != 200:
            logger.debug("刷新视频状态返回码异常: {}" % resp.status_code)
            logger.debug(resp.text)
            return None

        try:
            data = resp.json()
        except ValueError as exc:
            logger.debug("解析视频状态响应失败: {}", exc)
            return None

        if data.get("status") == "success":
            return data

        return None

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]):
        SessionManager.update_cookies()
        refreshed = self._refresh_video_status(session, job, _type)
        if refreshed:
            return refreshed

        # FIXME: Temporarily disabled for multithreading support
        if False and self.account and self.account.username and self.account.password:
            login_result = self.login(login_with_cookies=False)
            if login_result.get("status"):
                SessionManager.update_cookies()
                return self._refresh_video_status(session, job, _type)
            logger.warning("账号密码登录失败: {}", login_result.get("msg"))

        return None

    def _read_progress_log(self, _session, _course, _job, _job_info) -> tuple[bool, int]:
        """
        Report reading progress / heartbeat to Chaoxing server.

        Periodically calling this method keeps the reading session alive
        and accumulates reading duration on the server side.
        """
        self.rate_limiter.limit_rate(random_time=True, random_max=2)

        try:
            _resp = _session.get(
                url="https://mooc1.chaoxing.com/ananas/job/readv2",
                params={
                    "jobid": _job.get("jobid", ""),
                    "knowledgeid": _job_info.get("knowledgeid", ""),
                    "jtoken": _job.get("jtoken", ""),
                    "courseid": _course["courseId"],
                    "clazzid": _course["clazzId"],
                },
                timeout=10,
            )
        except RequestException:
            return False, 0

        if _resp.status_code == 200:
            try:
                data = _resp.json()
                status = str(data.get("status", "")).lower()
                msg = data.get("msg", "")
                passed = status in ("", "true", "success", "1") or msg == "success"
                return passed, 200
            except ValueError:
                return True, 200

        return False, _resp.status_code

    def study_video(self, _course, _job, _job_info, _speed: float = 1.0,
                    _type: Literal["Video", "Audio"] = "Video",
                    stop_event: threading.Event | None = None) -> StudyResult:
        _session = SessionManager.get_session()

        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        _info_url = f"https://mooc1.chaoxing.com/ananas/status/{_job['objectid']}?k={self.get_fid()}&flag=normal"
        # 显式超时 + 异常兜底：无超时的请求会让线程永久挂起，
        # 进而使 task_queue.join() 永不返回，表现为整个刷课卡死。
        try:
            _resp = _session.get(_info_url, headers=headers, timeout=(5, 15))
        except RequestException as exc:
            logger.error("获取视频信息失败(网络): {}", exc)
            return StudyResult.ERROR

        try:
            _video_info = _resp.json()
        except ValueError:
            logger.error("获取视频信息失败: 响应不是有效JSON, status={}", _resp.status_code)
            return StudyResult.ERROR

        if not isinstance(_video_info, dict) or _video_info.get("status") != "success":
            logger.error(f"Unknown status: {_video_info.get('status') if isinstance(_video_info, dict) else _video_info}")
            return StudyResult.ERROR

        _dtoken = _video_info["dtoken"]

        duration = int(_video_info["duration"])
        if duration <= 0:
            logger.error("视频时长异常({}s), 跳过: {}", duration, _job.get("name"))
            return StudyResult.ERROR
        # 服务端已认可的"累计观看时长"(秒)。注意这不是"播放位置":
        # 超星按上报节奏累计有效观看时长, 一次性跳到片尾会把 playTime 刷满却永远拿不到 isPassed。
        credited = max(0, min(int(_job.get("playTime") or 0) // 1000, duration))

        logger.info(f"开始任务: {_job['name']}, 总时长: {duration}s, 服务端已认可观看: {credited}s")

        pbar = tqdm(total=duration, initial=credited, desc=_job["name"],
                    unit_scale=True, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        forbidden_retry = 0
        max_forbidden_retry = 2  # 配合递增退避，给风控封禁窗口留出解除时间
        video_start_time = time.time()  # 视频任务开始时间
        _last_progress_print = time.time()  # 用于周期性打印推进情况

        # 上报节奏(2026-10 实测结论):
        #   - 服务端只认可"循序渐进"的进度增量: 每 N 秒上报一次, 位置前进 N*倍速;
        #   - 一次性从 0 报到 duration -> playTime 被写满但 isPassed 永远 false(历史踩坑);
        #   - 按 2x/4x 节奏推进 -> 大多数视频在几分钟内就能拿到 isPassed=true。
        report_interval = 10
        eff_speed = max(1.0, min(float(_speed or 1.0), float(gc.MAX_REPORT_SPEED)))
        step = max(1, int(round(report_interval * eff_speed)))
        # 单个任务最大处理时间(秒): 按"上报轮数 x 每轮真实耗时"估算。
        # 每轮真实耗时 = report_interval + 上报请求耗时 + 限速抖动(约 2s),
        # 再留出 403 冷却(最多 180s)与锚点/重试余量, 否则长视频会"到片尾前就被超时判死"。
        _rounds = (duration + step - 1) // step
        max_video_duration = int(_rounds * (report_interval + 6)) + 420
        # 服务端下发的建议上报间隔, 仅用于日志/诊断(实测 10s 节奏可被接受)
        logger.trace(
            "reportTimeInterval={}s, 本地上报节奏={}s",
            _job_info.get("reportTimeInterval"), report_interval,
        )

        # 历史"秒过"/中断留下的脏状态: 服务端累计观看已接近片尾但任务点仍未通过。
        # 这种状态靠"再报一次满进度"永远修不好(实测 isPassed 恒为 false);
        # 必须发一条 complete(isdrag=4) 报文把本会话的累计观看锚回起点, 再从 0 重新累计。
        # 容差取 5% 或一个上报步长(取大者): 覆盖"差几秒/差几十秒仍未通过"的中间状态。
        _poison_tol = max(step, duration // 20)
        if credited + _poison_tol >= duration:
            logger.warning(
                "任务点未通过但服务端累计观看已接近/达到片尾({}s/{}s), 重置本会话进度后重新累计: {}",
                credited, duration, _job["name"],
            )
            try:
                _anchor_passed, _anchor_state = self.video_progress_log(
                    _session, _course, _job, _job_info, _dtoken, duration, 0,
                    _type, 4, headers=headers,
                )
            except Exception as exc:
                logger.error("重置进度报文异常: {} | {}", exc, _job["name"])
                pbar.close()
                return StudyResult.ERROR
            if _anchor_state == 403:
                logger.error("重置进度报文被服务端拒绝(403), 跳过: {}", _job["name"])
                pbar.close()
                return StudyResult.FORBIDDEN
            if _anchor_state != 200:
                logger.error("重置进度失败(status={}), 跳过: {}", _anchor_state, _job["name"])
                pbar.close()
                return StudyResult.ERROR
            credited = 0
            pbar.n = 0
            pbar.refresh()

        # 版本标记: 用于确认实际加载的是新代码(节奏上报模式), 而非旧 pyc 缓存
        logger.info(
            "[CODE v3] 节奏上报模式 | 起点={}/{}s | 倍速={:.1f}x | 每{}s上报一次 | 超时预算={}s",
            int(credited), duration, eff_speed, report_interval, max_video_duration,
        )

        passed = False
        state: int = 0
        at_end_logged = False

        while not passed:
            if stop_event and stop_event.is_set():
                logger.warning(f"收到停止信号，中断视频任务: {_job['name']}")
                pbar.close()
                return StudyResult.ERROR

            # 按节奏推进: 每次上报把观看位置前进 step 秒(约 report_interval * 倍速),
            # 服务端才会把这部分计入"有效观看时长"; 到达片尾后继续补报,
            # 直到服务端返回 isPassed=true (实测通过点可能在片尾之前)。
            # 到片尾仍不通过时: 改用 isdrag=4(complete) 的收尾报文, 并缩短间隔。
            # 只报 isdrag=3 时, 部分视频在累计观看满时长后仍会一直 isPassed=false。
            at_end = credited >= duration
            # 用可中断等待替代 time.sleep: 停止信号必须在等待期间立即生效,
            # 否则每次点"停止"都要等满一个上报周期(10s)才有反应。
            if stop_event:
                if stop_event.wait(5 if at_end else report_interval):
                    logger.warning(f"收到停止信号，中断视频任务: {_job['name']}")
                    pbar.close()
                    return StudyResult.ERROR
            else:
                time.sleep(5 if at_end else report_interval)
            credited = min(duration, credited + step)
            logger.debug(
                "[上报] 任务={} | 观看位置={}/{}s | 倍速={:.1f}x | isdrag={}",
                _job['name'], int(credited), duration, eff_speed, 4 if at_end else 3,
            )
            passed, state = False, 0
            try:
                passed, state = self.video_progress_log(
                    _session, _course, _job, _job_info, _dtoken, duration,
                    int(credited), _type,
                    4 if at_end else 3,
                    headers=headers,
                )
            except Exception as exc:
                # 验证码页/非 JSON 响应/字段缺失等异常不应让 pbar 泄漏, 也不该杀死 worker
                logger.error("上报进度异常: {} | {}", exc, _job["name"])
                pbar.close()
                return StudyResult.ERROR
            logger.debug("[上报结果] isPassed={} status={} | {}", passed, state, _job['name'])

            if state == 403:
                # 触发全局冷却：所有线程暂停一段时间，避免并发线程继续撞风控
                cooldown_secs = min(180, 60 * (forbidden_retry + 1))
                self.video_log_limiter.cooldown(cooldown_secs)
                logger.warning("触发风控403, 全局冷却 {:.0f}s", cooldown_secs)

                if forbidden_retry >= max_forbidden_retry:
                    logger.warning("403重试失败, 跳过当前任务")
                    pbar.close()
                    return StudyResult.FORBIDDEN
                forbidden_retry += 1
                # 递增退避：固定 2-4s 后重发几乎相同的报文必然再次 403，
                # 按重试次数逐步拉长等待(约 8/16/24/32s)，给风控封禁窗口留出解除时间。
                backoff = min(60, 8 * forbidden_retry) + random.uniform(0, 5)
                logger.warning(
                    "出现403报错, 等待{:.0f}s后尝试刷新会话状态 (第{}次)",
                    backoff,
                    forbidden_retry,
                )
                # 长退避期间同样要能被停止信号打断
                if stop_event and stop_event.wait(backoff):
                    logger.warning(f"收到停止信号，中断视频任务: {_job['name']}")
                    pbar.close()
                    return StudyResult.ERROR
                if not stop_event:
                    time.sleep(backoff)
                refreshed_meta = self._recover_after_forbidden(_session, _job, _type)
                if refreshed_meta:
                    # 只换 dtoken, 不回退本地已推进的观看位置:
                    # 风控期服务端常原样返回旧进度, 覆盖会让进度条原地踏步。
                    _dtoken = refreshed_meta.get("dtoken", _dtoken)
                    logger.debug("Refreshed token: {}, 本地观看位置: {}", _dtoken, credited)
                continue

            if not passed and state != 200:
                pbar.close()
                return StudyResult.ERROR

            pbar.n = int(credited)
            pbar.refresh()

            if passed:
                break

            # 每 30 秒打印一次推进情况，便于观察进度是否真的在前进
            if time.time() - _last_progress_print >= 30:
                _last_progress_print = time.time()
                logger.info(
                    "[推进] {} | 已上报观看位置 {:.0f}s / {}s | 等待服务端确认通过",
                    _job['name'], credited, duration,
                )

            if credited >= duration and not at_end_logged:
                at_end_logged = True
                logger.debug("已上报到片尾, 继续补报直到服务端确认通过: {}", _job['name'])

            # 总超时检查：走完整个视频 + 余量后仍未通过则跳过
            if time.time() - video_start_time > max_video_duration:
                logger.error(f"视频上报超时({max_video_duration}s)仍未通过，跳过: {_job['name']}")
                pbar.close()
                return StudyResult.ERROR

        pbar.close()
        logger.info("任务完成: {} (已上报观看位置 {:.0f}s)", _job['name'], credited)
        return StudyResult.SUCCESS

    def study_document(self, _course, _job) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            requests.Response: Response object from the GET request

        Note:
            This method requires the following helper functions:
            - init_session(): To initialize a new session
            - get_timestamp(): To get current timestamp
            - re module for regular expression matching
        """
        _session = SessionManager.get_session()
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        _resp = _session.get(_url)
        if _resp.status_code != 200:
            return StudyResult.ERROR
        else:
            return StudyResult.SUCCESS

    def study_work(self, _course, _job, _job_info,
                   stop_event: threading.Event | None = None) -> StudyResult:
        # FIXME: 这一块可以单独搞一个类出来了，方法里面又套方法，每一次调用都会创建新的方法，十分浪费
        if self.tiku.DISABLE or not self.tiku:
            return StudyResult.SUCCESS
        _ORIGIN_HTML_CONTENT = ""  # 用于配合输出网页源码, 帮助修复#391错误

        def random_answer(options: str) -> str:
            answer = ""
            if not options:
                return answer

            if q["type"] == "multiple":
                logger.debug(f"当前选项列表[cut前] -> {options}")
                _op_list = multi_cut(options)
                logger.debug(f"当前选项列表[cut后] -> {_op_list}")

                if not _op_list:
                    logger.error(
                        "选项为空, 未能正确提取题目选项信息! 请反馈并提供以上信息"
                    )
                    return answer

                available_options = len(_op_list)
                select_count = 0

                # 根据可用选项数量调整可能选择的选项数
                if available_options <= 1:
                    select_count = available_options
                else:
                    max_possible = min(4, available_options)
                    min_possible = min(2, available_options)

                    weights_map = {
                        2: [1.0],
                        3: [0.3, 0.7],
                        4: [0.1, 0.5, 0.4],
                        5: [0.1, 0.4, 0.3, 0.2],
                    }

                    weights = weights_map.get(max_possible, [0.3, 0.4, 0.3])
                    possible_counts = list(range(min_possible, max_possible + 1))

                    weights = weights[:len(possible_counts)]

                    weights_sum = sum(weights)
                    if weights_sum > 0:
                        weights = [w / weights_sum for w in weights]

                    select_count = random.choices(possible_counts, weights=weights, k=1)[0]

                selected_options = random.sample(_op_list, select_count) if select_count > 0 else []

                for option in selected_options:
                    answer += option[:1]  # 取首字为答案，例如A或B

                answer = "".join(sorted(answer))
            elif q["type"] == "single":
                answer = random.choice(options.split("\n"))[:1]  # 取首字为答案, 例如A或B
            # 判断题处理
            elif q["type"] == "judgement":
                # answer = self.tiku.jugement_select(_answer)
                answer = "true" if random.choice([True, False]) else "false"
            logger.info(f"随机选择 -> {answer}")
            return answer

        def multi_cut(answer: str):
            """
            将多选题答案字符串按特定字符进行切割, 并返回切割后的答案列表

            参数:
            answer(str): 多选题答案字符串.

            返回:
            list[str]: 切割后的答案列表,如果无法切割, 则返回默认的选项列表None

            注意:
            如果无法从网页中提取题目信息,将记录警告日志并返回None
            """
            # cut_char = [',','，','|','\n','\r','\t','#','*','-','_','+','@','~','/','\\','.','&',' ']    # 多选答案切割符
            # ',' 在常规被正确划分的, 选项中出现, 导致 multi_cut 无法正确划分选项 #391
            # IndexError: Cannot choose from an empty sequence #391
            # 同时为了避免没有考虑到的 case, 应该先按照 '\n' 匹配, 匹配不到再按照其他字符匹配
            cut_char = [
                "\n",
                ",",
                "，",
                "|",
                "\r",
                "\t",
                "#",
                "*",
                "-",
                "_",
                "+",
                "@",
                "~",
                "/",
                "\\",
                ".",
                "&",
                " ",
                "、",
            ]  # 多选答案切割符
            res = cut(answer)
            if res is None:
                logger.warning(
                    f"未能从网页中提取题目信息, 以下为相关信息：\n\t{answer}\n\n{_ORIGIN_HTML_CONTENT}\n"
                )  # 尝试输出网页内容和选项信息
                logger.warning("未能正确提取题目选项信息! 请反馈并提供以上信息")
                return None
            else:
                return res

        def clean_res(res):
            cleaned_res = []
            if isinstance(res, str):
                res = [res]
            for c in res:
                # 仅在字符串长度大于1时才尝试去除开头的字母编号，防止误删单个字母答案
                cleaned = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*|[.,!?;:，。！？；：]', '', c) if len(c) > 1 else c
                cleaned_res.append(cleaned.strip())

            return cleaned_res

        def normalize_text(text: str) -> str:
            if not isinstance(text, str):
                text = str(text)
            # 统一常见异体字符，降低“风/⻛”类差异导致的匹配失败。
            char_map = str.maketrans({
                '⻛': '风',
                '⻔': '门',
                '⻋': '车',
                '⻢': '马',
            })
            normalized = text.translate(char_map)
            normalized = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', normalized)
            normalized = re.sub(r'\s+', '', normalized)
            normalized = re.sub(r'[，。！？；：,.!?;:()（）\[\]【】"“”‘’\-_/\\|]', '', normalized)
            return normalized.lower()

        def get_option_text(option: str) -> str:
            return re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', option).strip()

        def best_option_by_similarity(target: str, options: list, threshold: float = 0.8) -> str:
            if not target or not options:
                return ""
            target_norm = normalize_text(target)
            if not target_norm:
                return ""

            best_letter = ""
            best_score = 0.0
            for option in options:
                option_text = get_option_text(option)
                option_norm = normalize_text(option_text)
                if not option_norm:
                    continue
                score = SequenceMatcher(None, target_norm, option_norm).ratio()
                if score > best_score:
                    best_score = score
                    best_letter = option[:1]

            if best_score >= threshold:
                logger.info(f"相似度兜底匹配成功: {best_letter} (score={best_score:.2f}, threshold={threshold:.2f})")
                return best_letter
            return ""

        def is_subsequence(a, o):
            iter_o = iter(o)
            return all(c in iter_o for c in a)

        # FIXME: Use tenacity for retrying
        def with_retry(max_retries=3, delay=1):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    retries = 0
                    while retries < max_retries:
                        try:
                            _resp = func(*args, **kwargs)

                            # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
                            if '教师未创建完成该测验' in _resp.text:
                                raise PermissionError("教师未创建完成该测验")

                            questions = decode_questions_info(_resp.text)

                            if _resp.status_code == 200 and questions.get("questions"):
                                return (_resp, questions)

                            logger.warning(
                                f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中... ({retries + 1}/{max_retries})")

                        except requests.exceptions.RequestException as e:
                            logger.warning(f"请求失败: {str(e)[:50]}, 重试中... ({retries + 1}/{max_retries})")
                        retries += 1
                        time.sleep(delay * (2 ** retries))
                    raise MaxRetryExceeded(f"超过最大重试次数 ({max_retries})")

                return wrapper

            return decorator

        # 学习通这里根据参数差异能重定向至两个不同接口, 需要定向至https://mooc1.chaoxing.com/mooc-ans/workHandle/handle
        _session = SessionManager.get_session()

        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        @with_retry(max_retries=3, delay=1)
        def fetch_response():
            return _session.get(
                _url,
                params={
                    "api": "1",
                    "workId": _job["jobid"].replace("work-", ""),
                    "jobid": _job["jobid"],
                    "originJobId": _job["jobid"],
                    "needRedirect": "true",
                    "skipHeader": "true",
                    "knowledgeid": str(_job_info["knowledgeid"]),
                    "ktoken": _job_info["ktoken"],
                    "cpi": _job_info["cpi"],
                    "ut": "s",
                    "clazzId": _course["clazzId"],
                    "type": "",
                    "enc": _job["enc"],
                    "mooc2": "1",
                    "courseid": _course["courseId"],
                }
            )

        final_resp = {}
        questions = {}

        try:
            final_resp, questions = fetch_response()
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return StudyResult.ERROR

        _ORIGIN_HTML_CONTENT = final_resp.text  # 用于配合输出网页源码, 帮助修复#391错误

        # 搜题
        total_questions = len(questions["questions"])
        found_answers = 0
        for q in questions["questions"]:
            logger.debug(f"当前题目信息 -> {q}")
            # 添加搜题延迟 #428 - 默认0s延迟
            query_delay = self.kwargs.get("query_delay", 0)
            time.sleep(query_delay)
            res = self.tiku.query(q)
            answer = ""
            if not res:
                # 随机答题
                answer = random_answer(q["options"])
                q[f'answerSource{q["id"]}'] = "random"
            else:
                # 根据响应结果选择答案
                if q["type"] == "multiple":
                    # 多选处理
                    options_list = multi_cut(q["options"])
                    res_list = multi_cut(res)
                    if res_list is not None and options_list is not None:
                        for _a in clean_res(res_list):
                            matched = False
                            for o in options_list:
                                if (
                                        is_subsequence(_a, o)  # 去掉各种符号和前面ABCD的答案应当是选项的子序列
                                ):
                                    answer += o[:1]
                                    matched = True
                                    break # 找到匹配项后立即停止，防止重复添加
                            if not matched:
                                best_letter = best_option_by_similarity(_a, options_list, threshold=0.8)
                                if best_letter:
                                    answer += best_letter
                        # 对答案进行排序, 否则会提交失败
                        answer = "".join(sorted(set(answer)))
                    # else 如果分割失败那么就直接到下面去随机选
                elif q["type"] == "single":
                    # 单选也进行切割，主要是防止返回的答案有异常字符
                    options_list = multi_cut(q["options"])
                    if options_list is not None:
                        t_res = clean_res(res)
                        for o in options_list:
                            if is_subsequence(t_res[0], o):
                                answer = o[:1]
                                break
                        if not answer and t_res:
                            answer = best_option_by_similarity(t_res[0], options_list, threshold=0.8)
                elif q["type"] == "judgement":
                    answer = "true" if self.tiku.judgement_select(res) else "false"
                elif q["type"] == "completion":
                    if isinstance(res, list):
                        answer = "".join(res)
                    elif isinstance(res, str):
                        answer = res
                else:
                    # 其他类型直接使用答案 （目前仅知有简答题，待补充处理）
                    answer = res

                if not answer:  # 检查 answer 是否为空
                    logger.warning(f"找到答案但答案未能匹配 -> {res}\t随机选择答案")
                    answer = random_answer(q["options"])  # 如果为空，则随机选择答案
                    q[f'answerSource{q["id"]}'] = "random"
                else:
                    logger.info(f"成功获取到答案：{answer}")
                    q[f'answerSource{q["id"]}'] = "cover"
                    found_answers += 1
            # 填充答案
            q["answerField"][f'answer{q["id"]}'] = answer
            logger.info(f'{q["title"]} 填写答案为 {answer}')
        cover_rate = (found_answers / total_questions) * 100
        logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")
        # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
        if self.tiku.get_submit_params() == "1":
            questions["pyFlag"] = "1"
        elif cover_rate >= self.tiku.COVER_RATE * 100 or self.rollback_times >= 1:
            questions["pyFlag"] = ""
        else:
            questions["pyFlag"] = "1"
            logger.info(f"章节检测题库覆盖率低于{self.tiku.COVER_RATE * 100:.0f}%，不予提交")
        # 组建提交表单
        if questions["pyFlag"] == "1":
            for q in questions["questions"]:
                questions.update(
                    {
                        f'answer{q["id"]}':
                            q["answerField"][f'answer{q["id"]}'] if q[f'answerSource{q["id"]}'] == "cover" else '',
                        f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                    }
                )
        else:
            for q in questions["questions"]:
                questions.update(
                    {
                        f'answer{q["id"]}': q["answerField"][f'answer{q["id"]}'],
                        f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                    }
                )

        del questions["questions"]

        res = _session.post(
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            data=questions,
            headers={
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": '"Windows"',
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                # "Referer": "https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId=246831735&workAnswerId=52680423&workId=37778125&api=1&knowledgeid=913820156&classId=107515845&oldWorkId=07647c38d8de4c648a9277c5bed7075a&jobid=work-07647c38d8de4c648a9277c5bed7075a&type=&isphone=false&submit=false&enc=1d826aab06d44a1198fc983ed3d243b1&cpi=338350298&mooc2=1&skipHeader=true&originJobId=work-07647c38d8de4c648a9277c5bed7075a",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
            },
        )
        if res.status_code == 200:
            res_json = res.json()
            if res_json["status"]:
                logger.info(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题成功 -> {res_json["msg"]}')
            else:
                logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res_json["msg"]}')
                return StudyResult.ERROR
        else:
            logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res.text}')
            return StudyResult.ERROR
        return StudyResult.SUCCESS

    def _document_progress_log(self, _session, _course, _job, _job_info,
                               _dtoken, _duration, _playingTime,
                               _dtype: str = "Document") -> tuple[bool, int]:
        """
        Report reading/document progress to Chaoxing via the multimedia log API.

        Uses the same infrastructure as video progress logging but with
        dtype='Document' to track reading/document duration.
        """
        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        objectid = _job.get("objectid", "") or _job.get("objectId", "")
        otherinfo = _job.get("otherinfo", "")
        enc = self.get_enc(
            _course["clazzId"], _job["jobid"], objectid,
            _playingTime, _duration, self.get_uid()
        )

        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": objectid,
            "otherInfo": otherinfo,
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": 3,
            "view": "pc",
            "enc": enc,
            "dtype": _dtype,
            "_t": get_timestamp(),
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/{_dtoken}"
        )

        try:
            resp = _session.get(_url, params=params, timeout=10)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return data.get("isPassed", False), 200
                except ValueError:
                    return True, 200
            return False, resp.status_code
        except RequestException:
            return False, 0

    def _visit_chapter(self, _session, _course, _job, _job_info):
        """
        Record chapter browsing activity (equivalent to getTeacherAjax).
        This tells Chaoxing that the user has visited the chapter.
        """
        knowledgeid = _job_info.get("knowledgeid", "")
        if not knowledgeid:
            return

        # Method 1: studentstudyAjax (same as study_emptypage)
        try:
            _session.get(
                "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
                params={
                    "courseId": _course["courseId"],
                    "clazzid": _course["clazzId"],
                    "chapterId": knowledgeid,
                    "cpi": _course["cpi"],
                    "verificationcode": "",
                    "mooc2": 1,
                },
                timeout=5,
            )
        except RequestException:
            pass

        # Method 2: visit/coursestudy (getTeacherAjax equivalent)
        try:
            _session.get(
                "https://mooc1.chaoxing.com/visit/coursestudy",
                params={
                    "courseId": _course["courseId"],
                    "clazzid": _course["clazzId"],
                    "chapterId": knowledgeid,
                    "cpi": _course["cpi"],
                },
                timeout=5,
            )
        except RequestException:
            pass

        logger.trace(f"已记录章节浏览: {knowledgeid}")

    def study_read(self, _course, _job, _job_info, _speed: float = 1.0,
                   stop_event: threading.Event | None = None) -> StudyResult:
        """
        Study a reading task with real reading duration tracking.

        Chaoxing tracks reading duration through:
        1. Chapter visit records (getTeacherAjax equivalent)
        2. Multimedia log API (same as video, with dtype='Document')
        3. The readv2 completion endpoint

        This method simulates all three mechanisms to accumulate reading time.
        """
        _session = SessionManager.get_session()

        # --- Step 0: Determine reading duration ---
        duration = max(0, int(self.read_duration))
        objectid = _job.get("objectid", "")
        dtoken = None

        # Priority: status API > card data > config > 300s default
        if objectid:
            try:
                _info_url = (
                    f"https://mooc1.chaoxing.com/ananas/status/{objectid}"
                    f"?k={self.get_fid()}&flag=normal"
                )
                _resp = _session.get(_info_url, timeout=8)
                if _resp.status_code == 200:
                    data = _resp.json()
                    if data.get("status") == "success":
                        api_duration = int(data.get("duration", 0))
                        if api_duration > 0:
                            duration = api_duration
                            logger.debug(f"从状态API获取阅读时长: {duration}s")
                        dtoken = data.get("dtoken", "")
            except Exception as e:
                logger.debug(f"获取阅读材料信息失败: {e}")

        if _job.get("duration", 0) > 0 and duration <= 0:
            duration = _job["duration"]

        if duration <= 0:
            duration = 300  # 兜底

        effective_duration = max(30, int(duration / _speed))

        logger.info(
            f"开始阅读任务: {_job.get('title', '阅读任务')}, "
            f"原始时长: {duration}s, 模拟时长: {effective_duration}s"
        )

        # --- Step 1: Record chapter visit (getTeacherAjax equivalent) ---
        self._visit_chapter(_session, _course, _job, _job_info)

        # --- Step 2: Open reading page via readv2 ---
        _jobid = _job.get("jobid", "")
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": _jobid,
                "knowledgeid": _job_info.get("knowledgeid", ""),
                "jtoken": _job.get("jtoken", ""),
                "courseid": _course["courseId"],
                "clazzid": _course["clazzId"],
            },
        )
        if _resp.status_code != 200:
            logger.error(f"阅读任务初始化失败 -> [{_resp.status_code}]{_resp.text}")
            return StudyResult.ERROR

        try:
            _resp_json = _resp.json()
            status = str(_resp_json.get("status", "")).lower()
            msg = _resp_json.get("msg", "")
            if status in {"", "true", "success", "1"} or msg == "success":
                logger.info("阅读任务已可完成，继续模拟阅读以累计时长...")
        except ValueError:
            pass

        # --- Step 3: Simulate reading with progress reporting ---
        if effective_duration <= 0:
            logger.info("阅读任务无需模拟时长，直接完成")
            return StudyResult.SUCCESS

        max_retry = 3
        retry_count = 0
        last_report = 0.0
        report_interval = 45  # 每45秒上报一次进度

        pbar = tqdm(
            total=effective_duration,
            desc=_job.get("title", "阅读任务"),
            unit_scale=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
        )

        start_reading = time.time()
        elapsed = 0.0

        while elapsed < effective_duration:
            if stop_event and stop_event.is_set():
                logger.warning(f"收到停止信号，中断阅读任务: {_job.get('title', '阅读任务')}")
                pbar.close()
                return StudyResult.SUCCESS

            current_time = int(time.time() - start_reading)
            play_time = min(current_time, effective_duration)

            # 定期上报阅读进度（使用多媒体日志API或readv2心跳）
            if time.time() - last_report >= report_interval:
                last_report = time.time()

                # 每次网络调用前都检查停止信号
                if stop_event and stop_event.is_set():
                    logger.warning("收到停止信号，中断阅读任务")
                    pbar.close()
                    return StudyResult.SUCCESS

                # 定期记录章节访问
                self._visit_chapter(_session, _course, _job, _job_info)

                if stop_event and stop_event.is_set():
                    logger.warning("收到停止信号，中断阅读任务")
                    pbar.close()
                    return StudyResult.SUCCESS

                if dtoken:
                    passed, code = self._document_progress_log(
                        _session, _course, _job, _job_info,
                        dtoken, effective_duration, play_time,
                    )
                else:
                    passed, code = self._read_progress_log(
                        _session, _course, _job, _job_info,
                    )

                if stop_event and stop_event.is_set():
                    logger.warning("收到停止信号，中断阅读任务")
                    pbar.close()
                    return StudyResult.SUCCESS

                if code == 403:
                    retry_count += 1
                    if retry_count >= max_retry:
                        logger.warning("阅读进度上报连续失败，跳过")
                        break
                    logger.warning("阅读上报403，刷新cookie...")
                    time.sleep(random.uniform(2, 4))
                    SessionManager.update_cookies()
                else:
                    retry_count = 0

            # 更新进度条
            elapsed = time.time() - start_reading
            pbar.n = min(int(elapsed), effective_duration)
            pbar.refresh()
            # 分段等待: 单次阻塞不超过 1s, 保证停止信号能被立即响应
            if stop_event:
                if stop_event.wait(gc.THRESHOLD):
                    logger.warning(f"收到停止信号，中断阅读任务: {_job.get('title', '阅读任务')}")
                    pbar.close()
                    return StudyResult.SUCCESS
            else:
                time.sleep(gc.THRESHOLD)

        pbar.n = effective_duration
        pbar.refresh()
        pbar.close()

        # --- Step 4: Final completion call ---
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": _job.get("jobid", ""),
                "knowledgeid": _job_info.get("knowledgeid", ""),
                "jtoken": _job.get("jtoken", ""),
                "courseid": _course["courseId"],
                "clazzid": _course["clazzId"],
            },
        )
        if _resp.status_code != 200:
            logger.warning(f"阅读任务最终确认失败 -> [{_resp.status_code}]{_resp.text}")
        else:
            try:
                _resp_json = _resp.json()
                msg = _resp_json.get("msg", "")
                status = str(_resp_json.get("status", "")).lower()
                if status not in {"", "true", "success", "1"} and msg != "success":
                    logger.warning(f"阅读任务响应异常 -> {_resp_json}")
            except ValueError:
                logger.warning(f"阅读任务响应解析失败 -> {_resp.text}")

        logger.info(
            f"阅读任务完成: {_job.get('title', '阅读任务')}, "
            f"模拟阅读时长: {int(elapsed)}s"
        )
        return StudyResult.SUCCESS

    def _find_course_objectid(self, _course, _points):
        """
        Search for any valid objectId/dtoken from the course chapters.

        For completed courses where knowledge/cards returns empty attachments,
        we try alternative sources to find objectIds:
        1. Try each chapter's knowledgeId as an objectId in the status API
        2. Try the courseId as an objectId
        3. Try to find objectIds by visiting chapter pages

        Returns (objectId, dtoken) or (None, None) if nothing found.
        """
        _session = SessionManager.get_session()
        _fid = self.get_fid()
        _userid = self.get_uid()

        # Strategy 1: Try each chapter's knowledgeId in the status API
        for point in _points:
            try:
                _url = (
                    f"https://mooc1.chaoxing.com/ananas/status/{point['id']}"
                    f"?k={_fid}&flag=normal"
                )
                _resp = _session.get(_url, timeout=8)
                if _resp.status_code == 200:
                    _data = _resp.json()
                    if _data.get("status") == "success":
                        _dtoken = _data.get("dtoken", "")
                        if _dtoken:
                            logger.info(f"通过章节ID {point['id']} 获取到dtoken")
                            return point["id"], _dtoken
            except Exception:
                continue

        # Strategy 2: Try the courseId as an objectId
        try:
            _url = (
                f"https://mooc1.chaoxing.com/ananas/status/{_course['courseId']}"
                f"?k={_fid}&flag=normal"
            )
            _resp = _session.get(_url, timeout=8)
            if _resp.status_code == 200:
                _data = _resp.json()
                if _data.get("status") == "success":
                    _dtoken = _data.get("dtoken", "")
                    if _dtoken:
                        logger.info(f"通过课程ID获取到dtoken")
                        return _course["courseId"], _dtoken
        except Exception:
            pass

        logger.info("未能从课程中找到可用的objectId/dtoken")
        return None, None

    def _forced_document_log(self, _session, _course, _point,
                              _dtoken, _duration, _playingTime):
        """
        Report document reading progress using forced/available data.

        Similar to _document_progress_log but uses chapter-level data
        instead of task-level job data. This allows reporting reading
        duration even without a proper jobid/objectId from task cards.
        """
        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        objectid = str(_point["id"])  # Use knowledgeId as objectId
        # Construct enc using point id as both jobid and objectId
        # This is a best-effort attempt; Chaoxing may reject invalid enc
        enc = self.get_enc(
            _course["clazzId"], objectid, objectid,
            _playingTime, _duration, self.get_uid()
        )

        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": objectid,
            "otherInfo": "",
            "courseId": _course["courseId"],
            "jobid": objectid,
            "userid": self.get_uid(),
            "isdrag": 3,
            "view": "pc",
            "enc": enc,
            "dtype": "Document",
            "_t": get_timestamp(),
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/{_dtoken}"
        )

        try:
            resp = _session.get(_url, params=params, timeout=10)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return data.get("isPassed", False), 200
                except ValueError:
                    return True, 200
            logger.debug(f"强制文档日志返回 {resp.status_code}: {resp.text[:100]}")
            return False, resp.status_code
        except RequestException:
            return False, 0

    def _forced_readv2_heartbeat(self, _session, _course, _point):
        """
        Send a readv2 heartbeat for a chapter without proper jobid.
        Uses the chapter's knowledgeId as jobid.

        This MAY or MAY NOT contribute to reading duration tracking -
        it depends on whether Chaoxing accepts knowledgeIds as jobids.
        """
        try:
            _resp = _session.get(
                url="https://mooc1.chaoxing.com/ananas/job/readv2",
                params={
                    "jobid": _point["id"],
                    "knowledgeid": _point["id"],
                    "jtoken": "",
                    "courseid": _course["courseId"],
                    "clazzid": _course["clazzId"],
                },
                timeout=10,
            )
            if _resp.status_code == 200:
                try:
                    _data = _resp.json()
                    _status = str(_data.get("status", "")).lower()
                    _msg = _data.get("msg", "")
                    _passed = _status in ("", "true", "success", "1") or _msg == "success"
                    return _passed, 200
                except ValueError:
                    return True, 200
            return False, _resp.status_code
        except RequestException:
            return False, 0

    def study_emptypage(self, _course, point):
        _session = SessionManager.get_session()
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
        )
        if _resp.status_code != 200:
            logger.error(f"空页面任务失败 -> [{_resp.status_code}]{point['title']}")
            return StudyResult.ERROR
        else:
            logger.info(f"空页面任务完成 -> {point['title']}")
            return StudyResult.SUCCESS
