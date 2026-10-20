# -*- coding: utf-8 -*-
import argparse
import configparser
import enum
import sys
import threading
import time
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import PriorityQueue
from threading import RLock
from typing import Any

from tqdm import tqdm

from api.answer import Tiku
from api.base import Account
from api.config import GlobalConst as gc
from api.exceptions import LoginError, InputFormatError
from api.logger import configure_console_logger, logger
from api.notification import Notification
# 平台抽象层：主流程只依赖 Platform 契约，不再写死超星
from api.platform import (
    JOB_TYPE_TO_CAPABILITY,
    ChapterResult,
    Platform,
    PlatformManager,
    PlatformNotSupported,
    StudyResult,
    list_platforms,
    normalize_platform_key,
)


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except BaseException as e:
            logger.error(f"Error in thread {threading.current_thread().name}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    return wrapper


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args():
    """解析命令行参数"""
    platform_choices = [p["key"] for p in list_platforms()]
    parser = argparse.ArgumentParser(
        description="Samueli924/chaoxing · 多平台在线课程学习助手",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-P", "--platform", type=str, default=None, choices=platform_choices,
        help=f"要使用的平台: {', '.join(platform_choices)} (默认读取配置文件, 未配置则 chaoxing)",
    )

    parser.add_argument("--use-cookies", action="store_true", help="使用cookies登录")

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.5, help="视频播放倍速 (默认1.5, 最大4)"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, help="同时进行的章节数 (默认4, 如果一个章节有多个任务点，不会限制同时处理任务点的数量)"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry", 
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )

    parser.add_argument("--auto-sign", action="store_true", help="自动签到")

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf8")
    
    common_config: dict[str, Any] = {}
    tiku_config: dict[str, Any] = {}
    notification_config: dict[str, Any] = {}
    
    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = [item.strip() for item in common_config["course_list"].split(",") if item.strip()]
        # 处理speed，将字符串转换为浮点数
        if "speed" in common_config:
            common_config["speed"] = float(common_config["speed"])
        if "jobs" in common_config:
            common_config["jobs"] = int(common_config["jobs"])
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        # 处理read_duration
        if "read_duration" in common_config:
            common_config["read_duration"] = int(common_config["read_duration"])
        if "use_cookies" in common_config:
            common_config["use_cookies"] = str_to_bool(common_config["use_cookies"])
        if "username" in common_config and common_config["username"] is not None:
            common_config["username"] = common_config["username"].strip()
        if "password" in common_config and common_config["password"] is not None:
            common_config["password"] = common_config["password"].strip()
        if "verbose" in common_config:
            common_config["verbose"] = str_to_bool(common_config["verbose"])
        # 平台选择：缺省回落 chaoxing，兼容历史配置文件
        common_config["platform"] = normalize_platform_key(
            common_config.get("platform", "") or "chaoxing"
        )

    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                tiku_config[key] = float(tiku_config[key])

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))
    
    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置"""
    common_config = {
        "use_cookies": args.use_cookies,
        "username": args.username,
        "password": args.password,
        "course_list": [item.strip() for item in args.list.split(",") if item.strip()] if args.list else None,
        "speed": args.speed if args.speed else 1.5,
        "jobs": args.jobs,
        "notopen_action": args.notopen_action if args.notopen_action else "retry",
        "verbose": args.verbose,
        "platform": normalize_platform_key(getattr(args, "platform", None) or "chaoxing"),
    }
    return common_config, {}, {}


def init_config():
    """初始化配置"""
    args = parse_args()

    if args.config:
        common_config, tiku_config, notification_config = load_config_from_file(args.config)
        if args.verbose:
            common_config["verbose"] = True
        # 命令行 -P 优先于配置文件
        if getattr(args, "platform", None):
            common_config["platform"] = normalize_platform_key(args.platform)
        return common_config, tiku_config, notification_config
    else:
        return build_config_from_args(args)



def init_platform(common_config, tiku_config):
    """按配置初始化平台实例（替代原来的 init_chaoxing）。

    返回的是 ``Platform`` 子类实例；超星返回的是 ``ChaoxingPlatform``，
    它与旧的 ``Chaoxing`` 完全兼容。
    """
    platform_key = normalize_platform_key(common_config.get("platform", "chaoxing"))
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)

    # 如果没有提供用户名密码，从命令行获取
    if (not username or not password) and not use_cookies:
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = input("请输入你的密码, 按回车确认\n密码:")

    account = Account(username, password)

    # 设置题库
    tiku = Tiku()
    tiku.config_set(tiku_config)  # 载入配置
    tiku = tiku.get_tiku_from_config()  # 载入题库
    tiku.init_tiku()  # 初始化题库

    # 获取查询延迟设置
    query_delay = tiku_config.get("delay", 0)

    # 实例化平台
    read_duration = common_config.get("read_duration", 300)
    platform = PlatformManager.create(
        platform_key,
        account=account,
        tiku=tiku,
        config=common_config,
        query_delay=query_delay,
        read_duration=read_duration,
    )
    logger.info(f"已加载平台: {platform.display_name} ({platform_key})")
    return platform


def init_chaoxing(common_config, tiku_config):
    """向后兼容别名（历史调用方仍在使用这个名字）。"""
    common_config.setdefault("platform", "chaoxing")
    return init_platform(common_config, tiku_config)


def process_job(platform: Platform, course: dict, job: dict, job_info: dict, speed: float, stop_event: threading.Event | None = None) -> StudyResult:
    """处理单个任务点。

    任务点的具体实现由平台 adapter 负责（``Platform.process_job``），
    主流程只负责"该平台是否支持这类任务"的判断，不再内置任何平台私有逻辑。
    """
    job_type = job.get("type", "")
    capability = JOB_TYPE_TO_CAPABILITY.get(job_type)
    if capability is not None and not platform.supports(capability):
        logger.warning(
            f"平台 {platform.display_name} 暂不支持任务类型 {job_type}, 已跳过 "
            f"-> 章节: {course['title']} 任务ID: {job.get('jobid')}"
        )
        # 平台不支持 ≠ 任务失败：记录日志后放行，避免整章被误判失败
        return StudyResult.SUCCESS

    try:
        return platform.process_job(
            course, job, job_info, speed=speed, stop_event=stop_event
        )
    except PlatformNotSupported as e:
        logger.warning(f"平台 {platform.display_name} 未实现 {job_type} 任务处理, 已跳过: {e}")
        return StudyResult.SUCCESS
    except Exception as e:
        logger.exception(f"任务点处理异常 ({job_type}): {e}")
        return StudyResult.ERROR


@dataclass(order=True, unsafe_hash=False)
class ChapterTask:
    """章节任务。

    显式定义 ``__hash__`` 是必须的：``@dataclass(order=True)`` 会自动生成
    ``__eq__``，Python 随即把 ``__hash__`` 置为 ``None``，任务对象就**不可哈希**。
    而 JobProcessor 用 ``self._settled: set[ChapterTask]`` 做"每个任务只结算一次"
    的去重，一入队就会抛 ``TypeError: unhashable type: 'ChapterTask'``，
    表现为 worker 线程全部静默死掉、主线程在 ``_wait_all_settled`` 里永久挂起
    （界面上就是一直"运行中"、永不结束）。

    这里按 ``index`` 哈希：index 在单个课程内唯一，且与 ``__eq__`` 的
    比较字段一致（point/result/tries 都是 compare=False）。
    """

    index: int
    point: dict[str, Any] = field(compare=False)
    result: ChapterResult = field(default=ChapterResult.PENDING, compare=False)
    tries: int = field(default=0, compare=False)

    def __hash__(self) -> int:
        return hash(self.index)


class JobProcessor:
    STOP_POINT = {"__stop__": True}

    def __init__(self, platform: Platform, course: dict[str, Any], tasks: list[ChapterTask], config: dict[str, Any], stop_event: threading.Event | None = None, progress_callback=None):
        if "jobs" not in config or not config["jobs"]:
            config["jobs"] = 4

        self.platform = platform
        self.course = course
        self.speed = config["speed"]
        self.max_tries = 5
        self.tasks = tasks
        self.stop_event = stop_event
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.wait_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.retry_worker: threading.Thread | None = None
        self.worker_num = max(1, int(config["jobs"]))  # 负数/0 会让 range() 不建 worker -> 永久等待
        self.config = config
        self.progress_callback = progress_callback
        self._completed_count = 0
        self._total_count = len(tasks)
        self._lock = threading.Lock()
        # 任务结算计数: 每个章节任务最终只结算一次(成功/跳过/失败/主动停止)。
        # 不再使用 PriorityQueue.join()/task_done() 做控制流, 因为重试路径
        # (worker -> retry_queue -> retry_thread -> task_queue) 的 task_done 调用
        # 与 put 计数一旦在并发/停止竞态下错位, 就会抛
        # "task_done() called too many times" 或让 join() 永久阻塞。
        self._pending = 0
        self._settled: set[ChapterTask] = set()
        self._settle_cond = threading.Condition()

    def _settle(self, task: ChapterTask | None = None):
        """标记一个章节任务已最终结束(重试路径不再结算, 见 _handle_task 注释)。

        task 不为 None 时按集合去重结算: 集合本身保证了同一个任务只会被计数一次,
        不会像纯计数器那样出现"多结算一个 -> 提前 notify, 剩下的任务找不到归属"的错位。
        """
        with self._settle_cond:
            if task is not None:
                if task not in self._settled:
                    self._settled.add(task)
            if self._pending <= 0:
                # 正常情况下不会发生; 真发生时打日志而不是静默提前收尾
                logger.warning("任务结算计数异常: _pending 已归零仍收到 settle")
            self._pending = max(0, self._pending - 1)
            if self._pending <= 0:
                self._settle_cond.notify_all()

    def _wait_all_settled(self):
        """等待所有已入队的章节任务最终结束。

        收到停止信号后不再无限等待: 已入队但还没被 worker 取走的章节会随 worker 退出
        一起丢弃, 此时若继续等 _pending 归零, 停止请求就会永远卡在"正在停止"。
        """
        with self._settle_cond:
            while self._pending > 0:
                if self.stop_event and self.stop_event.is_set():
                    logger.info("收到停止信号, 放弃等待剩余 {} 个未开始章节", self._pending)
                    break
                self._settle_cond.wait(timeout=0.5)

    def _stop_task(self) -> ChapterTask:
        return ChapterTask(index=sys.maxsize, point=self.STOP_POINT)

    @staticmethod
    def _is_stop_task(task: ChapterTask) -> bool:
        return task.point.get("__stop__") is True

    def run(self):
        queued = 0
        for task in self.tasks:
            if self.stop_event and self.stop_event.is_set():
                logger.info("收到停止信号，停止处理后续章节")
                break
            self.task_queue.put(task)
            queued += 1
        with self._settle_cond:
            self._pending = queued

        for i in range(self.worker_num):
            thread = threading.Thread(target=self.worker_thread, daemon=True)
            self.threads.append(thread)
            thread.start()

        self.retry_worker = threading.Thread(target=self.retry_thread, daemon=True)
        self.retry_worker.start()

        # 等所有任务真正结算(成功/跳过/失败), 而不是依赖队列的 unfinished_tasks
        self._wait_all_settled()

        # 停止信号下 worker 可能仍在处理长任务: 用超时 put 投放终止标记,
        # 避免队列满时主线程被阻塞到 worker 结束才收尾。
        for _ in self.threads:
            try:
                self.task_queue.put(self._stop_task(), timeout=0.5)
            except Exception:
                logger.warning("投放工作线程终止标记超时, 跳过(停止信号)")
        try:
            self.retry_queue.put(self._stop_task(), timeout=0.5)
        except Exception:
            logger.warning("投放重试线程终止标记超时, 跳过(停止信号)")

        # 收到停止信号后不再无限期 join: worker 可能正卡在一次长任务(或阻塞的网络请求)里,
        # 无超时的 join 会让整个刷课线程无法收尾, 前端就永远停在"正在停止"。
        # worker 排空队列后自然会退出, 这里只等一小会儿; 剩下的交给 daemon 线程和
        # stop_event 在下一个检查点自行收敛。
        stop_join_timeout = 2 if (self.stop_event and self.stop_event.is_set()) else None
        for thread in self.threads:
            thread.join(timeout=stop_join_timeout)
            if thread.is_alive():
                logger.warning("章节处理线程未能及时退出, 已放弃等待(停止信号)")
        if self.retry_worker:
            self.retry_worker.join(timeout=stop_join_timeout)
            if self.retry_worker.is_alive():
                logger.warning("重试线程未能及时退出, 已放弃等待(停止信号)")


    @log_error
    def worker_thread(self):
        tqdm.set_lock(tqdm.get_lock())
        while True:
            task = self.task_queue.get()
            if self._is_stop_task(task):
                logger.debug("Queue worker stopped")
                return

            # 每个任务最终只结算一次: 成功/跳过/失败时结算;
            # 交给 retry_queue 重试时不结算, 等它再次被处理后结算。
            retried = False
            try:
                retried = self._handle_task(task)
            except Exception as e:
                # 单章异常不应杀死 worker 线程，降级为该章节失败
                logger.exception(f"处理章节异常: {task.point.get('title')} - {e}")
                task.tries += 1
                if task.tries >= self.max_tries:
                    with self._lock:
                        self.failed_tasks.append(task)
                elif self.stop_event and self.stop_event.is_set():
                    # 因停止信号放弃重试: 也要计入失败列表, 避免章节"静默消失"
                    with self._lock:
                        self.failed_tasks.append(task)
                else:
                    self.retry_queue.put(task)
                    retried = True
            finally:
                if not retried:
                    self._settle(task)

    def _handle_task(self, task) -> bool:
        """处理单个章节任务。

        返回 True 表示该任务已被放回 retry_queue（由 retry_thread 重新排队，稍后再次结算），
        返回 False 表示该任务已经最终结束，由 worker_thread 调用 _settle()。
        """
        task.result = process_chapter(self.platform, self.course, task.point, self.speed,
                                      stop_event=self.stop_event)

        match task.result:
            case ChapterResult.SUCCESS:
                logger.debug("Task success: {}", task.point["title"])
                with self._lock:
                    self._completed_count += 1
                    completed = self._completed_count
                    total = self._total_count
                if self.progress_callback:
                    self.progress_callback(
                        current_chapter=task.point["title"],
                        completed_chapters=completed,
                        total_chapters=total,
                    )
                logger.debug("pending tasks: {}", self._pending)

            case ChapterResult.NOT_OPEN:
                if self.config["notopen_action"] == "continue":
                    logger.warning("章节未开启: {}, 正在跳过", task.point["title"])
                    return False

                task.tries += 1
                if task.tries >= self.max_tries:
                    logger.error(
                        "章节未开启: {} 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                        "请手动检查完成并提交再重试。或者在配置中配置(自动跳过关闭章节/开启题库并启用提交)"
                    , task.point["title"])
                    return False

                # self.wait_queue.put(task)
                self.retry_queue.put(task)
                return True

            case ChapterResult.STOPPED:
                logger.info("任务因停止信号中断: {}", task.point["title"])

            case ChapterResult.FORBIDDEN:
                # 403等确定性失败, 重试不会有不同结果, 直接标记失败
                logger.error(
                    "任务被服务端拒绝(403), 重试无意义, 已跳过: {} (请稍后手动检查该章节)",
                    task.point["title"],
                )
                with self._lock:
                    self.failed_tasks.append(task)
                    self._completed_count += 1
                    completed = self._completed_count
                    total = self._total_count
                if self.progress_callback:
                    self.progress_callback(
                        current_chapter=f"❌ {task.point['title']} (403跳过)",
                        completed_chapters=completed,
                        total_chapters=total,
                    )

            case ChapterResult.ERROR:
                if self.stop_event and self.stop_event.is_set():
                    # 用户主动停止: 不要把一个"被停止打断"的章节当成失败去重试
                    logger.info("任务因停止信号中断, 不再重试: {}", task.point["title"])
                    return False
                task.tries += 1
                logger.warning("Retrying task {} ({}/{} attempts)", task.point["title"], task.tries,
                               self.max_tries)
                if task.tries >= self.max_tries:
                    logger.error("Max retries reached for task: {}", task.point["title"])
                    with self._lock:
                        self.failed_tasks.append(task)
                    return False
                self.retry_queue.put(task)
                return True

            case _:
                logger.error("Invalid task state {} for task {}", task.result, task.point["title"])
                with self._lock:
                    self.failed_tasks.append(task)

        return False

    @log_error
    def retry_thread(self):
        while True:
            task = self.retry_queue.get()
            if self._is_stop_task(task):
                return
            try:
                self.task_queue.put(task)
            except Exception as e:
                logger.exception(f"重试队列入队失败: {e}")
                # 入队失败说明这个章节没法再重试, 直接结算, 避免 _pending 永远不归零
                self._settle(task)
            time.sleep(1) # TODO: Replace with a configurable wait time


def process_chapter(platform: Platform, course:dict[str, Any], point:dict[str, Any], speed:float, stop_event: threading.Event | None = None) -> ChapterResult:
    """处理单个章节"""
    if stop_event and stop_event.is_set():
        return ChapterResult.STOPPED
    logger.info(f'当前章节: {point["title"]}')
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已标记完成，强制继续处理...')
    
    # 随机等待，避免请求过快
    platform.rate_limiter.limit_rate(random_time=True,random_min=0, random_max=0.2)
    
    # 获取当前章节的所有任务点
    job_info = None
    jobs, job_info = platform.get_job_list(course, point)

    # 发现未开放章节, 根据配置处理
    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    # 已经默认处理空任务，此处不需要判断
    if not jobs:
        pass

    from concurrent.futures import as_completed
    job_results:list[StudyResult]=[]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_job, platform, course, job, job_info, speed, stop_event): job for job in jobs}
        for future in as_completed(futures):
            if stop_event and stop_event.is_set():
                logger.info("收到停止信号，取消剩余任务点")
                for f in futures:
                    f.cancel()
                break
            try:
                result = future.result()
                job_results.append(result)
            except Exception as e:
                logger.error(f"任务点处理异常: {e}")
                job_results.append(StudyResult.ERROR)
    
    if stop_event and stop_event.is_set():
        return ChapterResult.STOPPED

    for result in job_results:
        if result == StudyResult.FORBIDDEN:
            return ChapterResult.FORBIDDEN

    for result in job_results:
        if result.is_failure():
            return ChapterResult.ERROR

    return ChapterResult.SUCCESS



def process_course(platform: Platform, course:dict[str, Any], config: dict, stop_event: threading.Event | None = None, progress_callback=None) -> list[ChapterTask]:
    """处理单个课程, 返回学习失败的章节任务列表"""
    logger.info(f"开始学习课程: {course['title']}")

    # 获取当前课程的所有章节
    point_list = platform.get_course_point(course)

    # 为了支持课程任务回滚, 采用下标方式遍历任务点

    _old_format_sizeof = tqdm.format_sizeof
    tqdm.format_sizeof = format_time
    tqdm.set_lock(RLock())

    tasks=[]

    for i, point in enumerate(point_list["points"]):
        task = ChapterTask(point=point, index=i)
        tasks.append(task)

    # 更新进度：当前课程的章节数
    if progress_callback:
        progress_callback(total_chapters=len(tasks), completed_chapters=0)

    p = JobProcessor(platform, course, tasks, config, stop_event=stop_event, progress_callback=progress_callback)
    p.run()

    # 强制学习时长模式：所有任务完成后，循环访问章节累计学习时长。
    # 具体"怎么累计时长"是平台私有的（超星是 multimodal log + readv2 心跳），
    # 因此下沉到 ``Platform.force_study_duration``，主流程只负责调用与进度上报。
    force_duration = int(config.get("read_duration", 0))
    if force_duration > 0 and point_list.get("points"):
        logger.info(f"所有任务处理完毕，进入强制学习时长模式: {force_duration}s...")
        _run_force_duration(
            platform,
            course,
            point_list["points"],
            force_duration,
            total_tasks=len(tasks),
            stop_event=stop_event,
            progress_callback=progress_callback,
        )

    tqdm.format_sizeof = _old_format_sizeof

    return p.failed_tasks


def _run_force_duration(platform: Platform, course: dict[str, Any], points: list[dict[str, Any]],
                        force_duration: int, total_tasks: int,
                        stop_event: threading.Event | None = None,
                        progress_callback=None) -> None:
    """强制的"挂机学习时长"循环。

    平台 adapter 通过实现 ``force_study_duration`` 来支持这个能力；
    未实现的平台直接跳过（不报错），这样新增平台可以后补。
    """
    from api.platform import PlatformCapability

    if not platform.supports(PlatformCapability.READ):
        logger.info(f"平台 {platform.display_name} 不支持强制学习时长模式, 已跳过")
        return

    start = time.time()
    pbar = tqdm(total=force_duration, desc="强制学习时长", unit_scale=True,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}")
    try:
        while time.time() - start < force_duration:
            if stop_event and stop_event.is_set():
                logger.info("强制学习时长收到停止信号，提前退出")
                break
            _elapsed = int(time.time() - start)
            try:
                platform.force_study_duration(
                    course, points, elapsed=_elapsed, total_duration=force_duration
                )
            except PlatformNotSupported:
                logger.info(f"平台 {platform.display_name} 未实现强制学习时长, 已跳过")
                return
            except Exception as e:
                logger.debug(f"强制学习时长上报异常(忽略): {e}")

            pbar.n = min(_elapsed, force_duration)
            pbar.refresh()
            if progress_callback:
                progress_callback(
                    current_chapter=f"⏳ 强制学习时长 {_elapsed}/{force_duration}s",
                    completed_chapters=total_tasks,
                    total_chapters=total_tasks,
                )
            time.sleep(1)
    finally:
        pbar.close()
        logger.info(f"强制学习时长完成: {int(time.time() - start)}s")

def filter_courses(all_course, course_list):
    """过滤要学习的课程"""
    if not course_list:
        # 手动输入要学习的课程ID列表
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 课程名: {course['title']}")
        print("*" * 28)
        try:
            course_list = input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198\n"
            ).split(",")
        except Exception as e:
            raise InputFormatError("输入格式错误") from e
    course_list = [str(course_id).strip() for course_id in course_list if str(course_id).strip()]
    if not course_list:
        logger.warning("未指定课程ID，将学习全部课程")
        return all_course

    # 筛选需要学习的课程
    course_task = []
    course_ids = []
    for course in all_course:
        if course["courseId"] in course_list and course["courseId"] not in course_ids:
            course_task.append(course)
            course_ids.append(course["courseId"])
    
    if not course_task:
        raise InputFormatError(f"未找到指定课程ID: {', '.join(course_list)}")
    
    return course_task


def format_time(num, suffix='', divisor=''):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"

    return f"{mins:02d}:{sec:02d}"


def run_study(common_config, tiku_config, notification_config,
              stop_event: threading.Event | None = None,
              progress_callback=None):
    """
    执行刷课流程（可被 Web 后台线程调用）

    Args:
        common_config: dict with common settings
        tiku_config: dict with tiku settings
        notification_config: dict with notification settings
        stop_event: threading.Event - if set, stops after current chapter
        progress_callback: callable(**kwargs) - called with state updates
    """
    configure_console_logger("DEBUG" if common_config.get("verbose", False) else "INFO")

    # 强制播放按照配置文件调节
    _speed = float(common_config.get("speed", 1.5))
    common_config["speed"] = min(gc.MAX_REPORT_SPEED, max(1.0, _speed))
    common_config["notopen_action"] = common_config.get("notopen_action", "retry")

    # 初始化平台实例（默认超星，由 common_config["platform"] 决定）
    platform = init_platform(common_config, tiku_config)
    platform_key = normalize_platform_key(common_config.get("platform", "chaoxing"))
    platform_label = platform.display_name

    # 设置外部通知
    notification = Notification()
    notification.config_set(notification_config)
    notification = notification.get_notification_from_config()
    notification.init_notification()

    # 检查当前登录状态
    _login_state = platform.login(login_with_cookies=common_config.get("use_cookies", False))
    # 兼容两种返回：历史字典 {"status":bool} 与平台层 LoginState
    if hasattr(_login_state, "ok"):
        _ok = bool(_login_state.ok)
        _msg = _login_state.msg
    else:
        _ok = bool(_login_state.get("status")) if isinstance(_login_state, dict) else False
        _msg = str(_login_state.get("msg", "")) if isinstance(_login_state, dict) else str(_login_state)
    if not _ok:
        raise LoginError(_msg)

    # 获取所有的课程列表
    all_course = platform.get_course_list()

    # 过滤要学习的课程
    course_task = filter_courses(all_course, common_config.get("course_list"))

    # 更新进度：总课程数
    if progress_callback:
        progress_callback(total_courses=len(course_task))

    # 开始学习
    logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")
    all_failed_tasks: list[ChapterTask] = []
    for i, course in enumerate(course_task):
        if stop_event and stop_event.is_set():
            logger.info("收到停止信号, 停止学习")
            break
        if progress_callback:
            progress_callback(
                current_course=course["title"],
                completed_courses=i,
            )
        failed = process_course(platform, course, common_config, stop_event=stop_event, progress_callback=progress_callback)
        all_failed_tasks.extend(failed or [])
        if progress_callback:
            progress_callback(completed_courses=i + 1)

    if stop_event and stop_event.is_set():
        # 用户主动停止: 不能误报"已完成"
        logger.warning("学习已按停止信号中止, 未完成的章节不会计入失败: {}", len(all_failed_tasks))
        notification.send(f"{platform_label} : 学习已停止, {len(all_failed_tasks)} 个章节未完成")
    elif all_failed_tasks:
        failed_titles = ", ".join(t.point.get("title", "未知章节") for t in all_failed_tasks)
        logger.warning(f"课程学习结束, 但有 {len(all_failed_tasks)} 个章节未成功完成: {failed_titles}")
        notification.send(f"{platform_label} : 学习结束, {len(all_failed_tasks)} 个章节未成功: {failed_titles}")
    else:
        logger.info("所有课程学习任务已完成")
        notification.send(f"{platform_label} : 所有课程学习任务已完成")


def main():
    """主程序入口（CLI 模式）"""
    try:
        # 初始化配置
        common_config, tiku_config, notification_config = init_config()
        run_study(common_config, tiku_config, notification_config)
        
    except SystemExit as e:
        if e.code != 0:
            logger.error(f"错误: 程序异常退出, 返回码: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt as e:
        logger.error(f"错误: 程序被用户手动中断, {e}")
    except BaseException as e:
        logger.error(f"错误: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        try:
            notification.send(f"超级课程助手 : 出现错误 {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except (Exception, NameError):
            pass  # 如果通知发送失败，忽略异常
        raise e


if __name__ == "__main__":
    main()
