import os
import json
from flask import Blueprint, jsonify, request

tiku_bp = Blueprint("tiku", __name__)

CACHE_PATH = "cache.json"


def _read_cache():
    """Read the cache.json file and return (questions_dict, error)."""
    if not os.path.exists(CACHE_PATH):
        return {}, None
    try:
        with open(CACHE_PATH, "r", encoding="utf8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}, None
    except Exception as e:
        return {}, str(e)


@tiku_bp.route("/api/tiku/cache", methods=["GET"])
def get_cache():
    """List cached Q&A pairs, optionally filtered by search query."""
    search = request.args.get("search", "").strip().lower()
    data, err = _read_cache()
    if err:
        return jsonify({"status": "error", "msg": f"读取缓存失败: {err}"}), 500

    items = []
    for question, answer in data.items():
        if search and search not in question.lower() and search not in answer.lower():
            continue
        items.append({"question": question, "answer": answer})

    return jsonify({"status": "ok", "count": len(items), "items": items})


@tiku_bp.route("/api/tiku/cache", methods=["DELETE"])
def clear_cache():
    """Clear all cached Q&A pairs."""
    try:
        with open(CACHE_PATH, "w", encoding="utf8") as f:
            json.dump({}, f, ensure_ascii=False, indent=4)
        return jsonify({"status": "ok", "msg": "缓存已清空"})
    except Exception as e:
        return jsonify({"status": "error", "msg": f"清空缓存失败: {e}"}), 500


@tiku_bp.route("/api/tiku/providers", methods=["GET"])
def list_providers():
    """Return available tiku provider names."""
    from api.answer import PROVIDER_REGISTRY
    providers = [
        {
            "name": name,
            "description": _get_provider_description(name),
        }
        for name in PROVIDER_REGISTRY
    ]
    return jsonify({"status": "ok", "providers": providers})


def _get_provider_description(name):
    descs = {
        "TikuYanxi": "言溪题库 (tk.enncy.cn)",
        "TikuGo": "GO题/网课小工具题库 (q.icodef.com)",
        "TikuLike": "LIKE知识库 (datam.site)",
        "TikuAdapter": "自建 tikuAdapter",
        "Xiaomi MiMo": "小米 MiMo 大模型 (OpenAI兼容, 支持 model=auto 自动选模型)",
        "DeepSeek": "DeepSeek 大模型 (OpenAI兼容, 支持 model=auto 自动选模型)",
    }
    return descs.get(name, name)
