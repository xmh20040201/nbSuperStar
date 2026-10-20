# -*- coding: utf-8 -*-
"""run_study 端到端流程验证（完全离线，用假平台跑通主流程）。

验证 run_study 已不再写死超星：换一个实现了 Platform 契约的假平台，
整条「登录 -> 拉课程 -> 逐章节派发任务点 -> 汇总结果」都能跑完。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.platform import (
    Platform,
    PlatformCapability,
    PlatformManager,
    PlatformMeta,
    StudyResult,
    normalize_course,
    normalize_point,
    register_platform,
)

CALLS = []


@register_platform
class FlowPlatform(Platform):
    key = "flowtest"
    meta = PlatformMeta(
        key="flowtest",
        name="流程测试平台",
        capabilities=frozenset({
            PlatformCapability.LOGIN_PASSWORD,
            PlatformCapability.COURSE_LIST,
            PlatformCapability.CHAPTER_LIST,
            PlatformCapability.JOB_LIST,
            PlatformCapability.VIDEO,
        }),
    )

    def _create_session(self):
        return None

    def _login_with_password(self):
        from api.platform import LoginState
        CALLS.append("login")
        return LoginState.success()

    def get_course_list(self):
        CALLS.append("courses")
        return [normalize_course({"courseId": 1, "clazzId": "", "cpi": "", "title": "测试课程"}, self.key)]

    def get_course_point(self, course):
        CALLS.append("points")
        return {
            "points": [
                normalize_point({"id": 11, "title": "1.1 视频章"}),
                normalize_point({"id": 12, "title": "1.2 视频章"}),
            ],
            "hasLocked": False,
        }

    def get_job_list(self, course, point):
        CALLS.append(f"jobs:{point['id']}")
        return [{"type": "video", "jobid": f"j{point['id']}", "title": point["title"]}], {"notOpen": False}

    def process_job(self, course, job, job_info, speed=1.0, stop_event=None):
        CALLS.append(f"job:{job['jobid']}")
        return StudyResult.SUCCESS


def main():
    import main as main_mod

    # 屏蔽通知与题库，避免任何外网请求
    class _NoNotify:
        def config_set(self, *a, **k):
            return self

        def get_notification_from_config(self, *a, **k):
            return self

        def init_notification(self, *a, **k):
            return self

        def send(self, *a, **k):
            CALLS.append("notify")
            return None

    main_mod.Notification = _NoNotify

    common = {
        "platform": "flowtest",
        "username": "u",
        "password": "p",
        "use_cookies": False,
        "course_list": ["1"],
        "speed": 1.0,
        "jobs": 2,
        "notopen_action": "continue",
        "read_duration": 0,
        "verbose": False,
    }

    # init_platform 会建 Tiku 实例，这里直接打桩成空题库
    import api.answer as answer_mod
    original_get = answer_mod.Tiku.get_tiku_from_config
    answer_mod.Tiku.get_tiku_from_config = lambda self: self
    try:
        main_mod.run_study(common, {}, {}, stop_event=__import__("threading").Event())
    finally:
        answer_mod.Tiku.get_tiku_from_config = original_get

    print("调用序列:", CALLS)
    assert "login" in CALLS, "未登录"
    assert "courses" in CALLS, "未拉课程列表"
    assert "jobs:11" in CALLS and "jobs:12" in CALLS, "未遍历章节"
    assert "job:j11" in CALLS and "job:j12" in CALLS, "未派发任务点"
    print("\n[PASS] run_study 在非超星平台上完整跑通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
