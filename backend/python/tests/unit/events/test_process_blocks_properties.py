"""``process_blocks`` hands a pushed record's stored values to indexing, and only while they are the record's newest.

Real bytes, as the run downloads them: the revision the run compares is the hash of those bytes, so a dict
re-serialised by the test would compare against nothing. The record row names the revision the push committed.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.events.processor as processor_module
from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.push import contract as C
from app.events.processor import Processor
from app.models.blocks import Block, BlocksContainer, BlockType
from app.models.record_metadata import MetadataProvenance, RecordMetadata
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import PROPERTIES_PATH
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter
from app.modules.record_metadata.projection import (
    DOWNLOADED_REV,
    NOT_PROVIDED,
    PROVIDED_PROPERTIES,
)
from app.modules.record_metadata.push_properties import PUSH_SOURCE
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.featureflag.platform_settings import ArtifactSettings
from tests.unit.modules.artifacts.fake_storage import FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.unit

ORG, REC, VRID = "org-1", "rec-1", "vr-1"
VALUES = {"status": "open", "labels": ["a", "b"], "points": 3}


def _pushed(properties: object = None, *, marker: bool = True, **extra: Any) -> bytes:  # noqa: ANN401
    """The stored document of a push: blocks, the push marker, and (when given) the ``properties`` member."""
    doc = BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, data="hello")],
        block_groups=[],
    ).model_dump(mode="json", exclude_none=True)
    if marker:
        doc[C.PUSH_FORMAT_KEY] = C.PUSH_FORMAT
    if properties is not None:
        doc["properties"] = properties
    doc.update(extra)
    return json.dumps(doc, sort_keys=True).encode()


def _props(values: dict[str, Any] | None = None) -> dict[str, Any]:
    return C.canonical_properties("schema-1", VALUES if values is None else values)


def _revision_of(data: bytes) -> str:
    return C.revision_id("a" * 64, C.content_rev(data))


def _row(data: bytes, **over: Any) -> dict[str, Any]:  # noqa: ANN401
    base = {
        "_key": REC,
        "orgId": ORG,
        "recordName": "doc",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "version": 1,
        "origin": "UPLOAD",
        "connectorName": "KB",
        "connectorId": "kb-1",
        "externalRecordId": "ext-1",
        "mimeType": "application/vnd.pipeshub.blocks",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "externalRevisionId": _revision_of(data),
        "contentRev": C.content_rev(data),
        "pushOrigin": True,
    }
    base.update(over)
    return base


class _Capture:
    """A pipeline that keeps the contexts it was asked to apply."""

    def __init__(self) -> None:
        self.applied: list[Any] = []

    async def apply(self, ctx: Any) -> None:  # noqa: ANN401
        self.applied.append(ctx)


def _processor(row: dict[str, Any], pipeline: Any) -> Processor:  # noqa: ANN401
    graph = MagicMock()
    graph.get_document = AsyncMock(
        side_effect=lambda key, collection, *a, **kw: (
            dict(row) if collection == CollectionNames.RECORDS.value else None
        )
    )
    graph.batch_update_nodes = AsyncMock(return_value=True)
    graph.update_node_fields_if_match = AsyncMock(return_value=True)
    processor = Processor.__new__(Processor)
    processor.logger = MagicMock()
    processor.graph_provider = graph
    processor.config_service = MagicMock()
    processor._pipeline = lambda: pipeline  # type: ignore[method-assign]
    processor._process_blockgroups = AsyncMock(
        side_effect=lambda containers, _name, **_kw: containers
    )  # type: ignore[method-assign]
    return processor


async def _process(processor: Processor, data: bytes | str | dict) -> list[Any]:
    with (
        patch.object(processor_module, "enhance_tables_with_llm", AsyncMock()),
        patch.object(sink_module, "notify_record_indexed", AsyncMock()),
    ):
        return [
            e
            async for e in processor.process_blocks(
                "doc", REC, 1, "KB", ORG, data, VRID
            )
        ]


@pytest.fixture
def refused() -> Any:  # noqa: ANN401
    with (
        patch.object(processor_module, "pushed_properties_refused") as spy,
        patch(
            "app.modules.record_metadata.push_properties.pushed_properties_refused", spy
        ),
    ):
        yield spy


# --- what reaches the settings ----------------------------------------------------------------------------


async def test_valid_values_reach_the_run_as_provided_push_values() -> None:
    data = _pushed(_props())
    pipeline = _Capture()

    await _process(_processor(_row(data), pipeline), data)

    [ctx] = pipeline.applied
    provided = ctx.settings[PROVIDED_PROPERTIES]
    assert isinstance(provided, RecordMetadata)
    assert provided.schema_id == "schema-1"
    assert {k: v.value for k, v in provided.values.items()} == VALUES
    # M16: what 9b and 9c read to tell a pushed value from an extracted one.
    assert PUSH_SOURCE == "push"
    assert {(v.provenance, v.source) for v in provided.values.values()} == {
        (MetadataProvenance.PROVIDED, "push")
    }


async def test_the_run_carries_the_revision_of_the_bytes_it_downloaded() -> None:
    """The sink files the run's content and values under this revision, not under whatever the record names later."""
    data = _pushed(_props())
    pipeline = _Capture()
    await _process(_processor(_row(data), pipeline), data)
    [ctx] = pipeline.applied
    assert ctx.settings[DOWNLOADED_REV] == C.content_rev(data)

    plain = _pushed(_props(), marker=False)
    pipeline = _Capture()
    await _process(_processor(_row(plain, pushOrigin=False, externalRevisionId="plain"), pipeline), plain)
    [ctx] = pipeline.applied
    assert DOWNLOADED_REV not in ctx.settings


