# -*- coding: utf-8 -*-
"""平台契约层：定义所有在线课程平台必须实现的最小接口。

设计约束（重要，改动前必读）
--------------------------------------------------
本项目的内部数据模型沿用了超星的字段命名（``courseId`` / ``clazzId`` /
``cpi``），因为 ``api/decode.py``、``api/base.py`` 与 ``main.py`` 的
任务调度全都直接读这些键，运行时改名的成本远高于让 adapter 做一层翻译。

因此本层的约定是：

* **规范化模型 = 现有超星字段名**，它是所有平台互通的"普通话"；
* **平台私有字段**（如智慧树的 ``recruitId``）允许直接挂在
  ``course`` / ``point`` / ``job`` 字典上，超星流程会忽略它们，
  但对应平台的 adapter 可以读；
* 新增平台时，在 ``api/platform/adapters/`` 下实现一个子类并用
  ``@register_platform`` 注册，**不需要改 main.py 的调度逻辑**。

这样 refactor 前后的超星行为完全一致（零行为变更），同时把"多平台"
的能力入口打开。
"""
from __future__ import annotations

import abc
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

# --------------------------------------------------------------------------
# 结果枚举（从 api/base.py 迁入，api/base.py 继续对外 re-export 以保持兼容）
# --------------------------------------------------------------------------


class StudyResult(Enum):
    """单个任务点（视频/音频/文档/阅读/作业/直播）的处理结果。"""

    SUCCESS = 0
    FORBIDDEN = 1  # 403，属于确定性失败，重试无意义
    ERROR = 2
    TIMEOUT = 3

    def is_success(self):
        return self == StudyResult.SUCCESS

    def is_failure(self):
        return self != StudyResult.SUCCESS


class ChapterResult(Enum):
    """一个章节（章节点）的处理结果。"""

    PENDING = 0
    SUCCESS = 1
    ERROR = 2
    NOT_OPEN = 3
    FORBIDDEN = 4
    STOPPED = 5


class JobStatus(IntEnum):
    """任务点状态，供各平台 adapter 归一化使用。"""

    PENDING = 0
    PASSED = 1
    SKIPPED = 2


# --------------------------------------------------------------------------
# 平台能力（capability）与规范化键
# --------------------------------------------------------------------------


class PlatformCapability(str, Enum):
    """平台支持哪些能力。

    ``main.py`` 在派发任务点前会先用 ``Platform.supports()`` 判断，
    平台不支持的能力不会报错，而是记录日志后跳过该任务点——
    这样新增平台可以"先支持视频，再慢慢补作业/直播"。
    """

    LOGIN_PASSWORD = "login_password"
    LOGIN_COOKIE = "login_cookie"
    COURSE_LIST = "course_list"
    CHAPTER_LIST = "chapter_list"
    JOB_LIST = "job_list"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    READ = "read"
    WORK = "work"
    LIVE = "live"
    SIGN = "sign"          # 自动签到
    READING_LINK = "reading_link"  # 生成可点击的阅读链接


CAPABILITY_ALL = frozenset(PlatformCapability)


#: 任务点 ``job["type"]`` -> 所需能力。超星沿用其原始命名
#: （``workid`` 表示章节检测，``work`` 表示普通作业）。
JOB_TYPE_TO_CAPABILITY: dict[str, PlatformCapability] = {
    "video": PlatformCapability.VIDEO,
    "audio": PlatformCapability.AUDIO,
    "document": PlatformCapability.DOCUMENT,
    "read": PlatformCapability.READ,
    "work": PlatformCapability.WORK,
    "workid": PlatformCapability.WORK,
    "live": PlatformCapability.LIVE,
}


#: 规范化课程字典中由 adapter 保证一定存在的键。
COURSE_KEYS: tuple[str, ...] = (
    "platform",   # 产出该课程的平台 key，例如 "chaoxing"
    "courseId",   # 平台内课程唯一 ID（字符串）
    "clazzId",    # 班级/开课 ID，无此概念的平台填 ""
    "cpi",        # 超星专用的 cpi 参数，无此概念的平台填 ""
    "title",
    "teacher",
)

#: 规范化章节字典中由 adapter 保证一定存在的键。
POINT_KEYS: tuple[str, ...] = (
    "id",            # 章节 ID（字符串）
    "title",
    "has_finished",  # bool，是否已完成
    "need_unlock",   # bool，是否需要先完成前置章节
)


def normalize_course(course: dict[str, Any], platform_key: str) -> dict[str, Any]:
    """补齐课程字典的规范化字段，并统一转成字符串。

    直接就地修改并返回入参字典：现有代码（web 层、filter_courses）持有的是
    同一个对象的引用，返回新对象会破坏这种隐式约定。
    """
    course["platform"] = platform_key
    for key in ("courseId", "clazzId", "cpi"):
        course[key] = "" if course.get(key) is None else str(course.get(key))
    course.setdefault("title", "")
    course.setdefault("teacher", "")
    return course


