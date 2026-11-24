"""The indexing flows F1-F22 through the production write paths on a real store.

Each step calls the function the product calls, in the order the product calls it: the connector save's stamp
(``DataSourceEntitiesProcessor._stamp_queued_at`` + an entity upsert), the compare-and-set to QUEUED, the bulk status
writer of a manual reindex and of a connector reindex-failed, ``write_attempt_start``, the retry revert, the cut
requeue, the rate-limit deferral, the terminal FAILED write, the sink-only end, ``restore_records`` and the
connector re-enable. Expected values come from a table written from the flow descriptions: "the start is the time of
step k", "the attempts are n", "failedAt is the time of step j".
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import Record
from app.modules.indexing.rate_limit_deferral import deferral_updates, plan_deferral
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    base_record,
    run_fields,
)

NS, Q, IP = ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value, ProgressStatus.IN_PROGRESS.value
OK, FAIL, OFF = ProgressStatus.COMPLETED.value, ProgressStatus.FAILED.value, ProgressStatus.AUTO_INDEX_OFF.value
REV = "rev-1"


class Flow:
    """One record and the steps applied to it. ``marks[i]`` is the (before, after) clock of step ``i`` (1-based)."""

    def __init__(self, store: Store, *, sink_clears: bool = True) -> None:
        self.s = store
        self.key = store.key("flow")
        self.marks: list[tuple[int, int]] = []
        self.sink_clears = sink_clears
        self.connector_id = f"{store.org}-c"

    async def step(self, name: str, *args: Any, **kw: Any) -> None:  # noqa: ANN401
        before = get_epoch_timestamp_in_ms()
        await asyncio.sleep(0.004)
        await getattr(self, f"op_{name}")(*args, **kw)
        await asyncio.sleep(0.004)
        self.marks.append((before, get_epoch_timestamp_in_ms()))
        await asyncio.sleep(0.004)

    async def run(self, *names: str | tuple[str, ...]) -> Flow:
        for n in names:
            if isinstance(n, tuple):
                await self.step(n[0], *n[1:])
            else:
                await self.step(n)
        return self

    def time_of(self, step: int) -> tuple[int, int]:
        return self.marks[step - 1]

    async def fields(self) -> dict[str, Any]:
        return run_fields(await self.s.doc(self.key))

    async def status(self) -> str | None:
        return (await self.s.doc(self.key)).get("indexingStatus")

    # ---- the production writes ----------------------------------------------------------------------------

    async def _upsert_entity(self, entity: Record) -> None:
        doc = entity.to_arango_base_record()
        if self.s.name != "postgres":
            doc = {**{k: v for k, v in doc.items() if k != "_key"}, "id": doc["_key"]}
        assert await self.s.p.batch_upsert_nodes([doc], RECORDS)

    async def op_save_new(self) -> None:
        """A connector save of a record the store has never seen."""
        entity = Record.from_arango_base_record(base_record(self.key, self.s.org, connectorId=self.connector_id))
        entity.indexing_status = NS
        DataSourceEntitiesProcessor._stamp_queued_at(entity, None)
        await self._upsert_entity(entity)

    async def op_save_existing(self, status: str = NS) -> None:
        """A connector save of a record that is stored: it is stamped from the stored record it replaces."""
        stored = Record.from_arango_base_record({**await self.s.doc(self.key), "_key": self.key})
        entity = Record.from_arango_base_record(base_record(self.key, self.s.org, connectorId=self.connector_id))
        entity.indexing_status = status
        DataSourceEntitiesProcessor._stamp_queued_at(entity, stored)
        await self._upsert_entity(entity)

    async def op_cas_queued(self) -> None:
        await self.s.p.compare_and_set_indexing_status([self.key], NS, Q)

    async def op_bulk(self, status: str = NS) -> None:
        """The manual reindex and the connector reindex-failed reset the status with the bulk writer."""
        await self.s.p.update_indexing_status_for_record_ids([self.key], status)

    async def op_reset_connector(self) -> None:
        """The Drive unreadable reset."""
        await self.s.p.reset_indexing_status_for_connector(self.connector_id, NS, only_statuses=[FAIL])

    def _event_processor(self) -> Any:  # noqa: ANN401
        """The real event processor over this store's provider: the attempt start is its ``mark_record_status``."""
        import logging

        from app.events.events import EventProcessor

        processor = EventProcessor.__new__(EventProcessor)
        processor.logger = logging.getLogger("flows")
        processor.graph_provider = self.s.p
        return processor

    async def op_attempt(self, rev: str = REV) -> None:
        doc = {"_key": self.key}
        await self._event_processor().mark_record_status(doc, ProgressStatus.IN_PROGRESS, content_rev=rev, parsing_starts=True)

    async def op_revert(self) -> None:
        """The retry revert of the record handler: the fields come from the handler's own function."""
        from app.services.messaging.kafka.handlers.record import requeue_updates

        updates = requeue_updates(await self.s.doc(self.key), store_only=False)
        assert updates, "the record is not IN_PROGRESS"
        await self.s.p.update_node(self.key, RECORDS, {**updates, "processingStartedAt": None})

    async def op_cut(self) -> None:
        """The cut requeue of the embedding stage: the real conditional writer of the event processor."""
        await self.s.raw_set(self.key, {"embedDeliveryId": "d1"})
        assert await self._event_processor()._cut_writer(self.key, "d1")(1.0, 0)

    async def op_defer(self) -> None:
        current = await self.s.doc(self.key)
        now = get_epoch_timestamp_in_ms()
        plan = plan_deferral(current, 60, now_ms=now, max_total_seconds=86400)
        await self.s.p.update_node(self.key, RECORDS, deferral_updates(plan, reason="paused", record=current, now_ms=now))

    async def op_stale_recovery(self) -> None:
        """Stale recovery requeues without any fill (that file is left unchanged here)."""
        await self.s.p.update_node(self.key, RECORDS, {"indexingStatus": Q, "queuedAtTimestamp": get_epoch_timestamp_in_ms(), "processingStartedAt": None})

    async def op_complete(self) -> None:
        await self.s.p.update_node(self.key, RECORDS, {"indexingStatus": OK, "lastIndexTimestamp": get_epoch_timestamp_in_ms(), "processingStartedAt": None})

    async def op_fail(self) -> None:
        """The terminal failure of the record handler (its own status writer)."""
        from tests.support.record_handler import make_record_handler

        handler = make_record_handler()
        handler.event_processor.graph_provider = self.s.p
        assert await handler._RecordEventHandler__update_document_status(self.key, FAIL, FAIL, "failed")

    async def op_fail_by_older_process(self) -> None:
        """An older process marks FAILED and stamps nothing about the run."""
        await self.s.raw_set(self.key, {"indexingStatus": FAIL})

    async def op_reset_by_older_process(self) -> None:
        """An older process resets to NOT_STARTED and leaves the closed run's start where it was."""
        await self.s.raw_set(self.key, {"indexingStatus": NS})

    async def op_sink_end(self) -> None:
        """The sink-only end: the fields are the orchestrator's own; with ``sink_clears`` off the clear is left out (control)."""
        from app.modules.transformers.sink_orchestrator import sink_only_end_fields

        fields = sink_only_end_fields(self.key, None)
        if not self.sink_clears:
            fields.pop("firstQueuedAt")
        assert await self.s.p.batch_update_nodes([fields], RECORDS)

    async def op_trash(self) -> None:
        self.batch = uuid.uuid4().hex[:8]
        await self.s.p.update_node(self.key, RECORDS, {"isDeleted": True, "deleteBatchId": self.batch})

    async def op_restore(self, requeue: bool = True) -> None:
        now = get_epoch_timestamp_in_ms()
        fields = {"indexingStatus": NS, "queuedAtTimestamp": now, "restoredAtTimestamp": now} if requeue else {}
        await asyncio.sleep(0.004)  # the statement's own clock is later than the caller's, as it is in the product
        restored = await self.s.p.restore_records([{"id": self.key, "set": fields}], self.batch)
        assert restored == [self.key], restored

    async def op_restore_stale(self) -> None:
        """The control of F21: a restore that classifies from the stored status keeps the run it had."""
        before = await self.s.doc(self.key)
        await self.op_restore()
        await self.s.raw_set(self.key, {"firstQueuedAt": before.get("firstQueuedAt"), "indexingAttempts": before.get("indexingAttempts")})

    async def op_connector_off(self) -> None:
        await self.s.p.update_node(self.key, RECORDS, {"indexingStatus": OFF})

    async def op_connector_on(self) -> None:
        stored = await self.s.doc(self.key)
        now = get_epoch_timestamp_in_ms()
        await self.s.p.update_node(self.key, RECORDS, EntityEventService._requeued_fields(stored, now))

    async def op_chat_attachment(self) -> None:
        """A chat attachment: the record is the upload route's own."""
        import types

        from app.api.routes.chatbot import attachment_record_doc

        item = types.SimpleNamespace(fileName="a.pdf", mimeType="application/pdf", size=3)
        doc = attachment_record_doc(record_id=self.key, org_id=self.s.org, item=item, external_record_id=self.key, virtual_record_id=self.key,
                                    now=get_epoch_timestamp_in_ms())
        doc["connectorId"] = self.connector_id
        if self.s.name == "postgres":
            doc.pop("id")
        assert await self.s.p.batch_upsert_nodes([doc], RECORDS)

    async def op_seed_complete(self) -> None:
        """A record that was indexed before the run fields existed."""
        await self.s.put(self.key, indexingStatus=OK, lastIndexTimestamp=get_epoch_timestamp_in_ms() - 100_000, connectorId=self.connector_id)

    async def op_seed_queued_before_the_fields(self) -> None:
        await self.s.put(self.key, indexingStatus=Q, queuedAtTimestamp=get_epoch_timestamp_in_ms() - 5000, connectorId=self.connector_id)