async def test_none_versus_empty_values() -> None:
    absent, empty_member = _pushed(), _pushed({})
    empty_values = _pushed(C.canonical_properties("schema-1", {}))
    results = []
    for data in (absent, empty_member, empty_values):
        pipeline = _Capture()
        await _process(_processor(_row(data), pipeline), data)
        [ctx] = pipeline.applied
        results.append(ctx.settings.get(PROVIDED_PROPERTIES, "unset"))

    assert results[0] is None, "no member: the record has none"
    assert results[1] is None, "an empty member: the record has none"
    assert isinstance(results[2], RecordMetadata) and results[2].values == {}, (
        "values {}: a schema and no values"
    )


async def test_malformed_member_is_not_provided_and_the_record_still_indexes(
    refused: MagicMock,
) -> None:
    data = _pushed({"schemaId": "schema-1", "values": {"points": {"nested": 1}}})
    pipeline = _Capture()

    events = await _process(_processor(_row(data), pipeline), data)

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    refused.assert_called_once_with("malformed")


async def test_non_push_blocks_ignore_a_properties_key(refused: MagicMock) -> None:
    data = _pushed(_props(), marker=False)
    pipeline = _Capture()

    await _process(
        _processor(_row(data, pushOrigin=False, externalRevisionId="plain"), pipeline),
        data,
    )

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings
    refused.assert_not_called()


# --- the values are the record's newest (T44) -------------------------------------------------------------


async def test_values_of_a_superseded_revision_are_refused(refused: MagicMock) -> None:
    """Run 1 downloaded revision 1; by its read of the row the push of revision 2 has committed."""
    old = _pushed(_props({"status": "old"}))
    new = _pushed(_props({"status": "new"}), extra_member=1)
    assert C.content_rev(old) != C.content_rev(new)
    pipeline = _Capture()

    await _process(_processor(_row(new), pipeline), old)

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings, (
        "the stale values must not become the newest"
    )
    refused.assert_called_once_with("superseded")


async def test_the_run_of_the_newest_revision_keeps_its_values(
    refused: MagicMock,
) -> None:
    old = _pushed(_props({"status": "old"}))
    new = _pushed(_props({"status": "new"}), extra_member=1)
    pipeline = _Capture()

    await _process(_processor(_row(new), pipeline), new)

    [ctx] = pipeline.applied
    assert ctx.settings[PROVIDED_PROPERTIES].values["status"].value == "new"
    refused.assert_not_called()
    assert old != new


@pytest.mark.parametrize(
    "revision", [None, "", "other", "pb1:short:short", "pb1:" + "a" * 64], ids=str
)
async def test_a_record_without_a_pushed_revision_tag_is_not_provided(
    refused: MagicMock, revision: str | None
) -> None:
    """A push-origin record by flag whose revision cannot be read: nothing to compare, so nothing is taken."""
    data = _pushed(_props())
    pipeline = _Capture()

    await _process(_processor(_row(data, externalRevisionId=revision), pipeline), data)

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings
    refused.assert_called_once_with("unbound")


async def test_a_none_member_of_a_superseded_run_is_not_stamped_as_none(
    refused: MagicMock,
) -> None:
    """The record has values now; a run of the bytes without any must not stamp 'absent' over them."""
    old = _pushed()
    new = _pushed(_props())
    pipeline = _Capture()

    await _process(_processor(_row(new), pipeline), old)

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings


async def test_the_comparison_is_over_the_downloaded_bytes_not_a_re_serialised_dict(
    refused: MagicMock,
) -> None:
    data = _pushed(_props())
    pipeline = _Capture()

    await _process(
        _processor(_row(data), pipeline), json.loads(data)
    )  # a dict has no downloaded bytes

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings
    refused.assert_called_once_with("unbound_input")


@pytest.mark.parametrize(
    "text",
    [
        _pushed(_props()).decode(),
        '{"blocks": [], "block_groups": [], "push_format": 1, "x": "\ud800"}',
    ],
    ids=["text", "lone surrogate"],
)
async def test_a_text_download_is_not_bound_and_never_raises(
    refused: MagicMock, text: str
) -> None:
    """The run hashes bytes for contentRev, so a text has nothing comparable: the true reason is counted, not 'superseded'."""
    data = _pushed(_props())
    pipeline = _Capture()

    events = await _process(_processor(_row(data), pipeline), text)

    [ctx] = pipeline.applied
    assert PROVIDED_PROPERTIES not in ctx.settings
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    refused.assert_called_once_with("unbound_input")


