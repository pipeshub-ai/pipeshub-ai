"""Upload-and-wait pieces shared by the resilience tests.

    RESILIENCE_RECOVERY_TIMEOUT_SEC  how long indexing may take to recover (default 900)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Collection
from typing import Any

from helper.clients.kb_client import KBClient

RECOVERY_TIMEOUT = int(os.getenv("RESILIENCE_RECOVERY_TIMEOUT_SEC", "900"))
POLL = 5
# UNKNOWN: the record was read but carried no status yet, so it is still in flight.
UNFINISHED = {"NOT_STARTED", "QUEUED", "IN_PROGRESS", "UNKNOWN"}


def document(token: str) -> bytes:
    # One shape for every document, so each chunks the same way; only the token
    # differs. Long enough that indexing a batch takes a while.
    sections = "\n\n".join(
        f"## Section {n}\n\nOperating note {n} for batch {token}. " + "Routine maintenance detail. " * 12
        for n in range(1, 31)
    )
    return f"# Runbook {token}\n\n{sections}\n".encode()


def record_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("record") or payload.get("data", {}).get("record") or payload


def statuses(kb_client: KBClient, record_ids: list[str]) -> dict[str, str]:
    return {
        record_id: record_fields(kb_client.get_record(record_id)).get("indexingStatus") or "UNKNOWN"
        for record_id in record_ids
    }


async def wait_until_finished(
    kb_client: KBClient,
    record_ids: list[str],
    *,
    reindexed: Collection[str] = (),
    timeout: float | None = None,
    poll: float = POLL,
    read: Callable[[KBClient, list[str]], dict[str, str]] = statuses,
) -> dict[str, str]:
    """Poll until every record has finished, and return the last statuses seen.

    A record in ``reindexed`` was re-queued after it had already FAILED, so the
    FAILED it shows at first is the old verdict, not the outcome of the retry:
    it counts as finished only once it has left FAILED at least once, or has
    reached COMPLETED. A failed status read (the stack may still be recovering)
    is retried until the deadline, then raised.
    """
    deadline = asyncio.get_event_loop().time() + (RECOVERY_TIMEOUT if timeout is None else timeout)
    waiting_for_retry = set(reindexed)
    current: dict[str, str] = {}
    last_error: Exception | None = None
    while True:
        try:
            current = read(kb_client, record_ids)
            last_error = None
        except Exception as exc:  # noqa: BLE001 - retried until the deadline, then raised
            last_error = exc
        else:
            waiting_for_retry -= {r for r in waiting_for_retry if current.get(r) != "FAILED"}
            if not waiting_for_retry and not UNFINISHED & set(current.values()):
                return current
        if asyncio.get_event_loop().time() >= deadline:
            if last_error is not None:
                raise last_error
            return current
        await asyncio.sleep(poll)
