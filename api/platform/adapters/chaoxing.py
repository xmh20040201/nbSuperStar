# -*- coding: utf-8 -*-
"""超星/学习通平台 adapter。

实现细节全部在 ``api/base.py`` 的 ``Chaoxing`` 类里（那是本项目最早的、
也是当前最完整的平台实现）。这里做的是三件事：

1. 用 ``@register_platform`` 把 ``Chaoxing`` 登记进平台注册表；
2. 把"契约方法"翻译成 Chaoxing 的历史方法名，让 ``main.py`` / web 层
   只依赖 ``Platform`` 接口；
3. 把原本散落在 main.py / web 层的超星私有逻辑（强制学习时长、
   阅读链接生成）收敛到这里，主流程不再包含任何平台私有代码。

这样超星的行为与重构前**完全一致**，同时其它平台可以照抄这个文件的
结构来实现。
"""
from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import quote

from api.base import Chaoxing, SessionManager
from api.logger import logger
from api.platform.contract import LoginState, StudyResult
from api.platform.registry import register_platform


@register_platform
class ChaoxingPlatform(Chaoxing):
    """学习通平台：把 Chaoxing 的既有能力包装成标准 Platform 契约。"""

    key = "chaoxing"

    # --- 契约方法 --------------------------------------------------------
    def login(self, login_with_cookies: bool = False) -> LoginState:
        """登录，返回统一的 ``LoginState``。

        注意：基类 ``Chaoxing.login`` 历史上返回 ``{"status": bool, "msg": str}``，
        web 层直接读这个字典。为了不破坏任何既有调用点，这里返回
        ``LoginState``——它自带 ``to_dict()`` 与 ``__getitem__`` 兼容层，
        两种写法都能用。
        """
        result = super().login(login_with_cookies=login_with_cookies)
        if isinstance(result, LoginState):
            return result
        if isinstance(result, dict):
            return LoginState(ok=bool(result.get("status")), msg=str(result.get("msg", "")))
        return LoginState.failure(f"未知的登录返回: {result!r}")

    def get_job_list(self, course: dict, point: dict):
        return super().get_job_list(course, point)

    def process_job(self, course: dict, job: dict, job_info: dict,
                    speed: float = 1.0, stop_event: threading.Event | None = None) -> StudyResult:
        return super().process_job(course, job, job_info, speed=speed, stop_event=stop_event)

    # --- 平台私有能力 ----------------------------------------------------
    def get_reading_link(self, course: dict, point: dict, enc: str = "") -> str:
        """生成学习通阅读链接（web 层「阅读链接」功能用）。"""
        refer_url = (
            "https://mooc1.chaoxing.com/mycourse/studentstudy"
            f"?chapterId={point['id']}"
            f"&courseId={course['courseId']}"
            f"&clazzid={course['clazzId']}"
            f"&cpi={course['cpi']}"
            f"&enc={enc}"
            "&mooc2=1"
            "&hidetype=0"
        )
        return (
            "https://mooc1.chaoxing.com/mycourse/transfer"
            f"?moocId={course['courseId']}"
            f"&clazzid={course['clazzId']}"
            f"&linkTime={int(time.time() * 1000)}"
            f"&ut=s&refer={quote(refer_url, safe='')}"
        )

    def force_study_duration(self, course: dict, points: list[dict],
                             elapsed: int = 0, total_duration: int = 0) -> None:
        """超星的"挂机累计学习时长"一轮上报。

        原实现写在 main.py 的循环里，每 20 秒触发一次四路混合上报：
        章节页 / 课程页 / 多媒体日志(dtoken) / readv2 心跳。
        迁移到这里后行为保持一致，主循环每秒调用一次，由本方法内部
        按 20 秒节流。
        """
        session = SessionManager.get_session(self.platform_key)
        interval = 20

        # 节流：不足一个上报周期直接返回（主循环每秒调用）
        if not hasattr(self, "_force_last_report"):
            self._force_last_report = -interval  # 首次立即上报
            self._force_idx = 0
            self._force_dtoken_pairs = None

        now = time.time()
        if elapsed - self._force_last_report < interval:
            return
        self._force_last_report = elapsed

        point = points[self._force_idx % len(points)]
        self._force_idx += 1

        # 每轮重新确认一次 dtoken（课程内容可能变化，缓存失效时自动重找）
        if self._force_dtoken_pairs is None:
            try:
                self._force_objectid, self._force_dtoken = self._find_course_objectid(course, points)
            except Exception:
                self._force_objectid, self._force_dtoken = None, None
            if self._force_dtoken:
                logger.info("找到可用dtoken，将使用多媒体日志API上报阅读时长")
            else:
                logger.info("未找到dtoken，将使用备选方案尝试累计时长")
            self._force_dtoken_pairs = True

        # --- 方法1: 访问章节页面（基础累计）---
        try:
            session.get(
                "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
                params={
                    "courseId": course["courseId"],
                    "clazzid": course["clazzId"],
                    "chapterId": point["id"],
                    "cpi": course["cpi"],
                    "verificationcode": "",
                    "mooc2": 1,
                },
                timeout=5,
            )
        except Exception:
            pass

        # --- 方法2: 访问课程学习页面 ---
        try:
            session.get(
                "https://mooc1.chaoxing.com/visit/coursestudy",
                params={
                    "courseId": course["courseId"],
                    "clazzid": course["clazzId"],
                    "chapterId": point["id"],
                    "cpi": course["cpi"],
                },
                timeout=5,
            )
        except Exception:
            pass

        # --- 方法3: 如果有dtoken，使用多媒体日志API上报阅读时长 ---
        if self._force_dtoken:
            try:
                self._forced_document_log(
                    session, course, point,
                    self._force_dtoken, total_duration, elapsed,
                )
            except Exception:
                pass

        # --- 方法4: 尝试readv2心跳 ---
        if self._force_idx % 2 == 0:
            try:
                self._forced_readv2_heartbeat(session, course, point)
            except Exception:
                pass


__all__ = ["ChaoxingPlatform"]
