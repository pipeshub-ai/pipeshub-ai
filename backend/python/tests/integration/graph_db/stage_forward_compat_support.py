"""Scenarios for the forward-compatible stage-state schema, shared by the ArangoDB/Neo4j file and the PostgreSQL file.

A later release may add a stage-state field or a status value, and a pod still on this release's schema must
accept the row. Record nodes stay strict. Each scenario takes the provider and runs in whatever organisation
context the caller bound.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import Priority, StageJob, StageState
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.state import GraphStageStateStore
from app.modules.pipeline.worker import StageJobHandler
from app.schema.arango.documents import (
    PROGRESS_STATUS_VALUES,
    STAGE_PRIORITIES,
    STAGE_TIERS,
    stage_state_schema,
)
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.arango.arango_http_client import ArangoQueryError
from app.services.messaging.config import StreamMessage
from app.services.resource_governor.models import ParseTier
from tests.unit.modules.pipeline.fakes import (
    ClassifyStage,
    RecordingHeadlines,
    RecordingProducer,
    StubIO,
)

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

STAGES = CollectionNames.STAGE_STATES.value
#: What each store raises for a document its schema refuses: the application validator (Neo4j, PostgreSQL) or the
#: ArangoDB collection validator (error 1620).
REFUSED = (SchemaValidationError, ArangoQueryError)
#: The virtual records the documents of this run name, for cleanup.
CREATED_VRIDS: list[str] = []


def stage_document(org: str, vrid: str | None = None, **overrides: object) -> dict[str, Any]:
    """A complete stage-state document, as the writer stores it."""
    vrid = vrid or f"vr-{uuid.uuid4().hex[:12]}"
    CREATED_VRIDS.append(vrid)
    job = StageJob(
        stage="classify", stage_version=1, org_id=org, virtual_record_id=vrid,
        rev="rev-a", record_ids=("rec-1",), connector_id="conn-1", tier=ParseTier.LIGHT, priority=Priority.BULK,
        trigger="embed", text_digest="t", blocks_digest="b", text_chars=10, has_tables=False, has_images=False,
    )
    state = StageState.from_job(job, ProgressStatus.IN_PROGRESS, 1)
    document = state.model_dump(mode="json", by_alias=True)
    document["id"] = document.pop("key")
    document.update(overrides)
    return document


def strict_schema_of_the_previous_release() -> dict[str, Any]:
    """The stage-state schema as it was before the relaxation: closed, with the enums."""
    schema = copy.deepcopy(stage_state_schema)
    rule = schema["rule"]
    rule["additionalProperties"] = False
    rule["properties"]["status"] = {"type": "string", "enum": PROGRESS_STATUS_VALUES}
    rule["properties"]["tier"] = {"type": "string", "enum": STAGE_TIERS}
    rule["properties"]["priority"] = {"type": "string", "enum": STAGE_PRIORITIES}
    return schema


async def unknown_field_and_value_accepted(graph: IGraphDBProvider, org: str) -> str:
    """A newer writer's row with an extra field is stored. Returns the key of the row it left behind."""
    document = stage_document(org)
    key = document["id"]
    assert await graph.stage_state_create({**document, "sweepAtMs": 123})
    # A CAS patch carrying a field this release does not declare, and a status it does not know.
    assert await graph.stage_state_compare_and_set(
        key, "IN_PROGRESS", "SPLIT", {"sweepAtMs": 456, "attempt": 1, "updatedAtMs": 2}
    )
    stored = await graph.stage_state_get(key)
    assert stored is not None
    assert stored["status"] == "SPLIT"
    assert stored["sweepAtMs"] == 456
    # A new value for a string enum field is stored too.
    other = stage_document(org, tier="nano")
    assert await graph.stage_state_create(other)

    store = GraphStageStateStore(graph)
    state = await store.get(key)
    assert state is not None
    # An unknown status reads back as foreign: never an error, and never a status this release acts on.
    assert not isinstance(state.status, ProgressStatus) and state.status.value == "SPLIT"
    assert state.attempt == 1
    by_stage = await store.get_many(document["virtualRecordId"], document["rev"])
    assert not isinstance(by_stage[document["stage"]].status, ProgressStatus)
    # The sweep's own read still finds the row's other fields.
    assert (await graph.stage_state_get(other["id"]))["tier"] == "nano"
    return key


async def stage_types_still_checked(graph: IGraphDBProvider, org: str) -> None:
    """The stage half: declared types and required fields are still enforced."""
    with pytest.raises(REFUSED):
        await graph.stage_state_create(stage_document(org, attempt="many"))
    missing_status = stage_document(org)
    del missing_status["status"]
    with pytest.raises(REFUSED):
        await graph.stage_state_create(missing_status)
    document = stage_document(org)
    assert await graph.stage_state_create(document)
    with pytest.raises(REFUSED):
        await graph.stage_state_compare_and_set(document["id"], "IN_PROGRESS", "COMPLETED", {"attempt": "many"})
    with pytest.raises(REFUSED):
        await graph.stage_state_compare_and_set(document["id"], "IN_PROGRESS", 7, {})  # type: ignore[arg-type]


