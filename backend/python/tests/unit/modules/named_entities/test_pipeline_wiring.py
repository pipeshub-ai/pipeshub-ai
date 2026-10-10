"""Named-entity stage wiring: never raises, gated by the flag, safe on failure."""

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
from app.modules.transformers.pipeline import IndexingPipeline
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


async def test_pipeline_runs_extraction_concurrently_with_classify():
    order: list[str] = []
    released = asyncio.Event()

    async def extract(ctx):
        order.append("ner-start")
        released.set()
        return _extraction()

    async def classify(ctx):
        await asyncio.wait_for(released.wait(), timeout=1)
        order.append("classify-done")
        ctx.record.semantic_metadata = MagicMock(summary="")

    doc_extraction = MagicMock(apply=AsyncMock(side_effect=classify))
    sink = MagicMock()
    sink.extract_named_entities = AsyncMock(side_effect=extract)
    sink.resolve_entities = AsyncMock()
    sink.attach_named_entities = MagicMock(side_effect=lambda *a: order.append("attach"))
    sink.blob_storage.apply = AsyncMock(side_effect=lambda ctx: order.append("blob"))
    sink.enrich = AsyncMock(side_effect=lambda ctx: order.append("enrich"))
    sink.persist_named_entities = AsyncMock(side_effect=lambda *a: order.append("persist"))
    ctx = MagicMock()
    await IndexingPipeline(doc_extraction, sink)._enrich(ctx)
    assert order == ["ner-start", "classify-done", "attach", "blob", "enrich", "persist"]


async def test_pipeline_cancels_extraction_when_classify_fails():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def extract(ctx):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def classify(ctx):
        await started.wait()
        raise RuntimeError("classify down")

    sink = MagicMock()
    sink.extract_named_entities = AsyncMock(side_effect=extract)
    pipeline = IndexingPipeline(MagicMock(apply=AsyncMock(side_effect=classify)), sink)
    with pytest.raises(RuntimeError):
        await pipeline._enrich(MagicMock())
    await asyncio.wait_for(cancelled.wait(), timeout=1)


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
    extraction even when classification gave no metadata; the graph writer must
    still see no metadata, which it records as a failed classification."""
    seen: dict = {}

    async def classify(ctx):
        ctx.record.semantic_metadata = None

    async def blob(ctx):
        seen["stored"] = ctx.record.semantic_metadata.named_entities

    async def enrich(ctx):
        seen["graph_saw"] = ctx.record.semantic_metadata

    sink = MagicMock()
    sink.extract_named_entities = AsyncMock(return_value=_extraction())
    sink.blob_storage.apply = AsyncMock(side_effect=blob)
    sink.enrich = AsyncMock(side_effect=enrich)
    sink.persist_named_entities = AsyncMock()
    ctx = SimpleNamespace(record=SimpleNamespace(semantic_metadata=None))

    await IndexingPipeline(MagicMock(apply=AsyncMock(side_effect=classify)), sink)._enrich(ctx)

    assert seen["graph_saw"] is None
    assert [entity["display_name"] for entity in seen["stored"]["entities"]] == [_SECRET_NAME]
    assert ctx.record.semantic_metadata is None
    sink.blob_storage.apply.assert_awaited_once()


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

