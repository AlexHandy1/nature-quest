import threading
from datetime import date
from typing import TypedDict


class _BudgetState(TypedDict):
    date: date | None
    count: int


DAILY_NARRATION_CALL_CAP = 200

_lock = threading.Lock()
_state: _BudgetState = {"date": None, "count": 0}


def try_consume_daily_budget(today: date) -> bool:
    with _lock:
        _reset_if_new_day(today)
        if _state["count"] >= DAILY_NARRATION_CALL_CAP:
            return False
        _state["count"] += 1
        return True


def reset() -> None:
    with _lock:
        _state["date"] = None
        _state["count"] = 0


def _reset_if_new_day(today: date) -> None:
    if _state["date"] != today:
        _state["date"] = today
        _state["count"] = 0
