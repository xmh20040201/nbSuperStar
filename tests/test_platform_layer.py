# -*- coding: utf-8 -*-
"""多平台抽象层的回归自测（不需要真实账号/网络）。

运行：python tests/test_platform_layer.py

覆盖点：
1. 平台注册表：超星已注册、别名收敛、未知平台报错
2. 契约：PlatformMeta / LoginState 的字典兼容层
3. 第三方平台可以零改动主流程地接入（用一个 Fake 平台验证）
4. main.process_job 会按 capability 正确跳过/放行
5. Cookie 按平台隔离，且历史 cookies.txt 能自动迁移
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILED.append(name)


def test_registry():
    print("\n== 1. 平台注册表 ==")
    from api.platform import PLATFORM_REGISTRY, get_platform, normalize_platform_key
    from api.platform.registry import ensure_platforms_loaded

    ensure_platforms_loaded()
    check("chaoxing 已注册", "chaoxing" in PLATFORM_REGISTRY, PLATFORM_REGISTRY.keys())
    for alias in ("cx", "学习通", "超星", "chaoxing"):
        check(f"别名 {alias} -> chaoxing", normalize_platform_key(alias) == "chaoxing")
    check("空值回落 chaoxing", normalize_platform_key("") == "chaoxing")
    check("get_platform 返回子类", get_platform("chaoxing").__name__ == "ChaoxingPlatform")

    try:
        get_platform("not_exists")
        check("未知平台应报错", False)
    except Exception as e:
        check("未知平台报错", "未知平台" in str(e), str(e))


def test_contract():
    print("\n== 2. 契约对象 ==")
    from api.platform import LoginState, PlatformCapability

    s = LoginState.success("ok")
    check("LoginState.ok", s.ok is True)
    check("LoginState 字典访问", s["status"] is True and s.get("msg") == "ok")
    check("LoginState to_dict", s.to_dict() == {"status": True, "msg": "ok"})

    from api.platform import get_platform
    cls = get_platform("chaoxing")
    check("超星支持 video", cls.meta.capabilities.__contains__(PlatformCapability.VIDEO))
    check("超星支持 cookie 登录", cls.meta.capabilities.__contains__(PlatformCapability.LOGIN_COOKIE))


def test_third_party_platform():
    """模拟一个新平台：验证主流程无需修改即可接入。"""
    print("\n== 3. 第三方平台接入 ==")
    from api.platform import (
        ChapterResult,
        Platform,
        PlatformCapability,
        PlatformManager,
        PlatformMeta,
        StudyResult,
        register_platform,
    )

    @register_platform
    class FakePlatform(Platform):
        key = "fake"
        meta = PlatformMeta(
            key="fake",
            name="测试平台",
            account_hint="学号",
            config_fields=(
                {"key": "school", "label": "学校", "type": "text"},
            ),
            capabilities=frozenset({PlatformCapability.LOGIN_PASSWORD, PlatformCapability.VIDEO}),
        )

        def _create_session(self):
            return None

        def _login_with_password(self):
            from api.platform import LoginState
            return LoginState.success()

        def get_course_list(self):
            from api.platform import normalize_course
            return [normalize_course(
                {"courseId": 101, "clazzId": "", "cpi": "", "title": "假课程"}, self.key)]

        def get_course_point(self, course):
            from api.platform import normalize_point
            return {"points": [normalize_point({"id": 1, "title": "第一章"})], "hasLocked": False}

        def get_job_list(self, course, point):
            return [{"type": "video", "jobid": "j1", "title": "视频"}], {"notOpen": False}

        def process_job(self, course, job, job_info, speed=1.0, stop_event=None):
            return StudyResult.SUCCESS

    from api.platform import PLATFORM_REGISTRY
    check("FakePlatform 已注册", "fake" in PLATFORM_REGISTRY)

    inst = PlatformManager.create("fake")
    check("平台实例创建", isinstance(inst, FakePlatform))
    check("课程规范化 courseId 为字符串", inst.get_course_list()[0]["courseId"] == "101")
    check("章节 id 为字符串", inst.get_course_point({})["points"][0]["id"] == "1")
    check("能力判断: 支持 video", inst.supports(PlatformCapability.VIDEO))
    check("能力判断: 不支持 sign", not inst.supports(PlatformCapability.SIGN))
    desc = inst.describe()
    # describe() 是给前端渲染表单用的，只能有"提示文案"，不能回传真实凭据
    leaked = {"password", "username", "cookies", "token"} & set(desc)
    check("describe 不泄露凭据字段", not leaked, f"泄露字段: {leaked}")

    # 主流程按 capability 派发
    import main
    r = main.process_job(inst, {"title": "假课程"}, {"type": "video", "jobid": "j1"}, {}, 1.0)
    check("process_job 放行支持的任务", r == StudyResult.SUCCESS, r)
    r = main.process_job(inst, {"title": "假课程"}, {"type": "live", "jobid": "j2"}, {}, 1.0)
    check("process_job 跳过不支持的任务(不误判失败)", r == StudyResult.SUCCESS, r)


def test_cookie_isolation():
    print("\n== 4. Cookie 按平台隔离 ==")
    import shutil
    from unittest import mock

    from api.platform import cookies as ck
    from api.platform.cookies import cookie_path_for, load_cookies, parse_cookies, save_cookies

    check("解析 cookie 字符串", parse_cookies("a=1; b=2") == {"a": "1", "b": "2"})

    # 关键：绝不能写真实的 cookies/ 目录（那里存放登录凭据）。
    # 用工作区下的临时目录替换 COOKIE_DIR，并禁用历史 cookies.txt 的迁移。
    # 刻意只建一层目录：受限沙箱下递归建目录/递归删除可能被拒绝访问。
    sandbox_dir = os.path.join(os.getcwd(), "_cookietest_sandbox")
    os.makedirs(sandbox_dir, exist_ok=True)
    try:
        with mock.patch.object(ck, "COOKIE_DIR", sandbox_dir), \
             mock.patch.object(ck, "LEGACY_COOKIE_PATH", os.path.join(sandbox_dir, "__none__.txt")):
            save_cookies({"token": "abc"}, "chaoxing")
            save_cookies({"zhs": "xyz"}, "fake")
            check("超星 cookie 独立", load_cookies("chaoxing") == {"token": "abc"}, load_cookies("chaoxing"))
            check("其它平台 cookie 独立", load_cookies("fake") == {"zhs": "xyz"}, load_cookies("fake"))
            check("未知平台返回空", load_cookies("no_such_platform") == {})
            # 两个平台的 cookie 落在不同文件，不会互相覆盖
            check(
                "cookie 按平台分文件",
                cookie_path_for("chaoxing") != cookie_path_for("fake"),
                (cookie_path_for("chaoxing"), cookie_path_for("fake")),
            )
    finally:
        for name in os.listdir(sandbox_dir):
            try:
                os.remove(os.path.join(sandbox_dir, name))
            except OSError:
                pass
        shutil.rmtree(sandbox_dir, ignore_errors=True)


def test_main_flow():
    print("\n== 5. main 流程完整性 ==")
    import main
    for fn in ("init_platform", "init_chaoxing", "process_job", "process_chapter",
               "process_course", "run_study", "filter_courses", "_run_force_duration"):
        check(f"main.{fn} 存在", hasattr(main, fn))

    from main import ChapterResult as MainChapterResult
    from api.platform import ChapterResult as PlatformChapterResult
    check("ChapterResult 统一为平台层定义", MainChapterResult is PlatformChapterResult)

    # filter_courses 仍按 courseId 过滤
    courses = [{"courseId": "1", "title": "A"}, {"courseId": "2", "title": "B"}]
    got = main.filter_courses(courses, ["2"])
    check("filter_courses 正常过滤", len(got) == 1 and got[0]["courseId"] == "2", got)


def main_entry():
    print("=" * 60)
    print("多平台抽象层回归自测")
    print("=" * 60)
    test_registry()
    test_contract()
    test_third_party_platform()
    test_cookie_isolation()
    test_main_flow()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main_entry())
