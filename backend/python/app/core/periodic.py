"""Periodic background jobs whose every tick is its own unit of work in the logs."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import NoReturn

from app.core.org_context import spawn_detached
from app.utils.error_class import log_failure
from app.utils.logging_fields import bind_log_fields, log_event, reset_log_fields
from app.utils.request_context import new_system_root, reset_context, set_context

_logger = logging.getLogger("app.jobs")


async def _tick(name: str, fn: Callable[[], Awaitable[object]]) -> None:
    context_token = set_context(new_system_root(name))
    fields_token = bind_log_fields(job=name)
    start = time.monotonic()
    try:
        await fn()
    except Exception as exc:
        log_failure(_logger, "job.tick.end", exc, duration_ms=round((time.monotonic() - start) * 1000))
    else:
        log_event(_logger, "job.tick.end", level=logging.DEBUG, outcome="success",
                  duration_ms=round((time.monotonic() - start) * 1000))
    finally:
        reset_log_fields(fields_token)
        reset_context(context_token)


async def run_periodic(name: str, interval_s: float, fn: Callable[[], Awaitable[object]]) -> NoReturn:
    """Run ``fn`` now and then every ``interval_s`` seconds after it returns, forever.

    Each tick runs in a fresh context with its own ``sys-<name>-`` root and ``job=<name>``,
    so nothing a tick binds reaches the next one, and ends with ``job.tick.end`` (DEBUG on
    success, the failure's level otherwise). A failing tick does not stop the loop;
    cancelling it does. Start it with ``spawn_detached``.
    """
    while True:
        # _tick replaces the detached root with its own, on purpose: a sync started in a tick keeps the tick's root.
        await spawn_detached(_tick(name, fn), name=f"{name}:tick")
        await asyncio.sleep(interval_s)
