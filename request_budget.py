"""Thread-safe run-wide outbound request budget shared by all connectors."""
from __future__ import annotations

import threading


class RequestBudgetExceeded(RuntimeError):
    pass


_lock = threading.Lock()
_limit: int | None = None
_used = 0


def activate(limit: int = 2000) -> None:
    global _limit, _used
    if limit < 1:
        raise ValueError("request budget must be positive")
    with _lock:
        _limit, _used = limit, 0


def deactivate() -> None:
    global _limit, _used
    with _lock:
        _limit, _used = None, 0


def consume_request(source: str = "outbound") -> None:
    global _used
    with _lock:
        if _limit is not None and _used >= _limit:
            raise RequestBudgetExceeded(f"run-wide outbound request budget ({_limit}) exhausted before {source} request")
        _used += 1


def stats() -> dict[str, int | bool | None]:
    with _lock:
        return {"limit": _limit, "used": _used, "remaining": max(0, _limit - _used) if _limit is not None else None,
                "exhausted": bool(_limit is not None and _used >= _limit)}
