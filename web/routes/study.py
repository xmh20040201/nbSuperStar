from flask import Blueprint, jsonify, request

from web.services.state import state
from web.services.study_service import start_study, stop_study, force_stop
from web.services.config_service import CONFIG_PATH

study_bp = Blueprint("study", __name__)


@study_bp.route("/api/study/start", methods=["POST"])
def api_start_study():
    """Start the study process with config from config.ini + optional overrides."""
    data = request.get_json(silent=True) or {}

    # 登录校验：未登录时禁止启动刷课
    from web.routes.auth import get_chaoxing
    if get_chaoxing() is None:
        return jsonify({"status": "error", "msg": "尚未登录，请先在「账号登录」页面登录后再启动学习"}), 401

    try:
        # Use main.py's config loader which properly handles type conversions
        from main import load_config_from_file
        common_config, tiku_config, notification_config = load_config_from_file(CONFIG_PATH)

        # Apply optional overrides from request body (already proper types from JSON)
        if "speed" in data:
            common_config["speed"] = min(4.0, max(1.0, float(data["speed"])))
        if "jobs" in data:
            common_config["jobs"] = max(1, int(data["jobs"]))

        ok, msg = start_study(common_config, tiku_config, notification_config)
        if ok:
            return jsonify({"status": "ok", "msg": msg})
        else:
            return jsonify({"status": "error", "msg": msg}), 400
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@study_bp.route("/api/study/stop", methods=["POST"])
def api_stop_study():
    """Signal the study process to stop; force=true 时直接把卡住的状态收敛掉。"""
    data = request.get_json(silent=True) or {}
    if data.get("force"):
        ok, msg = force_stop()
    else:
        ok, msg = stop_study()
    if ok:
        return jsonify({"status": "ok", "msg": msg})
    else:
        return jsonify({"status": "error", "msg": msg}), 400


@study_bp.route("/api/study/status", methods=["GET"])
def api_study_status():
    """Return current study state as JSON."""
    return jsonify({"status": "ok", "data": state.to_dict()})