def test_the_downloaded_revision_never_raises() -> None:
    from app.events.processor import _downloaded_revision

    assert _downloaded_revision("\ud800") is None
    assert _downloaded_revision(object()) is None
    assert _downloaded_revision(None) is None
    assert _downloaded_revision(b"abc") == C.content_rev(b"abc")


async def test_a_large_payload_is_converted_off_the_loop_and_a_small_one_inline() -> (
    None
):
    import threading

    from app.utils.cpu_offload import DEFAULT_OFFLOAD_THRESHOLD_BYTES

    seen: list[tuple[int, int]] = []
    real = processor_module.push_properties

    def spy(doc: object) -> object:
        seen.append((len(json.dumps(doc)), threading.get_ident()))
        return real(doc)

    loop_thread = threading.get_ident()
    big_text = "x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1024)
    for text in ("small", big_text):
        data = _pushed(_props(), pad=text)
        with patch.object(processor_module, "push_properties", spy):
            await _process(_processor(_row(data), _Capture()), data)

    [(_, small_thread), (_, big_thread)] = seen
    assert small_thread == loop_thread, "a small payload is converted inline"
    assert big_thread != loop_thread, "a large payload is converted in a worker thread"


# --- the artifact set (real sink, real writer, fake storage) -----------------------------------------------


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    store_module.ARTIFACT_ROUTES.reset()
    store_module.PROPERTIES_FILE.reset()
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


async def _index(
    storage: FakeStorage, row_data: bytes, data: bytes, *, writer_on: bool = True
) -> list[Any]:
    row = _row(row_data)
    logger = logging.getLogger("test-pushed-properties")
    processor = _processor(row, None)
    sink = SinkOrchestrator(
        graphdb=AsyncMock(),
        blob_storage=AsyncMock(),
        vector_store=AsyncMock(),
        graph_provider=processor.graph_provider,
        logger=logger,
        config_service=MagicMock(),
    )
    sink.vector_store.apply = AsyncMock(return_value=True)
    sink._save_reconciliation_metadata = AsyncMock()  # type: ignore[method-assign]
    sink._sync_record_identity_entities = AsyncMock()  # type: ignore[method-assign]
    sink._stored_run_is = AsyncMock(return_value=True)  # type: ignore[method-assign]
    sink.artifact_writer = ArtifactWriter(
        NodeArtifactStore(storage.blob_storage(), logger), logger
    )

    class _Pipeline:
        async def apply(self, ctx: Any) -> None:  # noqa: ANN401
            await sink.index(ctx)

    processor._pipeline = lambda: _Pipeline()  # type: ignore[method-assign]
    with (
        patch.object(
            sink_module, "is_artifact_write_enabled", AsyncMock(return_value=writer_on)
        ),
        patch.object(
            sink_module,
            "artifact_settings",
            AsyncMock(return_value=ArtifactSettings("cow", "gen")),
        ),
    ):
        return await _process(processor, data)


def _stored_properties(storage: FakeStorage) -> RecordMetadata | None:
    paths = [p for p in storage.puts if p.endswith(f"/{PROPERTIES_PATH}")]
    assert len(paths) <= 1
    return (
        RecordMetadata.model_validate_json(storage.puts[paths[0]].data)
        if paths
        else None
    )


async def test_pushed_properties_reach_properties_json(storage: FakeStorage) -> None:
    data = _pushed(_props())

    await _index(storage, data, data)

    stored = _stored_properties(storage)
    assert stored is not None, "indexing wrote no properties.json for a pushed record"
    assert {k: v.value for k, v in stored.values.items()} == VALUES
    assert {(v.provenance, v.source) for v in stored.values.values()} == {
        (MetadataProvenance.PROVIDED, "push")
    }


async def test_no_properties_member_writes_no_properties_json(
    storage: FakeStorage,
) -> None:
    data = _pushed()

    events = await _index(storage, data, data)

    assert _stored_properties(storage) is None
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    assert any(p.endswith("/manifest.json") for p in storage.puts), (
        "the set itself is still written"
    )


async def test_malformed_member_writes_no_properties_json(
    storage: FakeStorage, refused: MagicMock
) -> None:
    data = _pushed({"schemaId": "schema-1", "values": ["not", "a", "mapping"]})

    events = await _index(storage, data, data)

    assert _stored_properties(storage) is None
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]


async def test_a_superseded_run_writes_no_properties_json(
    storage: FakeStorage, refused: MagicMock
) -> None:
    old, new = (
        _pushed(_props({"status": "old"})),
        _pushed(_props({"status": "new"}), extra_member=1),
    )

    await _index(storage, new, old)

    assert _stored_properties(storage) is None


async def test_writer_off_indexes_normally(storage: FakeStorage) -> None:
    data = _pushed(_props())

    events = await _index(storage, data, data, writer_on=False)

    assert storage.puts == {}
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]


def test_not_provided_is_the_projections_own_sentinel() -> None:
    assert repr(NOT_PROVIDED) == "NOT_PROVIDED"
