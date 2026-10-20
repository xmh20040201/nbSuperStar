# -*- coding: utf-8 -*-
"""多平台抽象层对外出口。

使用方统一从这里导入，不直接依赖具体平台的实现模块：

    from api.platform import PlatformManager, get_platform, JobStatus

    platform = PlatformManager.create("chaoxing", account, config=cfg)
    platform.login()
    courses = platform.get_course_list()
"""
from api.platform.contract import (
    CAPABILITY_ALL,
    ChapterResult,
    COURSE_KEYS,
    JOB_TYPE_TO_CAPABILITY,
    POINT_KEYS,
    JobStatus,
    LoginState,
    Platform,
    PlatformCapability,
    PlatformError,
    PlatformMeta,
    PlatformNotSupported,
    RateLimiter,
    StudyResult,
    normalize_course,
    normalize_point,
)
from api.platform.account import Account
from api.platform.cookies import (
    cookie_path_for,
    load_cookies,
    save_cookies,
)
from api.platform.registry import (
    PLATFORM_REGISTRY,
    PlatformManager,
    ensure_platforms_loaded,
    get_platform,
    list_platforms,
    normalize_platform_key,
    register_platform,
)

__all__ = [
    "Account",
    "CAPABILITY_ALL",
    "COURSE_KEYS",
    "ChapterResult",
    "JOB_TYPE_TO_CAPABILITY",
    "JobStatus",
    "LoginState",
    "PLATFORM_REGISTRY",
    "POINT_KEYS",
    "Platform",
    "PlatformCapability",
    "PlatformError",
    "PlatformManager",
    "PlatformMeta",
    "PlatformNotSupported",
    "RateLimiter",
    "StudyResult",
    "cookie_path_for",
    "ensure_platforms_loaded",
    "get_platform",
    "list_platforms",
    "load_cookies",
    "normalize_course",
    "normalize_platform_key",
    "normalize_point",
    "register_platform",
    "save_cookies",
]
