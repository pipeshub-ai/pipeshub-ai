"""Named-entity wiring: its own pipeline stage beside classification, never raises, gated
by the flag, safe on failure."""

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.entities import EntityType
from app.modules.named_entities import stage
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import (
    Mention,
    NamedEntity,
    NamedEntityExtraction,
)
from app.modules.transformers.sink_orchestrator import SinkOrchestrator

_SECRET_NAME = "Zebulon Quartermaine"


def _record():
    return SimpleNamespace(id="rec-1", org_id="org-1", semantic_metadata=None)


def _extraction(status="COMPLETED") -> NamedEntityExtraction:
    entity = NamedEntity(
        kind=EntityKind.PERSON,
        display_name=_SECRET_NAME,
        norm_key="zebulon quartermaine",
        mentions=[Mention(block_index=0, char_start=0, char_end=20, surface=_SECRET_NAME, extractor="agent")],
    )
    return NamedEntityExtraction(strategy="agent", termination_reason="finish_ok", status=status, entities=[entity])


def _store():
    tx = MagicMock()
    tx.batch_update_nodes = AsyncMock()
    store = MagicMock()

    @asynccontextmanager
    async def transaction():
        yield tx

    store.transaction = transaction
    store.tx = tx
    return store


def _sink(graph=None) -> SinkOrchestrator:
    sink = SinkOrchestrator.__new__(SinkOrchestrator)
    sink.logger = logging.getLogger("test")
    sink.config_service = MagicMock()
    sink.graph_provider = graph or MagicMock()
    sink.graphdb = MagicMock()
    sink.entity_vector_store = None
    return sink


