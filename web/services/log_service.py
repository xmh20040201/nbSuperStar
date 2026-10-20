import queue
import json
import threading
from collections import deque
from datetime import datetime


class LogService:
    """Captures loguru output and distributes to SSE subscribers."""

    def __init__(self, maxlen=500):
        self._ring = deque(maxlen=maxlen)
        self._subscribers = []
        self._subscribers_lock = threading.Lock()

    def write(self, message: str, level: str = "INFO"):
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        with self._subscribers_lock:
            self._ring.append(entry)
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self._subscribers_lock:
            self._subscribers.append(q)
            # Replay last 50 entries for new subscriber
            for entry in list(self._ring)[-50:]:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._subscribers_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def get_history(self, limit=200):
        with self._subscribers_lock:
            return list(self._ring)[-limit:]


# Singleton
log_service = LogService()


def loguru_sink(msg):
    """Callback for loguru: forward log records to LogService."""
    record = msg.record
    log_service.write(
        record["message"].rstrip(),
        level=record["level"].name,
    )


def init_log_capture():
    """Register the loguru sink at app startup."""
    from api.logger import logger
    logger.add(loguru_sink, level="TRACE")
