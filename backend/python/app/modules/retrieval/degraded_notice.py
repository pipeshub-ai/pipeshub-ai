"""Whether a search of this request was answered from a partial search.

Provider contract: a vector store that answers a query from less than its full search calls ``note(reason)`` (it needs no hits to do so,
so an empty answer can be flagged), or sets ``SearchResult.degraded`` on the hits it returns; the retrieval service turns the latter into
the former. The chat stream reads ``was_degraded`` when the run completes. A ContextVar holding a mutable list is shared by the tasks the
request spawns, so a search made by a tool in another task is seen too; outside a tracked request ``note`` does nothing.
"""
import contextlib
import functools
from collections.abc import AsyncGenerator, Callable
from contextvars import ContextVar
from typing import Any, ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")

_seen: ContextVar[list[str] | None] = ContextVar("search_degraded", default=None)
_access: ContextVar[list[dict[str, str]] | None] = ContextVar("access_notices", default=None)
_apps: ContextVar[dict[str, dict[str, Any] | None] | None] = ContextVar("access_notice_apps", default=None)


def track() -> None:
    """Start tracking for this request (a no-op when it already is)."""
    if _seen.get() is None:
        _seen.set([])
        _access.set([])
        _apps.set({})


def scoped(stream: Callable[_P, AsyncGenerator[_T, None]]) -> Callable[_P, AsyncGenerator[_T, None]]:
    """Give each run of an async-generator entry point (a chat stream) its own scope, ended when the stream ends.

    ``track`` would reuse the list of an earlier run in the same task, so a partial search in one answer would mark the next one. The list
    stays shared within the run, so tasks it spawns can still report.
    """
    @functools.wraps(stream)
    async def run(*args: _P.args, **kwargs: _P.kwargs) -> AsyncGenerator[_T, None]:
        token, access_token, apps_token = _seen.set([]), _access.set([]), _apps.set({})
        try:
            # aclosing: a wrapper that is closed (a client disconnect) must close the run it wraps now, so the run's own teardown
            # (its task registry, its timing logs) runs where it always did, not whenever the inner generator is collected.
            async with contextlib.aclosing(stream(*args, **kwargs)) as run_events:
                async for item in run_events:
                    yield item
        finally:
            # ValueError: finalised from another context (an abandoned stream), which ends with it
            with contextlib.suppress(ValueError):
                _seen.reset(token)
                _access.reset(access_token)
                _apps.reset(apps_token)

    return run


def note(reason: str = "partial") -> None:
    if (seen := _seen.get()) is not None:
        seen.append(reason)


def note_access(notices: list[dict[str, str]]) -> None:
    """Remember the access notices of a search of this request, so the final answer can carry them (each once)."""
    if (seen := _access.get()) is not None:
        seen.extend(n for n in notices if n not in seen)


def app_memo() -> dict[str, dict[str, Any] | None] | None:
    """The connector documents this request or run has read for its notices (None for an id the graph did not return), or None outside a
    tracked request: a run that searches many times reads each connector once."""
    return _apps.get()


def access_notices() -> list[dict[str, str]]:
    return list(_access.get() or [])


def count() -> int:
    """How many times this request has been noted, so a caller can tell whether its own search was."""
    return len(_seen.get() or ())


def was_degraded() -> bool:
    return bool(_seen.get())


def flag(completion_data: dict[str, Any]) -> dict[str, Any]:
    """Mark a final answer whose request had a degraded search (the UI shows a note)."""
    if was_degraded():
        completion_data["searchDegraded"] = True
    if notices := access_notices():
        completion_data["accessNotices"] = notices
    return completion_data