def _view_job(stage_name: str = "entities"):
    from app.modules.pipeline.models import Priority, StageJob
    from app.services.resource_governor.models import ParseTier

    return StageJob.model_validate({
        "stage": stage_name, "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    })


async def test_extraction_is_dispatched_beside_classification_not_after_it():
    """Named entities run in parallel with classification: both stages are dispatched the
    moment the record is searchable, and only classification owns extractionStatus."""
    from app.modules.pipeline.coordinator import Coordinator
    from app.modules.pipeline.models import HeadlineField, Priority
    from app.modules.pipeline.policy import DEFAULT_POLICY
    from app.modules.pipeline.registry import StageRegistry
    from app.modules.pipeline.stages.classify import ClassifyStage
    from app.modules.pipeline.stages.entities import EntitiesStage
    from tests.unit.modules.pipeline.fakes import (
        Clock,
        InMemoryStageStateStore,
        RecordingHeadlines,
        RecordingProducer,
    )

    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(ClassifyStage())
    registry.register(EntitiesStage())
    clock = Clock()
    headlines, producer = RecordingHeadlines(), RecordingProducer()
    coordinator = Coordinator(
        registry, InMemoryStageStateStore(clock), headlines, producer,  # type: ignore[arg-type]
        policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"), clock_ms=clock,
    )

    published = await coordinator.on_external_done(
        "embed", _view_job("embed").view, priority=Priority.BULK, trigger="index"
    )

    assert sorted(job_id.rsplit(":", 1)[1] for job_id in published) == ["classify@1", "entities@1"]
    assert len(producer.jobs("pipeline.classify")) == 1
    assert len(producer.jobs("pipeline.entities")) == 1
    assert [write[1] for write in headlines.writes] == [HeadlineField.EXTRACTION]


class _SlowEntitiesIO:
    def __init__(self, unwound: list[bool]) -> None:
        self.unwound = unwound

    @property
    def policy(self):
        from app.modules.pipeline.policy import DEFAULT_POLICY

        return DEFAULT_POLICY

    @property
    def deadline(self):
        from app.modules.pipeline.stage import Deadline

        return Deadline(60)

    async def entities_config(self, job) -> dict:
        return {}

    async def current_record_ids(self, job) -> list[str]:
        return ["rec-1"]

    async def load_blocks(self, job):
        from app.models.blocks import BlocksContainer

        return BlocksContainer(blocks=[], block_groups=[])

    async def extract(self, job, blocks):
        try:
            await asyncio.sleep(10)
        finally:
            self.unwound.append(True)


async def test_an_extraction_past_its_budget_is_cancelled_and_finished_before_the_retry():
    """Awaited, not just cancelled: a task left pending is destroyed with a warning. The
    stage's budget is what cuts it short now; classification is not involved."""
    from app.modules.pipeline.coordinator import Coordinator
    from app.modules.pipeline.policy import DEFAULT_POLICY
    from app.modules.pipeline.registry import StageRegistry
    from app.modules.pipeline.stages.entities import EntitiesStage
    from app.modules.pipeline.worker import StageJobHandler, StageRetry
    from app.services.messaging.config import StreamMessage
    from tests.unit.modules.pipeline.fakes import (
        Clock,
        InMemoryStageStateStore,
        RecordingHeadlines,
        RecordingProducer,
    )

    class _Quick(EntitiesStage):
        budget_s = 0.05

    unwound: list[bool] = []
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_Quick())
    clock = Clock()
    states, headlines = InMemoryStageStateStore(clock), RecordingHeadlines()
    coordinator = Coordinator(
        registry, states, headlines, RecordingProducer(),  # type: ignore[arg-type]
        policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"), clock_ms=clock,
    )
    handler = StageJobHandler(
        registry, states, headlines, coordinator, lambda job, deadline: _SlowEntitiesIO(unwound),
        logger=logging.getLogger("t"), clock_ms=clock,
    )
    message = StreamMessage(eventType="stageJob", payload=_view_job().model_dump(mode="json", by_alias=True))

    with pytest.raises(StageRetry):
        async for _event in handler(message):
            pass

    assert unwound == [True]
    assert headlines.writes == []


async def test_extraction_failure_returns_failed_and_does_not_raise(caplog):
    sink = _sink()
    ctx = SimpleNamespace(record=_record())
    with patch(
        "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
        AsyncMock(return_value=True),
    ), patch.object(stage, "extract_named_entities", AsyncMock(side_effect=RuntimeError(_SECRET_NAME))):
        result = await sink.extract_named_entities(ctx)
    assert result.status == "FAILED"
    assert _SECRET_NAME not in caplog.text


@pytest.mark.parametrize("config", [{"budgets": {"max_turns": "lots"}}, {"tz": 7}])
async def test_a_malformed_config_blob_runs_with_the_defaults(config):
    sink = _sink()
    sink.config_service.get_config = AsyncMock(return_value=config)
    record = SimpleNamespace(
        **vars(_record()),
        record_name="Doc",
        record_type=SimpleNamespace(value="FILE"),
        block_containers=SimpleNamespace(blocks=[object()]),
    )
    with patch(
        "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
        AsyncMock(return_value=True),
    ), patch.object(stage, "indexing_llm", AsyncMock(side_effect=RuntimeError("no model"))):
        result = await sink.extract_named_entities(SimpleNamespace(record=record))
    assert (result.status, result.termination_reason) == ("SKIPPED", "empty")


async def test_a_flag_off_clear_failure_does_not_fail_the_record(caplog):
    graph = MagicMock(get_named_entities_for_record=AsyncMock(side_effect=RuntimeError(f"down {_SECRET_NAME}")))
    sink = _sink(graph)
    with patch(
        "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
        AsyncMock(return_value=False),
    ):
        await sink.persist_named_entities(SimpleNamespace(record=_record()), _extraction())
    assert "Named-entity persist failed for record rec-1: RuntimeError" in caplog.text
    assert _SECRET_NAME not in caplog.text


async def test_flag_off_clears_only_records_that_had_entities():
    store = _store()
    graph = MagicMock(
        get_named_entities_for_record=AsyncMock(return_value=[]),
        replace_named_entity_values=AsyncMock(),
        clear_named_entity_persist_retry=AsyncMock(return_value=True),
    )
    ctx = SimpleNamespace(record=_record())
    await stage.persist_named_entities(ctx, _extraction(), graph_provider=graph, graph_data_store=store, enabled=False)
    store.tx.batch_update_nodes.assert_not_awaited()
    graph.replace_named_entity_values.assert_not_awaited()

    graph.get_named_entities_for_record = AsyncMock(return_value=[{"id": "e1"}])
    with patch("app.modules.named_entities.graph_writer.EdgeReconciler") as reconciler:
        reconciler.return_value.reconcile = AsyncMock()
        await stage.persist_named_entities(ctx, _extraction(), graph_provider=graph, graph_data_store=store, enabled=False)
    assert reconciler.return_value.reconcile.await_args.kwargs["new_tos"] == {}
    graph.replace_named_entity_values.assert_awaited_once_with("rec-1", [], store.tx.txn)
    store.tx.batch_update_nodes.assert_not_awaited()


async def test_failed_extraction_keeps_previous_edges():
    store = _store()
    graph = MagicMock()
    ctx = SimpleNamespace(record=_record())
    with patch("app.modules.named_entities.graph_writer.EdgeReconciler") as reconciler:
        await stage.persist_named_entities(
            ctx, _extraction("FAILED"), graph_provider=graph, graph_data_store=store, enabled=True,
        )
    reconciler.assert_not_called()
    assert graph.mock_calls == []
    store.tx.batch_update_nodes.assert_not_awaited()


def _llm_outage() -> NamedEntityExtraction:
    entity = NamedEntity(
        kind=EntityKind.DATE,
        display_name="2025-07-01",
        norm_key="2025-07-01",
        mentions=[Mention(block_index=0, char_start=0, char_end=10, surface="2025-07-01", extractor="value")],
    )
    return NamedEntityExtraction(
        strategy="deterministic", termination_reason="llm_error", status="PARTIAL", entities=[entity]
    )


async def test_an_llm_outage_keeps_the_entities_of_the_last_good_run():
    graph = MagicMock(get_named_entities_for_record=AsyncMock(return_value=[{"id": "acme"}]))
    store = _store()
    with patch("app.modules.named_entities.graph_writer.EdgeReconciler") as reconciler:
        await stage.persist_named_entities(
            SimpleNamespace(record=_record()), _llm_outage(),
            graph_provider=graph, graph_data_store=store, enabled=True,
        )
    reconciler.assert_not_called()
    graph.create_named_entities_if_absent.assert_not_called()
    store.tx.batch_update_nodes.assert_not_awaited()


async def test_an_agent_that_gave_up_with_nothing_keeps_the_last_good_entities():
    gave_up = _llm_outage().model_copy(update={"termination_reason": "no_progress", "strategy": "agent"})
    graph = MagicMock(get_named_entities_for_record=AsyncMock(return_value=[{"id": "acme"}]))
    with patch("app.modules.named_entities.graph_writer.EdgeReconciler") as reconciler:
        await stage.persist_named_entities(
            SimpleNamespace(record=_record()), gave_up, graph_provider=graph, graph_data_store=_store(), enabled=True,
        )
    reconciler.assert_not_called()
    graph.create_named_entities_if_absent.assert_not_called()


async def test_an_llm_outage_on_a_record_with_no_entities_writes_the_deterministic_ones():
    graph = MagicMock(
        get_named_entities_for_record=AsyncMock(return_value=[]),
        find_named_entities=AsyncMock(return_value=[]),
        create_named_entities_if_absent=AsyncMock(),
        replace_named_entity_values=AsyncMock(),
        clear_named_entity_persist_retry=AsyncMock(return_value=True),
    )
    with patch("app.modules.named_entities.graph_writer.EdgeReconciler") as reconciler:
        reconciler.return_value.reconcile = AsyncMock()
        await stage.persist_named_entities(
            SimpleNamespace(record=_record()), _llm_outage(),
            graph_provider=graph, graph_data_store=_store(), enabled=True,
        )
    record_id, values, _transaction = graph.replace_named_entity_values.await_args.args
    assert (record_id, [value["kind"] for value in values]) == ("rec-1", ["date"])


async def test_a_persist_failure_is_logged_as_failed_with_counts_only(caplog):
    caplog.set_level(logging.INFO)
    graph = MagicMock(
        find_named_entities=AsyncMock(return_value=[]),
        create_named_entities_if_absent=AsyncMock(side_effect=RuntimeError(f"bind {_SECRET_NAME}")),
    )
    ctx = SimpleNamespace(record=_record())
    outcome = await stage.persist_named_entities(
        ctx, _extraction(), graph_provider=graph, graph_data_store=_store(), enabled=True,
    )
    assert outcome == "failed"
    assert "named_entities record=rec-1 outcome=failed" in caplog.text
    assert _SECRET_NAME not in caplog.text


async def test_a_lookup_that_cannot_run_writes_nothing_and_fails_the_persist(caplog, monkeypatch) -> None:
    monkeypatch.setattr("app.modules.entity_resolution.lookup.LOOKUP_RETRY_DELAY_SECONDS", 0.0)
    caplog.set_level(logging.INFO)
    graph = MagicMock(
        find_named_entities=AsyncMock(side_effect=TimeoutError(f"bind {_SECRET_NAME}")),
        create_named_entities_if_absent=AsyncMock(),
    )
    outcome = await stage.persist_named_entities(
        SimpleNamespace(record=_record()), _extraction(),
        graph_provider=graph, graph_data_store=_store(), enabled=True,
    )
    assert outcome == "failed"
    assert graph.find_named_entities.await_count == 2
    graph.create_named_entities_if_absent.assert_not_awaited()
    assert _SECRET_NAME not in caplog.text


async def test_a_lookup_that_cannot_run_is_queued_and_the_retry_links_the_survivor(monkeypatch) -> None:
    """The first write defers instead of linking the merged-away node; the
    retry, once the graph answers, links the node it was merged into."""
    monkeypatch.setattr("app.modules.entity_resolution.lookup.LOOKUP_RETRY_DELAY_SECONDS", 0.0)
    from app.modules.named_entities.keys import named_entity_key

    own = named_entity_key("org-1", "person", "zebulon quartermaine")
    merged_row = {"normKey": "zebulon quartermaine", "_key": own, "mergedInto": "survivor"}
    graph = MagicMock(
        find_named_entities=AsyncMock(side_effect=[TimeoutError(), TimeoutError(), [merged_row]]),
        resolve_named_entity_redirects=AsyncMock(return_value={own: "survivor"}),
        schedule_named_entity_persist_retry=AsyncMock(return_value=1),
    )
    sink = _sink(graph)
    sink.graphdb = MagicMock(graph_data_store=_store())
    write = AsyncMock(return_value=[])
    with patch.object(stage.NamedEntityGraphWriter, "write", write), \
            patch("app.services.featureflag.platform_settings.is_named_entity_extraction_enabled", AsyncMock(return_value=True)):
        ctx = SimpleNamespace(record=_record())
        assert await sink.persist_named_entities(ctx, _extraction()) == "failed"
        write.assert_not_awaited()
        graph.schedule_named_entity_persist_retry.assert_awaited_once_with("org-1", "rec-1", "persist_failed", 0)

        assert await sink.persist_named_entities(ctx, _extraction(), retry_due=100, retry_attempts=1) == "written"
    (_org_id, _record_id, resolved), kwargs = write.await_args
    assert [(item.graph_key, item.merged_into) for item in resolved] == [("survivor", "survivor")]
    assert kwargs == {"retry_due": 100}


async def test_duplicate_sync_projects_named_entities_but_not_pii_or_foreign_rows():
    graph = MagicMock(
        get_taxonomy_entities_for_record=AsyncMock(return_value=[]),
        get_named_entities_for_record=AsyncMock(return_value=[
            {"id": "n1", "kind": "organization", "name": "Acme", "orgId": "org-1"},
            {"id": "n2", "kind": "email", "name": "a@b.co", "orgId": "org-1"},
            {"id": "n3", "kind": "organization", "name": "Other", "orgId": "org-2"},
        ]),
        get_record_group_by_id=AsyncMock(return_value=None),
    )
    sink = _sink(graph)
    sink.entity_vector_store = MagicMock(
        upsert_entities_batch=AsyncMock(),
        replace_entities_batch=AsyncMock(),
        upsert_entities_membership=AsyncMock(),
    )
    await sink.sync_entities_for_duplicate({"_key": "dup-1", "orgId": "org-1"})
    projected = [
        entity
        for call in sink.entity_vector_store.mock_calls
        for arg in call.args
        if isinstance(arg, list)
        for entity in arg
        if getattr(entity, "entity_type", None) is EntityType.NAMED_ENTITY
    ]
    assert [(entity.entity_id, entity.kind) for entity in projected] == [("n1", "organization")]


def test_no_module_writes_entity_extraction_status():
    """The records schema gains this field in this release. A build without it
    reapplies its strict schema on start and then rejects every update to a record
    that carries the field, so its first writer ships a release after the schema.
    Only the schema may name it: extraction status lives in the record's blob."""
    import pathlib

    import app

    root = pathlib.Path(app.__file__).parent
    allowed = {pathlib.Path("schema/arango/documents.py")}
    writers = {
        path.relative_to(root)
        for path in root.rglob("*.py")
        if "entityExtractionStatus" in path.read_text(encoding="utf-8")
    }
    assert writers == allowed


async def test_an_extraction_without_classification_still_reaches_the_blob():
    """The blob is what the graph and vectors are rebuilt from, so it must hold the
    extraction even when no classification is stored (yet, or at all); the stored record
    must still read as unclassified to the classify stage."""
    from app.events.processor import convert_record_dict_to_record
    from app.modules.pipeline.leases import RecordLeases
    from app.modules.pipeline.policy import DEFAULT_POLICY
    from app.modules.pipeline.stage import Deadline
    from app.modules.pipeline.stages.classify_io import GraphClassifyIO
    from app.modules.pipeline.stages.entities_io import GraphEntitiesIO

    stored = {"block_containers": {"blocks": [], "block_groups": []}, "semantic_metadata": None}

    async def get(_vrid, _org):
        return dict(stored)

    async def save(_org, _record, _vrid, metadata):
        stored["semantic_metadata"] = metadata

    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={
        "_key": "rec-1", "orgId": "org-1", "contentRev": "rev-a", "recordName": "a.txt",
        "externalRecordId": "x", "connectorId": "conn-1", "mimeType": "text/plain",
    })
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    blob = MagicMock(get_record_from_storage=AsyncMock(side_effect=get), save_semantic_metadata=AsyncMock(side_effect=save))
    sink = MagicMock(persist_named_entities=AsyncMock(return_value="written"))
    common = {
        "policy": DEFAULT_POLICY, "graph": graph, "blob_storage": blob, "sink": sink, "config_service": MagicMock(),
        "record_from_document": convert_record_dict_to_record, "record_leases": RecordLeases(),
    }

    io = GraphEntitiesIO(_view_job(), Deadline(30), **common)
    await io.current_record_ids(_view_job())
    assert await io.save(_view_job(), ["rec-1"], _extraction()) == "written"

    assert [entity["display_name"] for entity in stored["semantic_metadata"]["named_entities"]["entities"]] == [
        _SECRET_NAME
    ]
    classify_io = GraphClassifyIO(_view_job("classify"), Deadline(30), classifier=AsyncMock(), **common)
    assert not await classify_io.stored_metadata_present(_view_job("classify"))