def normalize_point(point: dict[str, Any]) -> dict[str, Any]:
    """补齐章节字典的规范化字段。"""
    point["id"] = "" if point.get("id") is None else str(point.get("id"))
    point.setdefault("title", "")
    point["has_finished"] = bool(point.get("has_finished", False))
    point["need_unlock"] = bool(point.get("need_unlock", False))
    return point


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class PlatformError(Exception):
    """平台层可预期的错误（登录失败、平台不支持某能力等）。"""


class PlatformNotSupported(PlatformError):
    """平台未实现某个能力/方法。"""


class LoginError(PlatformError):
    """登录失败。"""


# --------------------------------------------------------------------------
# 限速器（从 api/base.py 迁入）
# --------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval
        # 全局冷却截止时间：一旦触发风控(403)，所有线程共享同一冷却窗口，
        # 避免并发线程继续撞击风控把封禁范围扩大
        self._cooldown_until = 0.0

    def cooldown(self, seconds: float):
        """登记一个全局冷却窗口，期间所有请求都会被阻塞。"""
        with self.lock:
            until = time.time() + max(0.0, float(seconds))
            if until > self._cooldown_until:
                self._cooldown_until = until

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        import random

        with self.lock:
            now = time.time()
            # 冷却窗口优先：等待到冷却结束（锁外 sleep，避免阻塞其他线程登记）
            cool_wait = max(self._cooldown_until - now, 0)
            base_wait = max(self.last_call + self.call_interval - now, 0)
            extra_wait = random.uniform(random_min, random_max) if random_time else 0
            call_wait = max(base_wait, cool_wait) + extra_wait
            self.last_call = now + call_wait

        if call_wait > 0:
            time.sleep(call_wait)


# --------------------------------------------------------------------------
# 登录态
# --------------------------------------------------------------------------


@dataclass
class LoginState:
    """``login()`` 的统一返回值。

    同时提供对象式（``.ok`` / ``.msg``）与字典式
    （``["status"]`` / ``.get("msg")``）两种访问方式——
    后者是为了兼容 web 层历史上 ``result["status"]`` 的写法，
    让 adapter 返回 ``LoginState`` 时无需改动任何调用点。
    """

    ok: bool
    msg: str = ""

    @classmethod
    def success(cls, msg: str = "登录成功") -> "LoginState":
        return cls(True, msg)

    @classmethod
    def failure(cls, msg: str) -> "LoginState":
        return cls(False, msg)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.ok, "msg": self.msg}

    # -- 字典兼容层 ------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        if key == "status":
            return self.ok
        if key == "msg":
            return self.msg
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        return key in ("status", "msg")

    def raise_for_failure(self):
        if not self.ok:
            raise LoginError(self.msg)


# --------------------------------------------------------------------------
# 平台基类
# --------------------------------------------------------------------------


@dataclass
class PlatformMeta:
    """注册表里登记的平台元信息，供 CLI / Web / 文档自动生成列表。"""

    key: str
    name: str
    home: str = ""
    account_hint: str = "账号"
    password_hint: str = "密码"
    cookie_hint: str = "Cookie 字符串（key=value; key2=value2）"
    config_fields: tuple[dict[str, str], ...] = ()
    capabilities: frozenset[PlatformCapability] = CAPABILITY_ALL
    notes: str = ""


