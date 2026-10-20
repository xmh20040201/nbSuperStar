from flask import Blueprint, jsonify, request

from web.services.config_service import load_config, save_config

config_bp = Blueprint("config", __name__)

# 敏感配置项：接口返回时统一脱敏，避免密码 / 密钥 / Token 明文暴露
SENSITIVE_KEYS = {"password", "tokens", "key", "api_key", "secret", "siliconflow_key"}
MASK = "********"


def mask_config(cfg: dict) -> dict:
    """返回脱敏后的配置副本：敏感项打码，用户名部分打码。"""
    result = {}
    for section, items in cfg.items():
        masked = {}
        for key, value in items.items():
            k = key.lower()
            if k in SENSITIVE_KEYS:
                masked[key] = MASK if value else ""
            elif k == "username" and value:
                v = str(value)
                masked[key] = (v[:3] + "****" + v[-4:]) if len(v) > 7 else "****"
            else:
                masked[key] = value
        result[section] = masked
    return result


def strip_masked_values(incoming: dict, existing: dict) -> dict:
    """保存前过滤：凡是仍为脱敏占位符的字段，回退为磁盘上的原值，防止把掩码写进配置。"""
    cleaned = {}
    for section, items in incoming.items():
        sec = dict(items or {})
        old = existing.get(section, {})
        for key, value in list(sec.items()):
            k = key.lower()
            if value == MASK or (k == "username" and isinstance(value, str) and "****" in value):
                if key in old:
                    sec[key] = old[key]
                else:
                    sec.pop(key)
        cleaned[section] = sec
    return cleaned


@config_bp.route("/api/config", methods=["GET"])
def get_config():
    try:
        cfg = load_config()
        return jsonify({"status": "ok", "data": mask_config(cfg)})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@config_bp.route("/api/config", methods=["POST"])
def post_config():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "msg": "无效的请求数据"}), 400
        cleaned = strip_masked_values(data, load_config())
        save_config(cleaned)
        return jsonify({"status": "ok", "msg": "配置已保存"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500
