"""NEKO: N16 planner-progress — request-local observation, never retry policy."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TypeVar

import asyncio

_T = TypeVar("_T")
_observer: ContextVar[Callable[[str, str], None] | None] = ContextVar("llm_progress", default=None)


def report_progress(request_type: str, phase: str, model: str = "") -> None:
    # NEKO: N16 planner-progress — compaction/Replyer must not overwrite Planner status.
    observer = _observer.get()
    if observer is not None and request_type == "maisaka.planner":
        observer(phase, model[:100])


async def observe_planner(operation: Awaitable[_T], callback: Callable[[str, str], None]) -> _T:
    # NEKO: N16 planner-progress — inherited child contexts become inert on every exit.
    active = True

    def notify(phase: str, model: str) -> None:
        if active:
            try:
                callback(phase, model)
            except Exception:
                pass  # Display failures must never change model execution.

    token = _observer.set(notify)
    try:
        result = await operation
    except asyncio.CancelledError:
        notify("已取消", "")
        raise
    except Exception:
        notify("请求失败", "")
        raise
    else:
        notify("响应已返回", "")
        return result
    finally:
        active = False
        _observer.reset(token)
