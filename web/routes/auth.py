from flask import Blueprint, jsonify, request

from api.base import Account, Chaoxing
from api.exceptions import LoginError
from api.logger import logger

from web.services.log_service import log_service
from web.services.config_service import load_config, save_config

auth_bp = Blueprint("auth", __name__)

# In-memory runtime state (not persisted)
_chaoxing_instance = None


def get_chaoxing():
    global _chaoxing_instance
    return _chaoxing_instance


def set_chaoxing(cx):
    global _chaoxing_instance
    _chaoxing_instance = cx


@auth_bp.route("/api/login", methods=["POST"])
def login():
    """Login with username and password."""
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"status": False, "msg": "用户名和密码不能为空"})

    try:
        account = Account(username, password)
        cx = Chaoxing(account=account, tiku=None)
        result = cx.login(login_with_cookies=False)

        if result.get("status"):
            set_chaoxing(cx)
            # 同步账号到 config.ini
            try:
                cfg = load_config()
                if "common" not in cfg:
                    cfg["common"] = {}
                cfg["common"]["username"] = username
                cfg["common"]["password"] = password
                save_config(cfg)
                log_service.write(f"已同步登录信息到配置文件", "INFO")
            except Exception as e:
                logger.warning(f"同步配置失败: {e}")
            log_service.write(f"登录成功: {username}", "SUCCESS")
            return jsonify({"status": True, "msg": "登录成功"})
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
    cookies_str = data.get("cookies", "").strip()

    if not cookies_str:
        return jsonify({"status": False, "msg": "Cookie 不能为空"})

    try:
        # Write cookies to file and use them
        from api.cookies import save_cookies, use_cookies
        from api.base import SessionManager

        # Save provided cookies to file
        cookie_pairs = {}
        for item in cookies_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookie_pairs[k.strip()] = v.strip()

        # We need a session to save cookies
        session = SessionManager.get_session()
        for k, v in cookie_pairs.items():
            session.cookies.set(k, v)
        save_cookies(session)

        account = Account("", "")
        cx = Chaoxing(account=account, tiku=None)
        result = cx.login(login_with_cookies=True)

        if result.get("status"):
            set_chaoxing(cx)
            log_service.write("Cookie 登录成功", "SUCCESS")
            return jsonify({"status": True, "msg": "Cookie 登录成功"})
        else:
            return jsonify({"status": False, "msg": result.get("msg", "Cookie 登录验证失败")})

    except Exception as e:
        logger.exception(f"Cookie 登录异常: {e}")
        return jsonify({"status": False, "msg": f"Cookie 登录异常: {e}"})


@auth_bp.route("/api/login/status", methods=["GET"])
def login_status():
    """Check current login status."""
    cx = get_chaoxing()
    if cx is None:
        return jsonify({"logged_in": False, "username": "", "uid": ""})

    try:
        uid = cx.get_uid()
        name = cx.account.username if cx.account else ""
        return jsonify({"logged_in": True, "username": name, "uid": uid})
    except Exception:
        return jsonify({"logged_in": False, "username": "", "uid": ""})


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    """Clear the Chaoxing instance (logout)."""
    set_chaoxing(None)
    log_service.write("已登出", "INFO")
    return jsonify({"status": "ok", "msg": "已登出"})
