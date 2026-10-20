# -*- coding: utf-8 -*-
"""Cookie 持久化的兼容层。

历史 API（``save_cookies(session)`` / ``use_cookies()``）保持不变，
但落盘位置改为**按平台分文件**（``cookies/<platform>.txt``），
实现位于 ``api.platform.cookies``。

之所以保留这个薄封装而不是直接改调用点：``api/base.py``、
``web/routes/auth.py`` 里有多处历史调用，改签名会带来无谓的回归风险。
"""
import requests

from api.config import GlobalConst as gc  # noqa: F401  (兼容旧引用 gc.COOKIES_PATH)
from api.platform.cookies import (  # noqa: F401
    cookie_path_for,
    cookies_to_string,
    load_cookies,
    parse_cookies,
    save_cookies as _save_cookies,
)


def save_cookies(session: requests.Session, platform_key: str = "chaoxing") -> str:
    """把 session 中的 cookie 写入 ``cookies/<platform>.txt``。"""
    return _save_cookies(session.cookies, platform_key)


def use_cookies(platform_key: str = "chaoxing") -> dict:
    """读取指定平台的 cookie；文件不存在时返回空字典。"""
    return load_cookies(platform_key)
