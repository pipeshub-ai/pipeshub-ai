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

from typing import Any

import pytest

STALL_BOUND_S = 2.0


def running_at_each_widening(client: Any, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:  # noqa: ANN401
    """From now on, for each schema change ``client`` sends, the ids of the stream transactions the server lists right before it.

    The change waits for every transaction open when it reaches the server, and every later records write queues behind it.
    The list is a separate request, so a transaction that begins between it and the change is not in it: an empty list says
    the guard sent the change with nothing open after its quiet point, not that none began before the change arrived (the
    guard does not hold new transactions back from other processes). A test asserting on an empty list keeps new
    transactions out of that window itself."""
    sent: list[list[str]] = []
    update = client.update_collection_schema

    async def listed_then_update(name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        sent.append([t["id"] for t in await client.list_running_transactions()])
        return await update(name, schema, timeout)

    monkeypatch.setattr(client, "update_collection_schema", listed_then_update)
    return sent