@pytest.mark.parametrize(
    ("metadata", "extraction", "holds"),
    [
        (None, NamedEntityExtraction(status="SKIPPED", termination_reason="empty"), False),
        ("classified", _extraction(), False),
        (None, None, False),
        (None, _extraction(), True),
    ],
)
def test_the_blob_holder_is_set_only_when_it_carries_something(metadata, extraction, holds):
    record = SimpleNamespace(semantic_metadata=metadata)
    with stage.extraction_blob_holder(record, extraction) as held:
        assert held is holds
    assert record.semantic_metadata == metadata


async def test_a_stored_extraction_is_written_again_without_a_model():
    sink = _sink()
    sink.persist_named_entities = AsyncMock(return_value="written")
    stored = _extraction().model_dump(mode="json")
    ctx = SimpleNamespace(record=SimpleNamespace(semantic_metadata=SimpleNamespace(named_entities=stored)))

    assert await sink.reproject_named_entities(ctx) == "written"
    (_, extraction), _ = sink.persist_named_entities.await_args
    assert [entity.display_name for entity in extraction.entities] == [_SECRET_NAME]

    ctx.record.semantic_metadata = None
    assert await sink.reproject_named_entities(ctx) is None


async def test_an_extraction_records_what_it_was_normalized_against():
    from app.modules.named_entities.extractor import (
        NamedEntityExtractor,
        NamedEntityRequest,
    )
    from app.modules.named_entities.keys import KEY_SCHEME

    extraction = await NamedEntityExtractor().extract(NamedEntityRequest(
        blocks=[SimpleNamespace(index=0, id="b", type="text", data="Pay $1,250 next week")],
        org_id="org", reference_time_ms=1791546544639, tz="Europe/Berlin",
        enabled=frozenset({EntityKind.CURRENCY, EntityKind.DATE_RANGE}),
    ))
    assert (extraction.reference_time_ms, extraction.tz, extraction.key_scheme) == (1791546544639, "Europe/Berlin", KEY_SCHEME)


