import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StudyState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None

    is_running: bool = False
    current_course: str = ""
    current_chapter: str = ""
    current_job: str = ""
    progress_pct: float = 0.0
    total_courses: int = 0
    completed_courses: int = 0
    total_chapters: int = 0
    completed_chapters: int = 0
    status: str = "idle"  # idle | running | completed | error | stopped
    error_message: str = ""
    start_time: float = 0.0
    elapsed_seconds: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self):
        with self._lock:
            result = {}
            for k, v in self.__dict__.items():
                if not k.startswith("_") and k not in ("stop_event", "thread"):
                    result[k] = v
            result["elapsed_seconds"] = int(time.time() - self.start_time) if self.start_time else 0
            return result

    def reset(self):
        """清空上一轮的运行痕迹（不触碰 stop_event / thread / is_running）。"""
        with self._lock:
            self.current_course = ""
            self.current_chapter = ""
            self.current_job = ""
            self.progress_pct = 0.0
            self.total_courses = 0
            self.completed_courses = 0
            self.total_chapters = 0
            self.completed_chapters = 0
            self.status = "idle"
            self.error_message = ""
            self.start_time = 0.0
            self.elapsed_seconds = 0

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)


# Global singleton
state = StudyState()
