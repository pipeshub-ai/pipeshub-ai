"""Writes a record's name into the entity index as soon as a connector has saved the record.

A connector publishes a ``record-title-events`` message when it creates, renames or moves a
record (or removes one). This consumer turns those into the record's ``record`` entity point,
the same point indexing writes later (``EntityRecord.for_record``, same id), so a record can be
found by name before its content is indexed, and before a content backlog has cleared.

The message is a hint, never the source of truth: before writing, the record is read again from
the graph and the point is built from what is stored there. A replayed, forged or reordered
event therefore cannot resurrect a deleted record or write another org's data.

Events are buffered and written up to ``RECORD_TITLE_BATCH_SIZE`` at a time, one embedding call
per batch. The stock consumers hand a handler one message at a time, so the handler returns once
a message is buffered; a full batch is written before the handler that completed it returns (that
is the back-pressure), and a partial one after ``RECORD_TITLE_FLUSH_SECONDS``. The handler acknowledges a message once it is
buffered, so events still buffered when the process dies (at most ``RECORD_TITLE_FLUSH_SECONDS``
worth) are lost. A batch that cannot be written is retried, then published again to the same topic,
at most ``MAX_REQUEUES`` times per event, then dropped with a logged reason. What is lost or dropped
is only repaired by a rebuild of the entity index (a version or embedding model change).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org, spawn_detached
from app.models.entities import EntityRecord, EntityType
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from app.services.messaging.config import messaging_env
from app.services.messaging.record_titles import (
    DELETE,
    UPSERT,
    build_record_title_message,
    clean_record_title,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from logging import Logger

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.config import StreamMessage
    from app.services.messaging.worker_loop import WorkerLoop

CONSUMER_GROUP = "record_title_group"
CONSUMER_CLIENT = "record_title_client"

_RETRY_FIRST_S = 1.0
_RETRY_MAX_S = 15.0
MAX_REQUEUES = 5
_RECORD_FIELDS = ["id", "orgId", "recordName", "connectorId", "recordGroupId", "isDeleted"]


@dataclass(frozen=True)
class TitleEvent:
    org_id: str
    record_id: str
    delete: bool
    timestamp: int
    attempt: int = 0  # how many times a failed write has put this event back on the topic

    @property
    def rank(self) -> tuple[int, bool]:
        """Later wins; on the same timestamp a delete beats an upsert."""
        return (self.timestamp, self.delete)


def parse_title_event(payload: object) -> TitleEvent | None:
    """The event, or None when it is malformed. The name in it is not used, so it is not read."""
    if not isinstance(payload, dict):
        return None
    org_id, record_id, kind = payload.get("orgId"), payload.get("recordId"), payload.get("eventType")
    if not (isinstance(org_id, str) and org_id and isinstance(record_id, str) and record_id):
        return None
    if kind not in (UPSERT, DELETE):
        return None
    timestamp = payload.get("timestamp")
    attempt = payload.get("attempt")
    return TitleEvent(
        org_id, record_id, kind == DELETE,
        timestamp if isinstance(timestamp, int) and not isinstance(timestamp, bool) else 0,
        attempt if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt > 0 else 0,
    )


def _key_of(row: dict[str, Any]) -> str | None:
    key = row.get("id") or row.get("_key")
    return key if isinstance(key, str) and key else None


class RecordTitleWriter:
    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        get_store: Callable[[], Awaitable[EntityVectorStore | None]],
        worker: WorkerLoop | None = None,
        requeue: Callable[[list[dict[str, Any]]], Awaitable[bool]] | None = None,
        batch_size: int | None = None,
        flush_seconds: float | None = None,
        max_attempts: int | None = None,
        retry_first_seconds: float = _RETRY_FIRST_S,
    ) -> None:
        self.logger = logger
        self.graph = graph_provider
        self._get_store = get_store
        self._worker = worker
        self._requeue = requeue
        self.batch_size = max(1, batch_size or messaging_env.record_title_batch_size)
        self.flush_seconds = max(0.05, flush_seconds or messaging_env.record_title_flush_seconds)
        self.max_attempts = max(1, max_attempts or messaging_env.max_delivery_attempts)
        self._retry_first_seconds = retry_first_seconds
        self._pending: dict[tuple[str, str], TitleEvent] = {}
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    async def handle(self, message: StreamMessage) -> bool:
        """Buffer one event. Malformed ones are dropped (and logged), never retried."""
        event = parse_title_event(message.payload)
        if event is None:
            self.logger.warning("Dropping a malformed record title event")
            return True
        key = (event.org_id, event.record_id)
        held = self._pending.get(key)
        if held is None or event.rank >= held.rank:
            self._pending[key] = event
        if len(self._pending) >= self.batch_size:
            # In an empty context: the consumer binds the triggering message's org around this
            # handler, and a batch may hold other orgs. Awaited, so a full batch is still back-pressure.
            await asyncio.get_running_loop().create_task(self.flush(), context=contextvars.Context())
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._idle_task is None:
            self._idle_task = spawn_detached(self._flush_when_idle())

    async def aclose(self) -> None:
        """Stop the timer and write what is buffered, once."""
        task, self._idle_task = self._idle_task, None
        if task is not None:
            async with self._lock:  # an in-flight flush holds the lock: let it finish, it owns a popped batch
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        while self._pending:
            if not await self.flush(attempts=1):
                break

    async def _flush_when_idle(self) -> None:
        while True:
            await asyncio.sleep(self.flush_seconds)
            try:
                while self._pending:
                    await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.error("Record title flush failed", exc_info=True)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    async def flush(self, *, attempts: int | None = None) -> bool:
        """Write up to one batch. True when it was written (or there was nothing to write)."""
        async with self._lock:
            keys = list(self._pending)[: self.batch_size]
            batch = [self._pending.pop(key) for key in keys]
            if not batch:
                return True
            return await self._write_with_retry(batch, attempts or self.max_attempts)

    async def _write_with_retry(self, batch: list[TitleEvent], attempts: int) -> bool:
        delay = self._retry_first_seconds
        reason = ""
        for attempt in range(1, attempts + 1):
            try:
                reason = await self._on_worker(lambda: self._write(batch)) or ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"{type(exc).__name__}"
                self.logger.warning(
                    "Record title batch of %d failed (attempt %d/%d): %s", len(batch), attempt, attempts, exc,
                )
            if not reason:
                return True
            if attempt < attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RETRY_MAX_S)
        again = [e for e in batch if e.attempt < MAX_REQUEUES]
        self.logger.error(
            "Giving up on a batch of %d record title(s) after %d attempt(s): %s; "
            "putting %d back on the topic, dropping %d",
            len(batch), attempts, reason, len(again), len(batch) - len(again),
        )
        if again and self._requeue is not None:
            try:
                sent = await self._requeue([
                    build_record_title_message(
                        org_id=e.org_id, record_id=e.record_id, record_name=None, connector_id=None,
                        record_group_id=None, event_type=DELETE if e.delete else UPSERT, timestamp=e.timestamp,
                        attempt=e.attempt + 1,
                    )
                    for e in again
                ])
            except Exception:
                sent = False
            if not sent:
                self.logger.error("Could not put %d record title event(s) back on the topic; dropped", len(again))
        return False

    async def _on_worker(self, make: Callable[[], Coroutine[Any, Any, str]]) -> str:
        """Run on the loop the graph and vector clients are bound to."""
        if self._worker is None:
            return await make()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(make(), self._worker.start()))

    async def _write(self, batch: list[TitleEvent]) -> str:
        """Write one batch, org by org; returns "" when done, else why part of it was not written.

        Raises when the graph cannot be read: an unreadable record is not a missing one.
        """
        by_org: dict[str, list[TitleEvent]] = {}
        for event in batch:
            by_org.setdefault(event.org_id, []).append(event)
        reasons = []
        for org_id, events in by_org.items():
            with for_org(org_id, job="record_title"):
                reasons.append(await self._write_org(org_id, events))
        return "; ".join(r for r in reasons if r)

    async def _write_org(self, org_id: str, events: list[TitleEvent]) -> str:
        rows = await self._records([e.record_id for e in events])
        live: list[tuple[TitleEvent, dict[str, Any]]] = []
        deletes: list[str] = []
        for event in events:
            row = rows.get(event.record_id)
            if row is not None and row.get("orgId") != org_id:
                self.logger.warning(
                    "Dropping a record title event whose org differs from the record's | record=%s", event.record_id,
                )
                continue
            trashed = row is not None and bool(row.get("isDeleted"))
            if event.delete:
                # A live record keeps its point: the delete is stale.
                if row is None or trashed:
                    deletes.append(event.record_id)
            elif row is not None and not trashed:
                live.append((event, row))

        connectors = await self._live_connectors({r.get("connectorId") for _, r in live} - {None, ""})
        entities: list[EntityRecord] = []
        for event, row in live:
            connector_id = row.get("connectorId") or None
            name = clean_record_title(row.get("recordName"))
            if name is None or (connector_id and connector_id not in connectors):
                continue
            entities.append(EntityRecord.for_record(
                event.record_id, name, org_id, connector_id, row.get("recordGroupId") or None,
            ))
        if not entities and not deletes:
            return ""
        store = await self._get_store()
        if store is None:
            raise RuntimeError("no entity store")
        if entities:
            outcome = await store.upsert_entities_batch(entities, merge_membership=False)
            if outcome.failed:
                return f"{outcome.failed} of {len(entities)} not written"
        if deletes:
            await store.delete_entities(org_id, EntityType.RECORD.value, deletes)
        return ""

    async def _records(self, record_ids: list[str]) -> dict[str, dict[str, Any]]:
        rows = await self.graph.get_nodes_by_field_in(
            CollectionNames.RECORDS.value, "id", record_ids, return_fields=_RECORD_FIELDS, raise_on_error=True,
        )
        return {key: row for row in rows or [] if (key := _key_of(row))}

    async def _live_connectors(self, connector_ids: set[Any]) -> set[str]:
        """Connectors that exist and are not being deleted. A turned-off one counts: its records are
        still findable by name."""
        if not connector_ids:
            return set()
        rows = await self.graph.get_nodes_by_field_in(
            CollectionNames.APPS.value, "id", sorted(connector_ids), return_fields=["id", "status"],
            raise_on_error=True,
        )
        return {key for row in rows or [] if (key := _key_of(row)) and row.get("status") != APP_STATUS_DELETING}


class RecordTitleConsumer:
    """Stops the consumer, then writes what its handler still holds."""

    def __init__(
        self,
        consumer: Any,  # noqa: ANN401
        writer: RecordTitleWriter,
        close_producer: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.consumer = consumer
        self.writer = writer
        self._close_producer = close_producer

    async def start(self) -> None:
        await self.writer.start()
        await self.consumer.start(self.writer.handle)

    async def stop(self) -> None:
        try:
            await self.consumer.stop()
        finally:
            try:
                await self.writer.aclose()  # its last flush may still requeue
            finally:
                if self._close_producer is not None:
                    await self._close_producer()

    def is_running(self) -> bool:
        return bool(self.consumer.is_running())


async def build_record_title_consumer(
    app_container: Any,  # noqa: ANN401
    *,
    broker_type: Any,  # noqa: ANN401
    retry_manager: Any,  # noqa: ANN401
    worker: WorkerLoop | None,
    logger: Logger,
) -> RecordTitleConsumer:
    """The consumer of ``record-title-events``: a plain one of its own, outside the record lanes,
    the fair scheduler and the governor's slots, so a content backlog never holds a name back."""
    from app.modules.indexing.entity_index_rebuild import resolve_entity_store
    from app.services.messaging.config import ConsumerType, Topic
    from app.services.messaging.messaging_factory import MessagingFactory
    from app.services.messaging.utils import MessagingUtils

    graph_provider = getattr(app_container, "_graph_provider", None) or await app_container.graph_provider()
    config = await MessagingUtils.create_consumer_config(
        app_container, CONSUMER_CLIENT, CONSUMER_GROUP, [Topic.RECORD_TITLE_EVENTS.value],
    )
    consumer = MessagingFactory.create_consumer(
        broker_type=broker_type,
        logger=logger,
        config=config,
        consumer_type=ConsumerType.SIMPLE,
        retry_manager=retry_manager,
    )
    producer = None

    async def requeue(messages: list[dict[str, Any]]) -> bool:
        nonlocal producer
        if producer is None:  # made on first need, as in pipeline.first_use
            producer_config = await MessagingUtils.create_producer_config_from_service(
                app_container.config_service(), "record_title_requeue_producer",
            )
            new_producer = MessagingFactory.create_producer(logger=logger, config=producer_config)
            await new_producer.initialize()
            producer = new_producer  # cached only once it is usable
        acked = await producer.send_messages(
            Topic.RECORD_TITLE_EVENTS.value, [(m["payload"]["recordId"], m) for m in messages],
        )
        return all(acked)

    async def close_producer() -> None:
        if producer is not None:
            await producer.cleanup()

    return RecordTitleConsumer(
        consumer,
        RecordTitleWriter(
            logger=logger,
            graph_provider=graph_provider,
            get_store=lambda: resolve_entity_store(app_container),
            worker=worker,
            requeue=requeue,
        ),
        close_producer,
    )
