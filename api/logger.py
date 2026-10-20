import re
import sys

from loguru import logger
from tqdm import tqdm

tqdm_stream = sys.stderr

# ---------------------------------------------------------------------------
# 日志脱敏
# ---------------------------------------------------------------------------
# 背景：loguru 的异常堆栈（logger.exception / logger.opt(exception=...)）会把
# 被捕获异常的局部变量连同 dict/list 内容整段转储，曾导致 config dict 中的
# username / password / tokens 明文落进 chaoxing.log。这里在 sink 层统一脱敏，
# 无论日志来自普通调用还是异常转储，都会先过一遍过滤器。
# ---------------------------------------------------------------------------
_REDACTED = "***REDACTED***"

# key: 'value' / key="value" / key=value（覆盖 dict repr、f-string、日志格式化）
_SECRET_PATTERNS = [
    # 带引号形式: 'password': 'xxx' / "tokens": "xxx"
    (re.compile(r"""((?:['"]?\b(?:password|passwd|pwd|username|user)\b['"]?)\s*[:=]\s*)(['"])(?:(?!\2).)*\2""", re.I),
     lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}{m.group(2)}"),
    (re.compile(r"""((?:['"]?\b(?:tokens?|api_key|apikey|authorization|secret|sendkey|cookies?|sessionid)\b['"]?)\s*[:=]\s*)(['"])(?:(?!\2).)*\2""", re.I),
     lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}{m.group(2)}"),
    # 裸 key=value 形式（ini 片段、f-string、格式化输出）
    (re.compile(r"""((?:^|[^\w'"\[\]])\b(?:password|passwd|pwd|username|tokens?|api_key|apikey|secret|sendkey)\b\s*[:=]\s*)(?!\*{3})([^\s,'"}\]]+)""", re.I),
     lambda m: f"{m.group(1)}{_REDACTED}"),
    # Authorization: Bearer xxxxx
    (re.compile(r"(Bearer\s+)\S+", re.I), lambda m: f"{m.group(1)}{_REDACTED}"),
    # OpenAI / 通用 sk- 风格 key
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), lambda m: "sk-" + _REDACTED),
    # 超星 cookie 片段
    (re.compile(r"(JSESSIONID=)[^\s;,'\"]+", re.I), lambda m: f"{m.group(1)}{_REDACTED}"),
    (re.compile(r"(_uid=)[^\s;,'\"]+", re.I), lambda m: f"{m.group(1)}{_REDACTED}"),
]


def redact(text: str) -> str:
    """对日志文本做凭据脱敏，失败时返回原文，绝不因脱敏异常影响业务。"""
    if not text:
        return text
    try:
        for pattern, repl in _SECRET_PATTERNS:
            text = pattern.sub(repl, text)
        return text
    except Exception:
        return text


def tqdm_sink(msg):
    try:
        tqdm.write(redact(str(msg)).rstrip(), file=tqdm_stream)
        tqdm_stream.flush()
    except Exception:
        pass


def _add_sink(level: str = "INFO"):
    """添加控制台 sink。

    优先使用 enqueue=True（多线程日志更安全），但如果运行环境不允许
    创建 multiprocessing 管道（例如受限沙箱会抛 PermissionError），
    则自动降级为同步写入，保证程序能正常启动而不是直接崩溃。
    """
    try:
        return logger.add(tqdm_sink, colorize=True, enqueue=True, level=level)
    except Exception:
        return logger.add(tqdm_sink, colorize=True, enqueue=False, level=level)


logger.remove()
_console_handler_id = _add_sink("INFO")


def configure_console_logger(level: str = "INFO"):
    global _console_handler_id
    logger.remove(_console_handler_id)
    _console_handler_id = _add_sink(level)
