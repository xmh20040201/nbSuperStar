# -*- coding: utf-8 -*-
"""按平台隔离的 Cookie 持久化。

历史行为：全项目共用一个 ``cookies.txt``。多平台接入后这样做会互相覆盖
（学习通和智慧树的 cookie 会串味），所以改成按平台分文件：

    cookies/<platform_key>.txt

同时**保留向后兼容**：如果 ``cookies/chaoxing.txt`` 不存在但根目录的
``cookies.txt`` 存在（老用户/老仓库），第一次读会把它迁移过来，
老用户的登录态不会因为这次重构丢失。
"""
from __future__ import annotations

import os
import shutil

from api.config import GlobalConst as gc

COOKIE_DIR = "cookies"
# 历史共用 cookie 文件；路径由 GlobalConst 统一管理（默认 "cookies.txt"）
LEGACY_COOKIE_PATH = gc.COOKIES_PATH


def cookie_path_for(platform_key: str) -> str:
    """返回某平台 cookie 文件路径，并确保目录存在。"""
    os.makedirs(COOKIE_DIR, exist_ok=True)
    path = os.path.join(COOKIE_DIR, f"{platform_key}.txt")
    # 一次性迁移历史 cookies.txt 给超星（老仓库唯一存在的平台）
    if platform_key == "chaoxing" and not os.path.exists(path):
        if os.path.exists(LEGACY_COOKIE_PATH):
            try:
                shutil.copyfile(LEGACY_COOKIE_PATH, path)
            except OSError:
                pass
    return path


def parse_cookies(cookie_string: str) -> dict[str, str]:
    """解析 ``k=v; k2=v2`` 形式的 cookie 字符串。"""
    cookies: dict[str, str] = {}
    for item in (cookie_string or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        k = k.strip()
        if k:
            cookies[k] = v.strip()
    return cookies


def cookies_to_string(cookies) -> str:
    """把 dict / RequestsCookieJar / items() 转成 ``k=v; k2=v2``。"""
    if hasattr(cookies, "items"):
        items = cookies.items()
    else:
        items = cookies
    return ";".join(f"{k}={v}" for k, v in items)


def load_cookies(platform_key: str = "chaoxing") -> dict[str, str]:
    """读取平台 cookie；文件不存在时返回空字典（不抛异常，老行为一致）。"""
    path = cookie_path_for(platform_key)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return parse_cookies(f.read())


def save_cookies(cookies, platform_key: str = "chaoxing") -> str:
    """写入平台 cookie，返回落盘路径。"""
    path = cookie_path_for(platform_key)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookies_to_string(cookies))
    return path


__all__ = [
    "COOKIE_DIR",
    "LEGACY_COOKIE_PATH",
    "cookie_path_for",
    "cookies_to_string",
    "load_cookies",
    "parse_cookies",
    "save_cookies",
]
