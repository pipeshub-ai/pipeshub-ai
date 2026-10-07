"""When a record event is skipped because its connector is off or gone.

``RecordEventHandler.process_event`` and the consumers' read-time filter both
decide with the functions here, so the rule cannot drift between them: the
handler applies them one step at a time while it processes an event, and
``read_time_outcome`` replays the same steps, in the same order, to tell
whether an event would end in nothing but that skip. Only those events are
settled at read time; anything the handler would do more with goes through
the normal path.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    RECONCILIATION_ENABLED_EXTENSIONS,
    RECONCILIATION_ENABLED_MIME_TYPES,
    CollectionNames,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.services.graph_db.common.record_visibility import is_live_record
from app.services.messaging.connector_off import ConnectorOffResult
from app.utils.user_errors import CONNECTOR_OFF

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.config import StreamMessage

_CONNECTOR_GATED_EVENTS = frozenset({
    EventTypes.NEW_RECORD.value,
    EventTypes.REINDEX_RECORD.value,
    EventTypes.UPDATE_RECORD.value,
})
# After a removed connector's skip the handler's finally block promotes the
# record's queued copies when it reads one of these, so such an event is not
# only a skip.
_STATUSES_THAT_RELEASE_COPIES = frozenset({
    ProgressStatus.COMPLETED.value,
    ProgressStatus.EMPTY.value,
    ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
})


class ConnectorState(Enum):
    ACTIVE = "active"
    OFF = "off"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class ReadTimeOutcome(Enum):
    PASS = "pass"
    # The handler acknowledges without writing anything.
    SETTLE = "settle"
    # The handler writes AUTO_INDEX_OFF / CONNECTOR_OFF and acknowledges.
    SETTLE_CONNECTOR_OFF = "settle_connector_off"


def connector_state(instance: Mapping[str, Any] | None) -> ConnectorState:
    """State of a connector app document read with ``raise_on_error=True``."""
    if not instance:
        return ConnectorState.REMOVED
    return ConnectorState.ACTIVE if instance.get("isActive", False) else ConnectorState.OFF


def connector_gated_event(event_type: str, payload: Mapping[str, Any]) -> bool:
    """Whether the handler checks the record's connector before indexing this
    event. vectorDbOnly opts out: the vector-store rebuild deliberately
    re-embeds disabled connectors from blob."""
    return event_type in _CONNECTOR_GATED_EVENTS and not payload.get("vectorDbOnly")


def clears_embeddings_first(event_type: str, payload: Mapping[str, Any]) -> bool:
    """Whether the handler deletes the record's embeddings before anything
    else: an update or reindex of a type that is not reconciled block by block."""
    if payload.get("vectorDbOnly") or event_type not in (
        EventTypes.UPDATE_RECORD.value,
        EventTypes.REINDEX_RECORD.value,
    ):
        return False
    return not (
        payload.get("mimeType", "unknown") in RECONCILIATION_ENABLED_MIME_TYPES
        or payload.get("extension", "unknown") in RECONCILIATION_ENABLED_EXTENSIONS
    )


def enrichment_cut_short(record: Mapping[str, Any]) -> bool:
    return record.get("extractionStatus") == ProgressStatus.IN_PROGRESS.value


def resuming_enrichment(record: Mapping[str, Any]) -> bool:
    return (
        enrichment_cut_short(record)
        and record.get("indexingStatus") == ProgressStatus.COMPLETED.value
    )


def already_indexed(event_type: str, payload: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    """The guard that stops a replayed newRecord from re-running the pipeline
    over an indexed corpus; an explicit forceReindex opts out of it."""
    return (
        not payload.get("forceReindex")
        and not enrichment_cut_short(record)
        and event_type in (EventTypes.NEW_RECORD.value, EventTypes.REINDEX_RECORD.value)
        and record.get("indexingStatus") == ProgressStatus.COMPLETED.value
    )


def gating_connector_id(record: Mapping[str, Any]) -> str | None:
    """The connector whose state decides whether this record is indexed."""
    connector_id = record.get("connectorId")
    if connector_id and record.get("origin") == OriginTypes.CONNECTOR.value:
        return str(connector_id)
    return None


def connector_off_updates(record: Mapping[str, Any]) -> dict[str, Any]:
    """The fields the handler writes on a record whose connector is off."""
    updates: dict[str, Any] = {
        "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
        "extractionStatus": record.get("extractionStatus", ProgressStatus.NOT_STARTED.value),
        "processingStartedAt": None,
        "reason": CONNECTOR_OFF,
    }
    if record.get("parsingStatus") == ProgressStatus.IN_PROGRESS.value:
        updates["parsingStatus"] = ProgressStatus.AUTO_INDEX_OFF.value
    return updates


def read_time_outcome(
    event_type: str,
    payload: Mapping[str, Any],
    record: Mapping[str, Any] | None,
    state_of: Callable[[str], ConnectorState],
) -> ReadTimeOutcome:
    """What the handler would do with an event of a connector known to be off
    or gone, given the record as it stands; PASS unless that is only the skip.

    The steps are the handler's own, in its order. An IN_PROGRESS record is
    left to the normal path: a delivery elsewhere may hold its lease, and only
    the handler takes that lease before writing. So is one whose status
    another event of the same record may still depend on (see below).
    """
    if not connector_gated_event(event_type, payload):
        return ReadTimeOutcome.PASS
    if record is None:
        # The handler acknowledges a missing record either way; settling it
        # here is kept to removed connectors, whose records are deleted with
        # them, so a record read that silently misses cannot drop live work.
        connector_id = payload.get("connectorId")
        removed = bool(connector_id) and state_of(str(connector_id)) is ConnectorState.REMOVED
        return ReadTimeOutcome.SETTLE if removed else ReadTimeOutcome.PASS
    if not is_live_record(record):
        return ReadTimeOutcome.SETTLE
    if clears_embeddings_first(event_type, payload) or already_indexed(event_type, payload, record):
        return ReadTimeOutcome.PASS
    connector_id = gating_connector_id(record)
    if connector_id is None:
        return ReadTimeOutcome.PASS
    state = state_of(connector_id)
    if state not in (ConnectorState.OFF, ConnectorState.REMOVED) or resuming_enrichment(record):
        return ReadTimeOutcome.PASS
    status = record.get("indexingStatus")
    if state is ConnectorState.REMOVED:
        return (
            ReadTimeOutcome.PASS
            if status in _STATUSES_THAT_RELEASE_COPIES
            else ReadTimeOutcome.SETTLE
        )
    # An event of the same record still buffered here, or behind this one,
    # may need the status as it is: a COMPLETED record's newRecord releases
    # its queued copies. Overwriting it first would lose that, so only a status
    # no other path keys on is overwritten here.
    if (
        not isinstance(status, str)
        or status == ProgressStatus.IN_PROGRESS.value
        or status in _STATUSES_THAT_RELEASE_COPIES
    ):
        return ReadTimeOutcome.PASS
    return ReadTimeOutcome.SETTLE_CONNECTOR_OFF


class GraphConnectorOffFilter:
    """``ConnectorOffFilter`` backed by ``IGraphDBProvider``.

    Only "this connector is on" is remembered, for ``refresh_seconds``: it is
    what lets a batch of live connectors' events through with no graph call.
    Off or removed is read afresh for every batch that has such events, so
    turning a connector back on takes effect at once. Per batch with candidates
    that is one connector read, one record read, and for the turned-off ones
    one conditional write and one more connector read to confirm they are
    still off.

    A failed or slow read settles nothing and pauses the filter for
    ``refresh_seconds`` (at least ``_MIN_PAUSE_SECONDS``), so an unreachable
    graph costs the read loop one timeout per pause rather than one per batch.
    """

    _MIN_PAUSE_SECONDS = 30.0

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        logger: Logger,
        refresh_seconds: float,
        *,
        read_timeout_seconds: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._graph = graph_provider
        self._logger = logger
        self._refresh_seconds = refresh_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._clock = clock
        self._active_until: dict[str, float] = {}
        self._paused_until = 0.0

    def _known_active(self, connector_id: str) -> bool:
        until = self._active_until.get(connector_id)
        return until is not None and until > self._clock()

    def _pause(self, what: str, error: Exception) -> None:
        pause = max(self._refresh_seconds, self._MIN_PAUSE_SECONDS)
        self._paused_until = self._clock() + pause
        self._logger.warning(
            "Could not %s; queued events of turned-off connectors go through the "
            "normal path for the next %.0fs: %r", what, pause, error,
        )

    async def _read_states(self, connector_ids: set[str]) -> dict[str, ConnectorState]:
        docs = await asyncio.wait_for(
            self._graph.get_nodes_by_field_in(
                CollectionNames.APPS.value,
                "id",
                sorted(connector_ids),
                return_fields=["id", "isActive"],
                raise_on_error=True,
            ),
            timeout=self._read_timeout_seconds,
        )
        found = {str(doc["id"]): doc for doc in docs or [] if doc and doc.get("id")}
        states = {c: connector_state(found.get(c)) for c in connector_ids}
        now = self._clock()
        for connector_id, state in states.items():
            if state is ConnectorState.ACTIVE:
                self._active_until[connector_id] = now + self._refresh_seconds
            else:
                self._active_until.pop(connector_id, None)
        return states

    async def settle(self, messages: Sequence[StreamMessage]) -> ConnectorOffResult:
        if self._paused_until > self._clock():
            return ConnectorOffResult()
        candidates: list[int] = []
        for i, message in enumerate(messages):
            payload = message.payload or {}
            connector_id = payload.get("connectorId")
            if (
                connector_gated_event(message.eventType, payload)
                and payload.get("recordId")
                and connector_id
                and not self._known_active(str(connector_id))
            ):
                candidates.append(i)
        if not candidates:
            return ConnectorOffResult()

        try:
            states = await self._read_states(
                {str(messages[i].payload["connectorId"]) for i in candidates}
            )
        except Exception as e:
            self._pause("read the state of the queued events' connectors", e)
            return ConnectorOffResult()
        candidates = [
            i for i in candidates
            if states[str(messages[i].payload["connectorId"])]
            in (ConnectorState.OFF, ConnectorState.REMOVED)
        ]
        if not candidates:
            return ConnectorOffResult()

        record_ids = sorted({str(messages[i].payload["recordId"]) for i in candidates})
        try:
            docs = await asyncio.wait_for(
                self._graph.get_nodes_by_field_in(
                    CollectionNames.RECORDS.value, "id", record_ids, raise_on_error=True
                ),
                timeout=self._read_timeout_seconds,
            )
            records: dict[str, Mapping[str, Any]] = {}
            for doc in docs or []:
                key = (doc.get("_key") or doc.get("id")) if doc else None
                if key:
                    records[str(key)] = doc
            # A record the handler would judge by another connector than its
            # event names (one moved between connectors) is judged by that one.
            others = {
                c for c in (gating_connector_id(r) for r in records.values())
                if c and c not in states
            }
            if others:
                states.update(await self._read_states(others))
        except Exception as e:
            self._pause("read the records of turned-off connectors", e)
            return ConnectorOffResult()

        def state_of(connector_id: str) -> ConnectorState:
            return states.get(connector_id, ConnectorState.UNKNOWN)

        outcomes: dict[int, ReadTimeOutcome] = {}
        for i in candidates:
            message = messages[i]
            outcome = read_time_outcome(
                message.eventType,
                message.payload,
                records.get(str(message.payload["recordId"])),
                state_of,
            )
            if outcome is not ReadTimeOutcome.PASS:
                outcomes[i] = outcome

        # Every event of a record goes to the handler if any one of them does:
        # the handler runs them in order, and settling one first would change
        # what the other finds.
        handled = {
            str(message.payload.get("recordId"))
            for i, message in enumerate(messages)
            if i not in outcomes and message.payload.get("recordId")
        }
        outcomes = {
            i: o for i, o in outcomes.items()
            if str(messages[i].payload["recordId"]) not in handled
        }

        swapped = await self._mark_connector_off(
            {
                str(messages[i].payload["recordId"])
                for i, o in outcomes.items()
                if o is ReadTimeOutcome.SETTLE_CONNECTOR_OFF
            },
            records,
        )
        swapped = await self._still_off_after_write(swapped, records)
        outcomes = {
            i: o for i, o in outcomes.items()
            if o is not ReadTimeOutcome.SETTLE_CONNECTOR_OFF
            or str(messages[i].payload["recordId"]) in swapped
        }

        by_connector: Counter[tuple[str, str]] = Counter()
        for i in outcomes:
            connector_id = str(messages[i].payload["connectorId"])
            by_connector[(connector_id, state_of(connector_id).value)] += 1
        return ConnectorOffResult(settled=frozenset(outcomes), by_connector=by_connector)

    async def _mark_connector_off(
        self, record_ids: set[str], records: Mapping[str, Mapping[str, Any]]
    ) -> set[str]:
        """Write the handler's connector-off fields on the records that still
        hold the status the batch read saw, in one conditional statement.

        The filter does not hold the record's lease, so a record another
        delivery has moved on since (IN_PROGRESS, COMPLETED) is left exactly as
        that delivery left it. Returns the ids written; any other id goes
        through the normal path.
        """
        if not record_ids:
            return set()
        rows = [
            (
                record_id,
                connector_off_updates(records[record_id]),
                {"indexingStatus": records[record_id]["indexingStatus"]},
            )
            for record_id in sorted(record_ids)
        ]
        try:
            written = await asyncio.wait_for(
                self._graph.update_nodes_fields_if_match(
                    CollectionNames.RECORDS.value, rows
                ),
                timeout=self._read_timeout_seconds,
            )
        except Exception as e:
            self._pause("mark the records of turned-off connectors as not indexed", e)
            return set()
        return set(written) & record_ids

    async def _still_off_after_write(
        self, written: set[str], records: Mapping[str, Mapping[str, Any]]
    ) -> set[str]:
        """The written records whose connector is still off once written.

        A connector turned back on between the state read and the write would
        otherwise have its event settled as not indexed, and turning a
        connector on does not re-queue such records. So its state is read
        again after the write: the records of one that is now on (or can no
        longer be read) get their fields back, conditionally on still holding
        what was just written, and their events take the normal path, where
        the handler checks the connector itself.
        """
        if not written:
            return written
        connectors = {
            c for c in (gating_connector_id(records[r]) for r in written) if c
        }
        try:
            states = await self._read_states(connectors)
        except Exception as e:
            self._pause("re-read the connectors of the records just marked not indexed", e)
            states = {}
        reopened = {
            r for r in written
            if states.get(gating_connector_id(records[r]) or "")
            not in (ConnectorState.OFF, ConnectorState.REMOVED)
        }
        if reopened:
            rows = []
            for record_id in sorted(reopened):
                record = records[record_id]
                written_fields = connector_off_updates(record)
                rows.append((
                    record_id,
                    {field: record.get(field) for field in written_fields},
                    {"indexingStatus": written_fields["indexingStatus"], "reason": written_fields["reason"]},
                ))
            try:
                await asyncio.wait_for(
                    self._graph.update_nodes_fields_if_match(
                        CollectionNames.RECORDS.value, rows
                    ),
                    timeout=self._read_timeout_seconds,
                )
            except Exception as e:
                # The events still take the normal path; the handler writes
                # whatever status the connector's state calls for.
                self._logger.warning(
                    "Could not restore %d record(s) whose connector was turned "
                    "back on; the handler will set their status: %r",
                    len(reopened), e,
                )
        return written - reopened
