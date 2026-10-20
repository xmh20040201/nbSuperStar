import time
from urllib.parse import quote

from flask import Blueprint, jsonify, request

from api.logger import logger
from web.routes.auth import get_chaoxing
from web.services.config_service import load_config, save_config

courses_bp = Blueprint("courses", __name__)


def _build_reading_url(course, point, enc=""):
    refer_url = (
        "https://mooc1.chaoxing.com/mycourse/studentstudy"
        f"?chapterId={point['id']}"
        f"&courseId={course['courseId']}"
        f"&clazzid={course['clazzId']}"
        f"&cpi={course['cpi']}"
        f"&enc={enc}"
        "&mooc2=1"
        "&hidetype=0"
    )
    return (
        "https://mooc1.chaoxing.com/mycourse/transfer"
        f"?moocId={course['courseId']}"
        f"&clazzid={course['clazzId']}"
        f"&linkTime={int(time.time() * 1000)}"
        f"&ut=s&refer={quote(refer_url, safe='')}"
    )


@courses_bp.route("/api/courses", methods=["GET"])
def list_courses():
    cx = get_chaoxing()
    if cx is None:
        return jsonify({"status": "error", "msg": "请先登录"}), 401

    try:
        courses = cx.get_course_list()
        cfg = load_config()
        selected = cfg.get("common", {}).get("course_list", "")
        selected_ids = [item.strip() for item in selected.split(",") if item.strip()]
        return jsonify({"status": "ok", "courses": courses, "selected_ids": selected_ids})
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
    cx = get_chaoxing()
    if cx is None:
        return jsonify({"status": "error", "msg": "请先登录"}), 401

    try:
        cfg = load_config()
        selected = cfg.get("common", {}).get("course_list", "")
        selected_ids = {item.strip() for item in selected.split(",") if item.strip()}
        if not selected_ids:
            return jsonify({
                "status": "error",
                "msg": "请先在课程列表勾选课程并点击“保存选择”，再生成阅读链接。",
            }), 400

        all_courses = cx.get_course_list()
        courses = [
            course for course in all_courses
            if str(course["courseId"]) in selected_ids
        ]

        items = []
        scanned = 0
        for course in courses:
            course_point = cx.get_course_point(
                course["courseId"], course["clazzId"], course["cpi"]
            )
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
                    "url": _build_reading_url(course, point, course_enc),
                })

        return jsonify({
            "status": "ok",
            "count": len(items),
            "scanned": scanned,
            "items": items,
        })
    except Exception as e:
        logger.exception(f"生成阅读链接失败: {e}")
        return jsonify({"status": "error", "msg": f"生成阅读链接失败: {e}"}), 500