@pytest.mark.parametrize(("stage_outcome", "retried"), [("written", False), ("kept", False), ("failed", True)])
async def test_a_failed_persist_is_queued_for_retry_and_never_fails_indexing(stage_outcome, retried):
    graph = MagicMock(schedule_named_entity_persist_retry=AsyncMock(return_value=1))
    sink = _sink(graph)
    with patch.object(stage, "persist_named_entities", AsyncMock(return_value=stage_outcome)), \
            patch("app.services.featureflag.platform_settings.is_named_entity_extraction_enabled", AsyncMock(return_value=True)):
        assert await sink.persist_named_entities(SimpleNamespace(record=_record()), _extraction()) == stage_outcome
    if retried:
        graph.schedule_named_entity_persist_retry.assert_awaited_once_with("org-1", "rec-1", "persist_failed", 0)
    else:
        graph.schedule_named_entity_persist_retry.assert_not_awaited()


async def test_a_retry_that_cannot_be_recorded_still_does_not_raise():
    graph = MagicMock(schedule_named_entity_persist_retry=AsyncMock(side_effect=RuntimeError("graph down")))
    sink = _sink(graph)
    with patch("app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
               AsyncMock(side_effect=TimeoutError())):
        assert await sink.persist_named_entities(SimpleNamespace(record=_record()), _extraction()) == "failed"
    graph.schedule_named_entity_persist_retry.assert_awaited_once_with("org-1", "rec-1", "TimeoutError", 0)


async def test_a_retry_that_fails_again_keeps_its_count() -> None:
    """Its marker may already be gone (a committed claim), so the count is carried."""
    graph = MagicMock(schedule_named_entity_persist_retry=AsyncMock(return_value=4))
    sink = _sink(graph)
    with patch.object(stage, "persist_named_entities", AsyncMock(return_value="failed")), \
            patch("app.services.featureflag.platform_settings.is_named_entity_extraction_enabled", AsyncMock(return_value=True)):
        await sink.persist_named_entities(SimpleNamespace(record=_record()), _extraction(), retry_due=100, retry_attempts=3)
    graph.schedule_named_entity_persist_retry.assert_awaited_once_with("org-1", "rec-1", "persist_failed", 3)

