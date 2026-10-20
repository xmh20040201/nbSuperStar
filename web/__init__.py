from flask import Flask, send_from_directory
import os

from .routes.config import config_bp
from .routes.auth import auth_bp
from .routes.courses import courses_bp
from .routes.study import study_bp
from .routes.logs import logs_bp
from .routes.tiku import tiku_bp

from .services.log_service import init_log_capture
from .auth import init_web_auth


def create_app():
    app = Flask(__name__, static_folder=None)
    # 允许通过环境变量覆盖密钥，避免把固定密钥提交到仓库
    app.secret_key = os.environ.get("SUPERSTAR_SECRET_KEY", "superstar-web-panel-secret-key")
    app.permanent_session_lifetime = 60 * 60 * 12  # 面板登录有效期 12 小时

    # 直接托管 templates/index.html 与 static 目录（保持轻量，无模板继承）
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    @app.route("/")
    def index():
        return send_from_directory(template_dir, "index.html")

    @app.route("/panel-login")
    def panel_login_page():
        return send_from_directory(template_dir, "panel_login.html")

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(static_dir, filename)

    # Register blueprints
    app.register_blueprint(config_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(courses_bp)
    app.register_blueprint(study_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(tiku_bp)

    # 面板访问鉴权（未设置 SUPERSTAR_PANEL_PASSWORD 时不改变原有行为）
    init_web_auth(app)

    # Initialize log capture
    init_log_capture()

    return app
