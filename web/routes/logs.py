import json
import time
from flask import Blueprint, Response, stream_with_context

from web.services.log_service import log_service
from web.services.state import state

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/api/logs/stream")
def stream_logs():
    """SSE endpoint for real-time log and progress streaming."""

    def generate():
        q = log_service.subscribe()
        try:
            while True:
                # Check for new log entries with a timeout
                try:
                    entry = q.get(timeout=2)
                    yield f"event: log\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except Exception:
                    # Timeout with no new entries - send heartbeat / progress
                    s = state.to_dict()
                    yield f"event: progress\ndata: {json.dumps(s, ensure_ascii=False)}\n\n"
                    yield "event: heartbeat\ndata: \n\n"
        except GeneratorExit:
            pass
        finally:
            log_service.unsubscribe(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
