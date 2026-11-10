"""`run_async` from inside a running loop: the coroutine runs in a worker thread, in a copy of
the caller's context, so its log lines keep the call's ids (`app/agents/actions/utils.py`)."""

import asyncio

from app.agents.actions.utils import run_async
from app.utils.logging_fields import bind_log_fields, current_log_fields, log_fields


async def _fields() -> dict[str, object]:
    return dict(current_log_fields())


def test_the_callers_log_fields_reach_the_coroutine() -> None:
    async def caller() -> dict[str, object]:
        with log_fields(tool="s3__list", tool_call_id="c1"):
            return run_async(_fields())

    assert asyncio.run(caller()) == {"tool": "s3__list", "tool_call_id": "c1"}


def test_a_binding_made_in_the_coroutine_stays_there() -> None:
    async def binds() -> None:
        bind_log_fields(tool_call_id="inner")

    async def caller() -> dict[str, object]:
        with log_fields(tool_call_id="outer"):
            run_async(binds())
            return dict(current_log_fields())

    assert asyncio.run(caller()) == {"tool_call_id": "outer"}
