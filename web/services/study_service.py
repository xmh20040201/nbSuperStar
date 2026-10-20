import threading
import time
import traceback

from api.logger import logger
from web.services.state import state
from web.services.log_service import log_service

_start_lock = threading.Lock()


def progress_callback(**kwargs):
    """Called from background thread to update study state."""
    state.update(**kwargs)
    # 计算总体进度百分比
    tc = state.total_chapters
    cc = state.completed_chapters
    total_courses = state.total_courses
    completed_courses = state.completed_courses
    if total_courses > 0:
        if tc > 0:
            # 有章节数据时，按课程内章节进度计算
            course_pct = cc / tc
            pct = (completed_courses + course_pct) / total_courses * 100
        else:
            # 无章节数据时，按课程完成数计算
            pct = completed_courses / total_courses * 100
        state.progress_pct = round(min(pct, 99.9), 1)


def start_study(common_config, tiku_config, notification_config):
    """Start the study process in a background thread."""
    global _start_lock
    with _start_lock:
        if state.is_running:
            return False, "已在运行中"

        # 上一轮的停止标志必须清干净: Event 是复用的对象, 残留的 set 状态会让
        # 新一轮刷课在第一个检查点就自杀, 表现成"重新运行还是上一次的结果"。
        state.stop_event.clear()
        # 逐字段重置, 而不是只重置进度数字:
        # 上一轮的 error_message / current_course / current_chapter 留着,
        # 前端就会瞬间显示上一轮的报错和课程名。
        state.reset()
        state.is_running = True
        state.status = "running"
        state.start_time = time.time()

        def _run():
            try:
                # Import here to avoid circular imports at module level
                from main import run_study as _run_study
                _run_study(
                    common_config,
                    tiku_config,
                    notification_config,
                    stop_event=state.stop_event,
                    progress_callback=progress_callback,
                )
                if state.stop_event.is_set():
                    state.status = "stopped"
                    log_service.write("刷课已停止", "WARNING")
                else:
                    state.status = "completed"
                    state.progress_pct = 100.0
                    log_service.write("所有课程学习任务已完成", "SUCCESS")
            except Exception as e:
                tb = traceback.format_exc()
                state.status = "error"
                state.error_message = f"{e}\n{tb}"
                log_service.write(f"刷课出错: {e}", "ERROR")
                logger.exception(f"Study thread error: {e}")
            finally:
                # 兜底：如果退出时状态仍卡在 stopping，强制转为 stopped
                if state.status == "stopping":
                    state.status = "stopped"
                    log_service.write("刷课已停止（强制收敛）", "WARNING")
                state.is_running = False

        thread = threading.Thread(target=_run, daemon=True, name="study-worker")
        state.thread = thread
        thread.start()
        return True, "已启动"


def stop_study():
    """Signal the study process to stop."""
    if not state.is_running:
        return False, "没有正在运行的任务"
    state.status = "stopping"
    state.stop_event.set()
    log_service.write("正在停止刷课...", "WARNING")
    return True, "正在停止..."

STOP_GRACE_SECONDS = 30


def force_stop():
    """强制收尾：worker 线程已退出但状态仍卡在 stopping 时，把界面拉回可操作状态。

    只做状态收敛，不打断线程本身（daemon 线程会随进程一起结束）。
    """
    thread = state.thread
    if thread is not None and thread.is_alive():
        return False, "任务仍在停止中，请稍候"
    state.stop_event.set()
    state.status = "stopped"
    state.is_running = False
    log_service.write("已强制结束刷课", "WARNING")
    return True, "已结束"
