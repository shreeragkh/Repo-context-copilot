"""
Two separate log stores, both surfaced on the admin dashboard:

1. `_log_buffer`   - general structured application logs (startup, ingestion,
                     cleanup, errors) via the standard `logging` module.
2. `_query_log`    - one entry per user query, capturing the adaptive answer,
                     (optionally) the baseline answer, token reduction %,
                     chunks used, which model answered, and whether it was
                     the free or paid tier.
"""
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_MAX_LOG_ENTRIES = 500
_MAX_QUERY_LOG_ENTRIES = 500

_log_buffer: deque[dict[str, Any]] = deque(maxlen=_MAX_LOG_ENTRIES)
_query_log: deque[dict[str, Any]] = deque(maxlen=_MAX_QUERY_LOG_ENTRIES)
_lock = threading.Lock()
_qlock = threading.Lock()


class StructuredLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }
        for key in ("query", "cache_hit", "latency_ms", "component", "detail"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        with _lock:
            _log_buffer.append(entry)


def setup_logging(level=logging.INFO):
    root = logging.getLogger()
    root.setLevel(level)
    handler = StructuredLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())


def get_recent_logs(n: int = 100) -> list[dict[str, Any]]:
    with _lock:
        items = list(_log_buffer)
    return items[-n:]


def clear_logs() -> int:
    with _lock:
        count = len(_log_buffer)
        _log_buffer.clear()
    return count


def record_query_log(
    *,
    query: str,
    is_admin: bool,
    complexity: str,
    adaptive_answer: str,
    adaptive_chunks: int,
    adaptive_tokens: int,
    model_name: str,
    model_provider: str,
    model_paid: bool,
    baseline_answer: str | None = None,
    baseline_chunks: int | None = None,
    baseline_tokens: int | None = None,
    cache_hit: bool = False,
    latency_ms: float = 0.0,
) -> dict[str, Any]:
    reduction_pct = None
    if baseline_tokens and baseline_tokens > 0 and adaptive_tokens is not None:
        reduction_pct = round(100 * (1 - adaptive_tokens / baseline_tokens), 1)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "is_admin": is_admin,
        "complexity": complexity,
        "adaptive_answer": adaptive_answer,
        "adaptive_chunks_used": adaptive_chunks,
        "adaptive_tokens": adaptive_tokens,
        "baseline_answer": baseline_answer,
        "baseline_chunks_used": baseline_chunks,
        "baseline_tokens": baseline_tokens,
        "token_reduction_pct": reduction_pct,
        "model_name": model_name,
        "model_provider": model_provider,
        "model_tier": "paid" if model_paid else "free",
        "cache_hit": cache_hit,
        "latency_ms": round(latency_ms, 1),
    }
    with _qlock:
        _query_log.append(entry)
    return entry


def get_query_logs(n: int = 100) -> list[dict[str, Any]]:
    with _qlock:
        items = list(_query_log)
    return items[-n:]


def clear_query_logs() -> int:
    with _qlock:
        count = len(_query_log)
        _query_log.clear()
    return count
