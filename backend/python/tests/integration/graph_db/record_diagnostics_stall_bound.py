"""The stall bound X of a records schema widening (measured by ``test_record_diagnostics_save_transaction_arango.py``).

X = the 99th-percentile duration of a connector save transaction, measured there under a connector-like workload, plus
1 s. The widening waits for the open stream transactions that wrote ``records`` and every later ``records`` write queues
behind it; the quiet-point check keeps that from happening, and X is what a write may wait in the case the check does
not find a quiet moment (``pipeshub_record_schema_rewiden_contended_total``).

That test prints the measured X; what it asserts is the number of round trips a save transaction holds open, which the
code controls (the duration is that number times the host's round-trip time).

The widening tests assert the same way: what a write waits for is the set of stream transactions running when the schema
change is sent, which the guard controls (``running_at_each_widening``); the waits themselves are printed."""

from typing import Any

import pytest

STALL_BOUND_S = 2.0


def running_at_each_widening(client: Any, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:  # noqa: ANN401
    """From now on, for each schema change ``client`` sends, the ids of the stream transactions the server lists right before it.
    The change waits for each of them and every later records write queues behind it, so an empty list is a change no write waits on."""
    sent: list[list[str]] = []
    update = client.update_collection_schema

    async def listed_then_update(name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        sent.append([t["id"] for t in await client.list_running_transactions()])
        return await update(name, schema, timeout)

    monkeypatch.setattr(client, "update_collection_schema", listed_then_update)
    return sent