def within(value: Any, marks: tuple[int, int]) -> bool:  # noqa: ANN401
    return isinstance(value, (int, float)) and marks[0] <= value <= marks[1]


# Each flow: (steps, expectation). Expectation: start = the step whose time is firstQueuedAt (None: absent), attempts, failed = step
FLOWS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "F1": (("save_new", "cas_queued", "attempt", "complete"), {"start": 1, "attempts": 1}),
    "F2": (("save_new", "cas_queued", "attempt", "revert", "attempt", "revert", "attempt", "complete"), {"start": 1, "attempts": 3}),
    "F3": (("save_new", "cas_queued", "attempt", "fail"), {"start": 1, "attempts": 1, "failed": 4}),
    "F4": (("save_new", "cas_queued", "attempt", "fail", ("bulk", NS), "cas_queued", "attempt"), {"start": 5, "attempts": 1, "failed": 4}),
    "F5": (("save_new", "cas_queued", "attempt", "fail", ("bulk", NS), "attempt", "cas_queued"), {"start": 5, "attempts": 1, "failed": 4}),
    "F6": (("save_new", "cas_queued", "attempt", "fail", ("bulk", NS), "save_existing", "attempt"), {"start": 5, "attempts": 1, "failed": 4}),
    "F7": (("save_new", "cas_queued", "attempt", "fail", "reset_connector", "cas_queued", "attempt"), {"start": 5, "attempts": 1, "failed": 4}),
    "F8": (("save_new", "cas_queued", "attempt", "complete", "attempt"), {"start": 5, "attempts": 1}),
    "F9": (("save_new", "cas_queued", "attempt", "sink_end", "attempt"), {"start": 5, "attempts": 1}),
    "F10": (("save_new", "cas_queued", ("save_existing", Q)), {"start": 1, "attempts": 0}),
    "F11": (("save_new", "cas_queued", "attempt", "complete", ("save_existing", NS)), {"start": 5, "attempts": 0}),
    "F12": (("save_new", "cas_queued", "attempt", "complete", "connector_off", "connector_on", "attempt"), {"start": 6, "attempts": 1}),
    "F13": (("save_new", "cas_queued", "attempt", "defer", "attempt", "defer", "attempt", "complete"), {"start": 1, "attempts": 3}),
    "F14": (("save_new", "cas_queued", "attempt", "stale_recovery", "attempt"), {"start": 1, "attempts": 2}),
    "F15": (("save_new", "cas_queued", "attempt", "cut", "attempt"), {"start": 1, "attempts": 2}),
    "F16": (("save_new", "cas_queued", "attempt", "complete", "trash", "restore", "attempt"), {"start": 6, "attempts": 1}),
    "F17": (("chat_attachment", "attempt"), {"start": 1, "attempts": 1}),
    "F21": (("save_new", "cas_queued", "trash", "restore", "attempt"), {"start": 4, "attempts": 1}),
    "F22": (("save_new", "cas_queued", "attempt", "complete", "reset_by_older_process", "cas_queued", "attempt"), {"start": 6, "attempts": 1}),
}

# the stored state before step 1 for the flows that start from a record that exists
SEEDS: dict[str, str] = {}


def check(flow: Flow, fields: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    problems = []
    start = expect["start"]
    if start is None:
        if "firstQueuedAt" in fields:
            problems.append(f"firstQueuedAt should be absent, is {fields['firstQueuedAt']}")
    elif not within(fields.get("firstQueuedAt"), flow.time_of(start)):
        problems.append(f"firstQueuedAt {fields.get('firstQueuedAt')} is not the time of step {start} {flow.time_of(start)}")
    if fields.get("indexingAttempts") != expect["attempts"]:
        problems.append(f"indexingAttempts {fields.get('indexingAttempts')} != {expect['attempts']}")
    if "failed" in expect and not within(fields.get("failedAt"), flow.time_of(expect["failed"])):
        problems.append(f"failedAt {fields.get('failedAt')} is not the time of step {expect['failed']}")
    if "failed" not in expect and "failedAt" in fields:
        problems.append(f"failedAt should be absent, is {fields['failedAt']}")
    return problems
