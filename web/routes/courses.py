import time

from flask import Blueprint, jsonify, request

from api.logger import logger
from api.platform import PlatformCapability, PlatformNotSupported
from web.routes.auth import get_chaoxing
from web.services.config_service import load_config, save_config

courses_bp = Blueprint("courses", __name__)


@courses_bp.route("/api/courses", methods=["GET"])
def list_courses():
    platform = get_chaoxing()
    if platform is None:
        return jsonify({"status": "error", "msg": "请先登录"}), 401

    try:
        courses = platform.get_course_list()
        cfg = load_config()
        selected = cfg.get("common", {}).get("course_list", "")
        selected_ids = [item.strip() for item in selected.split(",") if item.strip()]
        return jsonify({
            "status": "ok",
            "courses": courses,
            "selected_ids": selected_ids,
            "platform": getattr(platform, "platform_key", getattr(platform, "key", "")),
        })
    except Exception as e:
        logger.exception(f"获取课程列表失败: {e}")
        return jsonify({"status": "error", "msg": f"获取课程列表失败: {e}"}), 500


@courses_bp.route("/api/courses/select", methods=["POST"])
def select_courses():
    data = request.get_json(force=True)
    course_ids = data.get("course_ids", [])

    if not isinstance(course_ids, list):
        return jsonify({"status": "error", "msg": "course_ids 必须是数组"}), 400

    try:
        cfg = load_config()
        if "common" not in cfg:
            cfg["common"] = {}
        cfg["common"]["course_list"] = ",".join(str(cid) for cid in course_ids)
        save_config(cfg)
        return jsonify({"status": "ok", "msg": f"已保存 {len(course_ids)} 个课程 ID"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@courses_bp.route("/api/reading/links", methods=["GET"])
def reading_links():
    platform = get_chaoxing()
    if platform is None:
        return jsonify({"status": "error", "msg": "请先登录"}), 401

    # 阅读链接是平台私有能力：未实现时给出明确提示而不是 500
    if not platform.supports(PlatformCapability.READING_LINK):
        return jsonify({
            "status": "error",
            "msg": f"当前平台（{platform.display_name}）暂不支持生成阅读链接",
        }), 400

    try:
        cfg = load_config()
        selected = cfg.get("common", {}).get("course_list", "")
        selected_ids = {item.strip() for item in selected.split(",") if item.strip()}
        if not selected_ids:
            return jsonify({
                "status": "error",
                "msg": "请先在课程列表勾选课程并点击“保存选择”，再生成阅读链接。",
            }), 400

        all_courses = platform.get_course_list()
        courses = [
            course for course in all_courses
            if str(course["courseId"]) in selected_ids
        ]

        items = []
        scanned = 0
        for course in courses:
            course_point = platform.get_course_point(course)
            course_enc = course_point.get("enc", "")
            for point in course_point.get("points", []):
                scanned += 1
                title = point.get("title", "")
                if "阅读" not in title:
                    continue
                items.append({
                    "course_id": course["courseId"],
                    "course_title": course["title"],
                    "chapter_id": point["id"],
                    "chapter_title": title,
                    "reading_title": title,
                    "finished": bool(point.get("has_finished")),
                    # 链接构造由平台 adapter 负责，web 层不再拼超星 URL
                    "url": platform.get_reading_link(course, point, course_enc),
                })

        return jsonify({
            "status": "ok",
            "count": len(items),
            "scanned": scanned,
            "items": items,
        })
    except PlatformNotSupported as e:
        return jsonify({"status": "error", "msg": str(e)}), 400
    except Exception as e:
        logger.exception(f"生成阅读链接失败: {e}")
        return jsonify({"status": "error", "msg": f"生成阅读链接失败: {e}"}), 500
