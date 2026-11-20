"""The stall bound X of a records schema widening (measured by ``test_record_diagnostics_save_transaction_arango.py``).

X = the 99th-percentile duration of a connector save transaction, measured there under a connector-like workload, plus
1 s. The widening waits for the open stream transactions that wrote ``records`` and every later ``records`` write queues
behind it; the quiet-point check keeps that from happening, and X is what a write may wait in the case the check does
not find a quiet moment (``pipeshub_record_schema_rewiden_contended_total``).

That test prints the measured X; what it asserts is the number of round trips a save transaction holds open, which the
code controls (the duration is that number times the host's round-trip time).

The widening tests assert the same way: what a write waits for is the set of stream transactions open when the schema
change is sent, and the guard decides when to send it; ``running_at_each_widening`` records what the server lists right
before each change. The waits themselves are printed."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

STALL_BOUND_S = 2.0


def running_at_each_widening(client: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], list[int]]:  # noqa: ANN401
    """From now on, for each schema change ``client`` sends, the ids of the stream transactions the server lists right before
    it, and how many begin requests were still unanswered when the change came up (waited out before the list).

    The change waits for every transaction open when it reaches the server, and every later records write queues behind it.
    The list is a request of its own, so ``client``'s begin requests are held from the moment the change comes up until it
    returns, and the ones already sent are answered before the list is taken: none of ``client``'s transactions can begin
    between the list and the change, and an empty list is a change sent with none of them open. Another client's
    transactions are not held (the guard itself never holds them back)."""
    sent: list[list[str]] = []
    waited: list[int] = []
    update, get_session = client.update_collection_schema, client._get_session
    begins_open = asyncio.Event()
    begins_open.set()
    all_answered = asyncio.Event()
    all_answered.set()
    unanswered = 0

    @contextlib.asynccontextmanager
    async def held_begin(request: Any) -> AsyncIterator[Any]:  # noqa: ANN401
        nonlocal unanswered
        while not begins_open.is_set():  # a begin woken by one change can meet the next one closed
            await begins_open.wait()
        unanswered += 1
        all_answered.clear()
        try:
            async with request as resp:
                yield resp
        finally:
            unanswered -= 1
            if not unanswered:
                all_answered.set()

    class Session:
        def __init__(self, session: Any) -> None:  # noqa: ANN401
            self._session = session

        def __getattr__(self, name: str) -> Any:  # noqa: ANN401
            return getattr(self._session, name)

        def post(self, url: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            request = self._session.post(url, *args, **kwargs)
            return held_begin(request) if url.endswith("/_api/transaction/begin") else request

    async def session() -> Session:
        return Session(await get_session())

    async def listed_then_update(name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        begins_open.clear()
        try:
            waited.append(unanswered)
            await all_answered.wait()
            sent.append([t["id"] for t in await client.list_running_transactions()])
            return await update(name, schema, timeout)
        finally:
            begins_open.set()

    monkeypatch.setattr(client, "_get_session", session)
    monkeypatch.setattr(client, "update_collection_schema", listed_then_update)
    return sent, waited
