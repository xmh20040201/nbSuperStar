# -*- coding: utf-8 -*-
"""平台注册表。

新增平台的完整流程只有三步：:

    1. 在 api/platform/adapters/ 下写一个 ``Platform`` 子类
    2. 类上加 ``@register_platform`` 装饰器
    3. 在 api/platform/adapters/__init__.py 里 import 它（触发注册）

main.py / web 层不需要任何修改即可看到新平台。
"""
from __future__ import annotations

from typing import Any, Callable, Type

from api.platform.contract import (
    Platform,
    PlatformError,
    PlatformMeta,
    PlatformNotSupported,
)

#: platform key -> Platform 子类
PLATFORM_REGISTRY: dict[str, Type[Platform]] = {}

_adapters_loaded = False


def ensure_platforms_loaded() -> None:
    """惰性导入 ``api.platform.adapters`` 以触发 ``@register_platform``。

    必须惰性执行：``api/base.py``（超星实现）本身要 import
    ``api/platform`` 里的契约类型，若在包初始化时就反过来 import
    adapters，会形成 ``api.base -> api.cookies -> api.platform ->
    api.platform.adapters -> api.base`` 的循环导入。
    """
    global _adapters_loaded
    if _adapters_loaded:
        return
    _adapters_loaded = True
    try:
        import api.platform.adapters  # noqa: F401
    except ImportError:  # pragma: no cover - 仅用于容错，不应发生
        _adapters_loaded = False
        raise

DEFAULT_PLATFORM = "chaoxing"

#: 常见的同义写法，统一收敛到注册表里的 key
_PLATFORM_ALIASES: dict[str, str] = {
    "": DEFAULT_PLATFORM,
    "cx": "chaoxing",
    "chaoxing": "chaoxing",
    "xuexitong": "chaoxing",
    "学习通": "chaoxing",
    "超星": "chaoxing",
    "超星学习通": "chaoxing",
    "zhihuishu": "zhihuishu",
    "zhidao": "zhihuishu",
    "智慧树": "zhihuishu",
    "知到": "zhihuishu",
}


def register_platform(cls: Type[Platform]) -> Type[Platform]:
    """把 Platform 子类登记进注册表（可用作装饰器）。"""
    key = getattr(cls, "key", "")
    if not key:
        raise PlatformError(f"{cls.__name__} 缺少 key 类属性")
    if not isinstance(getattr(cls, "meta", None), PlatformMeta):
        raise PlatformError(f"{cls.__name__} 缺少 meta = PlatformMeta(...)")
    if cls.meta.key != key:
        raise PlatformError(
            f"{cls.__name__} 的 key({key}) 与 meta.key({cls.meta.key}) 不一致"
        )
    PLATFORM_REGISTRY[key.lower()] = cls
    return cls


def normalize_platform_key(platform_key: str | None) -> str:
    """把用户/配置里写的平台名收敛成注册表中的 key；未知平台原样返回小写。"""
    raw = (platform_key or "").strip()
    lowered = raw.lower()
    if lowered in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[lowered]
    if raw in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[raw]
    if lowered in PLATFORM_REGISTRY:
        return lowered
    return lowered or DEFAULT_PLATFORM


def get_platform(platform_key: str | None) -> Type[Platform]:
    """按 key 取 Platform 子类，未注册时抛 PlatformNotSupported。"""
    ensure_platforms_loaded()
    key = normalize_platform_key(platform_key)
    cls = PLATFORM_REGISTRY.get(key)
    if cls is None:
        available = ", ".join(sorted(PLATFORM_REGISTRY)) or "(无)"
        raise PlatformNotSupported(
            f"未知平台: {platform_key!r}，当前已支持: {available}"
        )
    return cls


def list_platforms() -> list[dict[str, Any]]:
    """返回所有已注册平台的描述信息（供 CLI / Web 使用）。"""
    ensure_platforms_loaded()
    result = []
    for key in sorted(PLATFORM_REGISTRY):
        cls = PLATFORM_REGISTRY[key]
        meta: PlatformMeta = cls.meta
        result.append(
            {
                "key": key,
                "name": meta.name,
                "home": meta.home,
                "account_hint": meta.account_hint,
                "password_hint": meta.password_hint,
                "cookie_hint": meta.cookie_hint,
                "config_fields": list(meta.config_fields),
                "capabilities": sorted(c.value for c in meta.capabilities),
                "supports_cookie_login": any(
                    c.value == "login_cookie" for c in meta.capabilities
                ),
                "supports_password_login": any(
                    c.value == "login_password" for c in meta.capabilities
                ),
                "notes": meta.notes,
            }
        )
    return result


class PlatformManager:
    """平台实例的工厂 + 单例持有者。

    单例只保存"当前活动平台"的实例：Web 面板同一时刻只服务一个账号，
    但**不同平台各有各的实例缓存**，切换平台时不会互相踩。
    """

    _instances: dict[str, Platform] = {}
    _active_key: str | None = None

    @classmethod
    def create(
        cls,
        platform_key: str | None,
        account: Any = None,
        tiku: Any = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Platform:
        """构造一个新实例（不接管单例）。"""
        cls_ = get_platform(platform_key)
        key = normalize_platform_key(platform_key)
        # adapter 通过 config["_platform_key"] 获知自己的身份（用于 cookie 路径）
        cfg = dict(config or {})
        cfg.setdefault("_platform_key", key)
        return cls_(account=account, tiku=tiku, config=cfg, **kwargs)

    @classmethod
    def set_active(cls, target: Any) -> None:
        """登记当前活动的平台实例。

        兼容旧调用 ``set_active(Chaoxing 实例)`` 与
        ``set_active(("chaoxing", 实例))`` 两种写法。
        """
        instance = target
        key = getattr(instance, "key", None) if instance is not None else None
        if instance is None:
            cls._active_key = None
            return
        key = normalize_platform_key(key)
        # 同一平台只保留最新实例（重新登录会替换旧的）
        for old_key in list(cls._instances):
            if old_key == key:
                del cls._instances[old_key]
        cls._instances[key] = instance
        cls._active_key = key

    @classmethod
    def get_active(cls) -> Platform | None:
        if cls._active_key is None:
            return None
        return cls._instances.get(cls._active_key)

    @classmethod
    def get_active_key(cls) -> str | None:
        return cls._active_key

    @classmethod
    def clear(cls) -> None:
        cls._instances.clear()
        cls._active_key = None


__all__ = [
    "DEFAULT_PLATFORM",
    "PLATFORM_REGISTRY",
    "PlatformManager",
    "get_platform",
    "list_platforms",
    "normalize_platform_key",
    "register_platform",
]