class Platform(abc.ABC):
    """所有平台 adapter 的基类。

    子类**必须**声明两个类属性::

        key  = "chaoxing"     # 配置里的 platform 值，小写
        meta = PlatformMeta(...)

    并实现 ``_login_with_password`` / ``get_course_list`` /
    ``get_course_point`` / ``get_job_list`` / ``process_job`` 这五个核心方法。
    其余方法有默认实现，平台不支持时抛 ``PlatformNotSupported``。
    """

    key: str = ""
    meta: PlatformMeta

    def __init__(
        self,
        account: "Account | None" = None,
        tiku: Any = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        self.account = account
        self.tiku = tiku
        self.config = config or {}
        self.kwargs = kwargs
        # 各平台请求间隔不同，子类可覆盖
        self.rate_limiter = RateLimiter(kwargs.pop("rate_interval", 0.3))
        self.session = self._create_session()

    # -- 会话与元信息 ---------------------------------------------------
    def _create_session(self):
        """创建该平台专用的 requests.Session（按平台隔离 cookie）。"""
        raise PlatformNotSupported(f"{self.key} 未实现 _create_session")

    @property
    def display_name(self) -> str:
        return self.meta.name if getattr(self, "meta", None) else self.key

    def supports(self, capability: PlatformCapability) -> bool:
        return capability in self.meta.capabilities

    def describe(self) -> dict[str, Any]:
        """给 Web 前端用的平台描述（不含任何敏感信息）。"""
        caps = self.meta.capabilities
        return {
            "key": self.meta.key,
            "name": self.meta.name,
            "home": self.meta.home,
            "account_hint": self.meta.account_hint,
            "password_hint": self.meta.password_hint,
            "cookie_hint": self.meta.cookie_hint,
            "config_fields": list(self.meta.config_fields),
            "capabilities": sorted(c.value for c in caps),
            "supports_cookie_login": PlatformCapability.LOGIN_COOKIE in caps,
            "supports_password_login": PlatformCapability.LOGIN_PASSWORD in caps,
            "notes": self.meta.notes,
        }

    # -- 登录 -----------------------------------------------------------
    def login(self, login_with_cookies: bool = False) -> LoginState:
        """统一登录入口，返回 ``LoginState``。"""
        if login_with_cookies:
            if not self.supports(PlatformCapability.LOGIN_COOKIE):
                return LoginState.failure(f"{self.display_name} 不支持 Cookie 登录")
            return self._login_with_cookies()
        if not self.supports(PlatformCapability.LOGIN_PASSWORD):
            return LoginState.failure(f"{self.display_name} 不支持账号密码登录")
        return self._login_with_password()

    @abc.abstractmethod
    def _login_with_password(self) -> LoginState:
        ...

    def _login_with_cookies(self) -> LoginState:
        raise PlatformNotSupported(f"{self.key} 不支持 Cookie 登录")

    def validate_session(self) -> bool:
        """校验当前会话是否仍然有效（Cookie 登录失败时用于回退）。"""
        raise PlatformNotSupported(f"{self.key} 未实现 validate_session")

    # -- 身份 -----------------------------------------------------------
    def get_uid(self) -> str:
        raise PlatformNotSupported(f"{self.key} 未实现 get_uid")

    # -- 课程 / 章节 / 任务点 --------------------------------------------
    @abc.abstractmethod
    def get_course_list(self) -> list[dict[str, Any]]:
        """返回规范化课程字典列表（至少含 COURSE_KEYS）。"""

    @abc.abstractmethod
    def get_course_point(self, course: dict[str, Any]) -> dict[str, Any]:
        """返回章节列表。

        ``{"points": [...], "hasLocked": bool, "enc": str}``
        """

    @abc.abstractmethod
    def get_job_list(
        self, course: dict[str, Any], point: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """返回 ``(任务点列表, 章节元信息)``。

        任务点字典必须带 ``type`` 键，取值见 ``JOB_TYPE_TO_CAPABILITY``。
        章节元信息里 ``notOpen=True`` 表示章节未开放。
        """

    # -- 任务点执行 ------------------------------------------------------
    @abc.abstractmethod
    def process_job(
        self,
        course: dict[str, Any],
        job: dict[str, Any],
        job_info: dict[str, Any],
        speed: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> StudyResult:
        """执行单个任务点。

        由各平台 adapter 按 ``job["type"]`` 自行分派，这样可以自由
        实现"一条链路上需要混合多个请求"的场景。
        """

    # -- 可选能力 --------------------------------------------------------
    def get_reading_link(
        self, course: dict[str, Any], point: dict[str, Any], enc: str = ""
    ) -> str:
        raise PlatformNotSupported(f"{self.key} 不支持生成阅读链接")

    def force_study_duration(
        self,
        course: dict[str, Any],
        points: list[dict[str, Any]],
        elapsed: int = 0,
        total_duration: int = 0,
    ) -> None:
        """"挂机累计学习时长"的一次心跳（``read_duration > 0`` 时按秒调用）。

        平台各自的上报方式差异极大（超星是多接口混合上报），因此这个钩子
        由 adapter 自行实现；未实现时主流程会记录日志并跳过该模式。
        每次调用应完成**一轮**上报，主循环控制频率。
        """
        raise PlatformNotSupported(f"{self.key} 不支持强制学习时长模式")

    def auto_sign(self, course: dict[str, Any]) -> Any:
        raise PlatformNotSupported(f"{self.key} 不支持自动签到")


__all__ = [
    "CAPABILITY_ALL",
    "COURSE_KEYS",
    "ChapterResult",
    "JOB_TYPE_TO_CAPABILITY",
    "JobStatus",
    "LoginError",
    "LoginState",
    "POINT_KEYS",
    "Platform",
    "PlatformCapability",
    "PlatformError",
    "PlatformMeta",
    "PlatformNotSupported",
    "RateLimiter",
    "StudyResult",
    "normalize_course",
    "normalize_point",
]
