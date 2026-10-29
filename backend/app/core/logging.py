"""结构化日志：每次问答一行 JSON，便于回放排障。"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:16]
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get() or "-"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": get_trace_id(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 压掉噪音
    for noisy in ("httpx", "httpcore", "urllib3", "qdrant_client", "jieba", "fastembed"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, msg: str, **fields: Any) -> None:
    logger.info(msg, extra={"fields": fields})


@contextmanager
def timer() -> Any:
    start = time.perf_counter()
    box: dict[str, float] = {}
    try:
        yield box
    finally:
        box["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 1)
