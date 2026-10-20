"""Web 面板访问鉴权。

设计原则：
- **默认不启用**。未设置访问密码时行为与之前完全一致，不会把用户锁在门外。
- 设置环境变量 `SUPERSTAR_PANEL_PASSWORD` 后，所有 /api/* (除登录/状态) 与首页
  都需要先登录（Flask session cookie）。
- 密码使用常量时间比较，避免时序侧信道。
- 同时对所有写操作做 Origin 校验，缓解跨站请求伪造（CSRF）。
"""

import hmac
import os

from flask import Blueprint, jsonify, request, session, redirect

PANEL_PASSWORD_ENV = "SUPERSTAR_PANEL_PASSWORD"

# 无需登录即可访问的路径
PUBLIC_PATHS = {
    "/api/panel/login",
    "/api/panel/auth-status",
}

_SESSION_KEY = "panel_authed"


def get_panel_password() -> str:
    return (os.environ.get(PANEL_PASSWORD_ENV) or "").strip()


def auth_enabled() -> bool:
    return bool(get_panel_password())


def is_authed() -> bool:
    if not auth_enabled():
        return True
    return bool(session.get(_SESSION_KEY))


def _safe_next(path: str) -> str:
    """只允许站内相对路径，防止开放重定向。"""
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def _same_origin_ok() -> bool:
    """校验写请求来自同源，缓解 CSRF。

    无 Origin 头时（部分非浏览器客户端）放行；有则必须与 Host 一致。
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        from urllib.parse import urlparse

        o = urlparse(origin)
        host = request.headers.get("Host", "")
        return o.netloc == host
    except Exception:
        return False


def init_web_auth(app):
    """注册鉴权前置钩子。必须在使用前调用一次。"""

    @app.before_request
    def _guard():
        path = request.path or "/"

        # 静态资源放行
        if path.startswith("/static/"):
            return None

        # 登录相关接口放行
        if path in PUBLIC_PATHS:
            return None

        # 写操作做同源校验（即使未启用密码，也能挡住最基本的跨站提交）
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if not _same_origin_ok():
                return jsonify({"status": "error", "msg": "非法的请求来源"}), 403

        if is_authed():
            return None

        # 未登录：API 返回 401，页面跳转登录
        if path.startswith("/api/"):
            return jsonify({"status": "error", "msg": "未登录", "need_login": True}), 401
        return redirect(f"/panel-login?next={_safe_next(request.full_path or '/')}")

    # 面板登录 / 登出 / 状态
    panel_bp = Blueprint("panel_auth", __name__)

    @panel_bp.route("/api/panel/auth-status", methods=["GET"])
    def auth_status():
        return jsonify(
            {
                "status": "ok",
                "enabled": auth_enabled(),
                "authed": is_authed(),
            }
        )

    @panel_bp.route("/api/panel/login", methods=["POST"])
    def panel_login():
        data = request.get_json(silent=True) or {}
        password = str(data.get("password", ""))
        expected = get_panel_password()

        if not expected:
            # 未启用密码，直接视为通过
            session[_SESSION_KEY] = True
            return jsonify({"status": "ok", "msg": "未启用访问密码"})

        if hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
            session[_SESSION_KEY] = True
            session.permanent = True
            return jsonify({"status": "ok", "msg": "登录成功"})

        return jsonify({"status": "error", "msg": "访问密码错误"}), 401

    @panel_bp.route("/api/panel/logout", methods=["POST"])
    def panel_logout():
        session.pop(_SESSION_KEY, None)
        return jsonify({"status": "ok", "msg": "已退出面板"})

    app.register_blueprint(panel_bp)