def record_document(org: str, key: str, **extra: object) -> dict[str, Any]:
    return {
        "id": key, "orgId": org, "recordName": key, "externalRecordId": key, "recordType": "FILE",
        "origin": "CONNECTOR", "connectorName": "KB", "connectorId": f"{org}-c", "virtualRecordId": f"{key}-v",
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "version": 1, "indexingStatus": "NOT_STARTED", **extra,
    }


async def record_nodes_stay_strict(graph: IGraphDBProvider, org: str) -> None:
    """The record half: a record node with an unknown field is refused, and the same node without it is accepted."""
    key = f"rec-{uuid.uuid4().hex[:12]}"
    # ArangoDB's batch insert raises a plain Exception carrying the validator's message (1620).
    with pytest.raises(Exception, match=r"notAField|record schema"):
        assert await graph.batch_upsert_nodes([record_document(org, key, notAField=1)], CollectionNames.RECORDS.value)
    assert await graph.get_document(key, CollectionNames.RECORDS.value) is None
    assert await graph.batch_upsert_nodes([record_document(org, key)], CollectionNames.RECORDS.value)
    await graph.delete_nodes([key], CollectionNames.RECORDS.value)


async def unknown_tier_and_priority_read_tolerantly(graph: IGraphDBProvider, org: str) -> None:
    """A tier or priority a later release added is read as HEAVY / bulk, and one such row never stops the
    other stages of its revision from being read."""
    from app.services.resource_governor.models import ParseTier

    good = stage_document(org)
    vrid, rev = good["virtualRecordId"], good["rev"]

    def sibling(stage: str, **overrides: object) -> dict[str, Any]:
        return {**stage_document(org, vrid=vrid, **overrides), "stage": stage, "id": f"{vrid}:{rev}:{stage}"}

    odd_tier, odd_priority = sibling("entities", tier="nano"), sibling("embed", priority="urgent")
    for document in (good, odd_tier, odd_priority):
        assert await graph.stage_state_create(document)

    states = await GraphStageStateStore(graph).get_many(vrid, rev)

    assert set(states) == {good["stage"], "entities", "embed"}
    assert states["entities"].tier is ParseTier.HEAVY
    assert states["embed"].priority is Priority.BULK
    assert states[good["stage"]].tier is ParseTier.LIGHT, "a known value is read as stored"
    one = await GraphStageStateStore(graph).get(odd_tier["id"])
    assert one is not None and one.tier is ParseTier.HEAVY



async def a_foreign_row_is_left_byte_identical(graph: IGraphDBProvider, org: str) -> None:
    """A row a newer release left in a status this release does not know: this release's coordinator, worker
    and sweep read it without raising and never write it, and its siblings stay readable."""
    done = stage_document(org)
    vrid, rev = done["virtualRecordId"], done["rev"]
    done.update(stage="embed", id=f"{vrid}:{rev}:embed", status="COMPLETED")
    foreign = {**stage_document(org, vrid=vrid), "status": "SPLIT", "attempt": 2, "sweepAtMs": 7, "updatedAtMs": 1}
    for document in (done, foreign):
        assert await graph.stage_state_create(document)
    key = foreign["id"]
    before = json.dumps(await graph.stage_state_get(key), sort_keys=True)

    store = GraphStageStateStore(graph)
    stage = ClassifyStage()
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(stage)
    producer = RecordingProducer()
    coordinator = Coordinator(
        registry, store, RecordingHeadlines(), producer,  # type: ignore[arg-type]
        policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"),
    )
    handler = StageJobHandler(
        registry, store, RecordingHeadlines(), coordinator, StubIO, logger=logging.getLogger("t"), worker_id="older"
    )
    state = await store.get(key)
    assert state is not None
    job = state.to_job()
    message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))

    assert await coordinator.on_external_done("embed", job.view, priority=Priority.BULK, trigger="t") == []
    assert await coordinator.redrive(job.view, "classify", priority=Priority.INTERACTIVE, force=True) is None
    assert [event async for event in handler(message)]
    await handler.on_message_abandoned(message, reason="poison", attempts=5)
    stale = await store.stale(statuses=list(ProgressStatus), updated_before_ms=2, limit=10_000)

    assert key not in {s.key for s in stale}
    assert (stage.runs, producer.sent) == (0, [])
    assert handler.outcomes("classify")["foreign"] == 1
    assert json.dumps(await graph.stage_state_get(key), sort_keys=True) == before
    states = await store.get_many(vrid, rev)
    assert states["embed"].status is ProgressStatus.COMPLETED
    assert not isinstance(states["classify"].status, ProgressStatus)

    # A revision whose prerequisite a newer release recorded is seen, and a redrive asks for nothing: an unseen
    # revision or None would make the record handler reindex it from scratch.
    recorded = {**stage_document(org), "stage": "embed", "status": "SPLIT", "sweepAtMs": 7}
    recorded["id"] = f"{recorded['virtualRecordId']}:{recorded['rev']}:embed"
    assert await graph.stage_state_create(recorded)
    recorded_before = json.dumps(await graph.stage_state_get(recorded["id"]), sort_keys=True)
    assert await coordinator.has_seen_revision(recorded["virtualRecordId"], recorded["rev"])
    assert await coordinator.redrive_revision(
        recorded["virtualRecordId"], recorded["rev"], ["classify"], priority=Priority.INTERACTIVE, force=True
    ) == []
    assert json.dumps(await graph.stage_state_get(recorded["id"]), sort_keys=True) == recorded_before
    assert producer.sent == []
