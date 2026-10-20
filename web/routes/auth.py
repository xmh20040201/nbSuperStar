from flask import Blueprint, jsonify, request

from api.base import Account, Chaoxing
from api.exceptions import LoginError
from api.logger import logger
from api.platform import (
    PLATFORM_REGISTRY,
    PlatformManager,
    get_platform,
    list_platforms,
    normalize_platform_key,
)
from api.platform.cookies import parse_cookies

from web.services.log_service import log_service
from web.services.config_service import load_config, save_config

auth_bp = Blueprint("auth", __name__)


# --------------------------------------------------------------------------
# 活动平台实例的持有者
# --------------------------------------------------------------------------
# 历史代码直接持有 Chaoxing 单例；多平台后改为把实例交给 PlatformManager，
# 但保留 get_chaoxing()/set_chaoxing() 两个函数名，web 层其它模块零改动。
def get_chaoxing():
    """返回当前活动平台实例（历史命名，实际可能是任意平台）。"""
    return PlatformManager.get_active()


def set_chaoxing(instance):
    """登记当前活动平台实例（历史命名）。"""
    PlatformManager.set_active(instance)


def get_active_platform():
    """语义更清晰的别名。"""
    return PlatformManager.get_active()


def _current_platform_key(default: str = "chaoxing") -> str:
    """当前平台 key：优先取内存中的活动实例，其次读配置。"""
    active = PlatformManager.get_active_key()
    if active:
        return active
    cfg = load_config()
    return normalize_platform_key(cfg.get("common", {}).get("platform", default))


def _persist_config(**updates):
    """把少量 common 段字段写回 config.ini。"""
    cfg = load_config()
    common = cfg.setdefault("common", {})
    common.update({k: v for k, v in updates.items() if v is not None})
    save_config(cfg)


# --------------------------------------------------------------------------
# 平台元信息
# --------------------------------------------------------------------------
@auth_bp.route("/api/platforms", methods=["GET"])
def api_platforms():
    """返回所有已支持平台的能力与表单字段，供前端动态渲染登录界面。"""
    cfg = load_config()
    configured = normalize_platform_key(cfg.get("common", {}).get("platform", "chaoxing"))
    active = PlatformManager.get_active_key()
    return jsonify({
        "status": "ok",
        "platforms": list_platforms(),
        "selected": active or configured,
        "logged_in_platform": active,
    })


@auth_bp.route("/api/platform/select", methods=["POST"])
def api_select_platform():
    """切换当前平台（只写配置，不影响已登录的实例）。"""
    data = request.get_json(force=True) or {}
    key = normalize_platform_key(data.get("platform"))
    if key not in PLATFORM_REGISTRY:
        try:
            get_platform(key)  # 触发惰性注册并用统一的错误信息兜底
        except Exception as e:
            return jsonify({"status": False, "msg": str(e)}), 400

    try:
        _persist_config(platform=key)
        meta = PLATFORM_REGISTRY[key].meta
        log_service.write(f"已切换平台: {meta.name}", "INFO")
        return jsonify({"status": True, "msg": f"已切换到 {meta.name}", "platform": key})
    except Exception as e:
        logger.exception(f"切换平台失败: {e}")
        return jsonify({"status": False, "msg": f"切换平台失败: {e}"}), 500


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------
@auth_bp.route("/api/login", methods=["POST"])
def login():
    """Login with username and password."""
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    platform_key = normalize_platform_key(data.get("platform") or _current_platform_key())

    if not username or not password:
        return jsonify({"status": False, "msg": "用户名和密码不能为空"})

    try:
        platform_cls = get_platform(platform_key)

        # 部分平台（如智慧树）需要额外参数，通过请求体里的 extras 传入
        extras = data.get("extras") or {}
        account = Account(username, password)
        instance = PlatformManager.create(
            platform_key, account=account, tiku=None, config={"platform": platform_key},
            **({"extras": extras} if extras else {}),
        )
        result = instance.login(login_with_cookies=False)

        if result.get("status"):
            set_chaoxing(instance)
            # 同步账号到 config.ini
            try:
                _persist_config(username=username, password=password, platform=platform_key)
                log_service.write("已同步登录信息到配置文件", "INFO")
            except Exception as e:
                logger.warning(f"同步配置失败: {e}")
            log_service.write(f"登录成功: {username} @ {platform_cls.meta.name}", "SUCCESS")
            return jsonify({"status": True, "msg": "登录成功", "platform": platform_key})
        else:
            return jsonify({"status": False, "msg": result.get("msg", "登录失败")})

    except LoginError as e:
        return jsonify({"status": False, "msg": str(e)})
    except Exception as e:
        logger.exception(f"登录异常: {e}")
        return jsonify({"status": False, "msg": f"登录异常: {e}"})


@auth_bp.route("/api/login/cookies", methods=["POST"])
def login_with_cookies():
    """Login using provided cookie string."""
    data = request.get_json(force=True)
    cookies_str = (data.get("cookies") or "").strip()
    platform_key = normalize_platform_key(data.get("platform") or _current_platform_key())

    if not cookies_str:
        return jsonify({"status": False, "msg": "Cookie 不能为空"})

    try:
        from api.platform.cookies import save_cookies as _save

        cookie_pairs = parse_cookies(cookies_str)

        # 先落盘再让平台读取：不同平台的 cookie 分别存储，互不覆盖
        instance = PlatformManager.create(
            platform_key, account=Account("", ""), tiku=None,
            config={"platform": platform_key},
        )
        _save(cookie_pairs, platform_key)
        # 刷新平台会话（SessionManager 按平台隔离）
        from api.base import SessionManager
        SessionManager.update_cookies(platform_key)

        result = instance.login(login_with_cookies=True)

        if result.get("status"):
            set_chaoxing(instance)
            try:
                _persist_config(platform=platform_key)
            except Exception as e:
                logger.warning(f"同步平台配置失败: {e}")
            log_service.write("Cookie 登录成功", "SUCCESS")
            return jsonify({"status": True, "msg": "Cookie 登录成功", "platform": platform_key})
        else:
            return jsonify({"status": False, "msg": result.get("msg", "Cookie 登录验证失败")})

    except Exception as e:
        logger.exception(f"Cookie 登录异常: {e}")
        return jsonify({"status": False, "msg": f"Cookie 登录异常: {e}"})


@auth_bp.route("/api/login/status", methods=["GET"])
def login_status():
    """Check current login status."""
    instance = get_chaoxing()
    if instance is None:
        return jsonify({
            "logged_in": False, "username": "", "uid": "",
            "platform": _current_platform_key(),
            "platform_name": "",
        })

    try:
        uid = instance.get_uid()
    except Exception:
        uid = ""
    name = instance.account.username if getattr(instance, "account", None) else ""
    platform_key = getattr(instance, "platform_key", None) or getattr(instance, "key", "")
    return jsonify({
        "logged_in": True,
        "username": name,
        "uid": uid,
        "platform": platform_key,
        "platform_name": getattr(getattr(instance, "meta", None), "name", platform_key),
    })


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    """Clear the platform instance (logout)."""
    set_chaoxing(None)
    log_service.write("已登出", "INFO")
    return jsonify({"status": "ok", "msg": "已登出"})
