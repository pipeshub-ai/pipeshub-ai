"""``SinkOrchestrator.index`` with artifact writes: when it writes, what it writes, and the
revision pointer it moves afterwards. None of it may fail indexing."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupSubType,
    GroupType,
)
from app.models.entities import MimeTypes, OriginTypes, Record, RecordType
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import ArtifactRef, Manifest, WrittenRevision
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.blob_storage import STORED_RECORD_FIELDS, STORED_REV
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import ReconciliationContext, TransformContext
from tests.unit.modules.artifacts.fake_storage import SIGNATURE, FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REV = "0123456789abcdef"
OLD_REV = "fedcba9876543210"
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
PNG_URI = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"px" * 64).decode()


def _record(blocks: list[Block] | None = None, groups: list[BlockGroup] | None = None) -> Record:
    return Record(
        id="rec-1",
        org_id="org-1",
        virtual_record_id="vr-1",
        record_name="report.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-1",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="conn-1",
        mime_type=MimeTypes.PDF.value,
        block_containers=BlocksContainer(
            blocks=blocks or [Block(index=0, type=BlockType.TEXT, data="hello")],
            block_groups=groups or [],
        ),
    )


def _written(document_id: str = "doc-rev-1") -> WrittenRevision:
    return WrittenRevision(
        manifest=Manifest(virtualRecordId="vr-1", rev=REV, orgId="org-1", producedAt=1, artifacts={}),
        manifest_ref=ArtifactRef(document_id=document_id, root="org-1/PipesHub/record-revisions/vr-1/" + REV,
                                 path="manifest.json"),
    )


class Graph:
    """The two documents ``index`` reads, and the conditional write it makes."""

    def __init__(self, record_doc: dict[str, Any], mapping: dict[str, Any] | None) -> None:
        self.record_doc = record_doc
        self.mapping = mapping
        self.provider = AsyncMock()
        self.provider.get_document = AsyncMock(side_effect=self._get)
        self.provider.batch_update_nodes = AsyncMock(return_value=True)
        self.provider.update_node_fields_if_match = AsyncMock(side_effect=self._cas)
        self.race_lost = False

    async def _get(self, key: str, collection: str) -> dict[str, Any] | None:
        if collection == MAPPING:
            return None if self.mapping is None else dict(self.mapping)
        return dict(self.record_doc)

    async def _cas(self, key: str, collection: str, updates: dict, expected: dict) -> bool:
        assert collection == MAPPING and key == "vr-1"
        if self.race_lost or self.mapping is None:
            return False
        if self.mapping.get("currentRev") != expected["currentRev"]:
            return False
        self.mapping.update(updates)
        return True


@pytest.fixture(autouse=True)
def _quiet_side_effects() -> Iterator[None]:
    store_module.ARTIFACT_ROUTES.reset()
    with patch.object(sink_module, "notify_record_indexed", AsyncMock()):
        yield


def _orchestrator(graph: Graph, *, flag: bool = True) -> SinkOrchestrator:
    vector_store = AsyncMock()
    vector_store.apply = AsyncMock(return_value=True)
    orch = SinkOrchestrator(
        graphdb=AsyncMock(),
        blob_storage=AsyncMock(),
        vector_store=vector_store,
        graph_provider=graph.provider,
        logger=logging.getLogger("test-sink-artifacts"),
        config_service=MagicMock(),
    )
    orch._save_reconciliation_metadata = AsyncMock()
    orch._sync_record_identity_entities = AsyncMock()
    orch.image_describer.annotate = AsyncMock(return_value=0)
    orch.artifact_writer = MagicMock()
    orch.artifact_writer.write_revision = AsyncMock(return_value=_written())
    orch._flag = flag  # type: ignore[attr-defined]
    return orch


async def _index(orch: SinkOrchestrator, ctx: TransformContext) -> None:
    with patch.object(sink_module, "is_artifact_write_enabled", AsyncMock(return_value=orch._flag)):  # type: ignore[attr-defined]
        await orch.index(ctx)


def _graph(mapping: dict[str, Any] | None = None, **record_doc: Any) -> Graph:  # noqa: ANN401
    return Graph({"indexingStatus": "NOT_STARTED", "contentRev": REV, **record_doc},
                 {"id": "vr-1", "record_doc_id": "doc-1"} if mapping is None else mapping)


class TestWhenItWrites:
    async def test_flag_off_calls_nothing(self) -> None:
        graph = _graph()
        orch = _orchestrator(graph, flag=False)

        await _index(orch, TransformContext(record=_record()))

        orch.artifact_writer.write_revision.assert_not_awaited()
        graph.provider.update_node_fields_if_match.assert_not_awaited()

    async def test_no_content_rev_writes_nothing(self) -> None:
        graph = _graph(contentRev=None)
        orch = _orchestrator(graph)

        await _index(orch, TransformContext(record=_record()))

        orch.artifact_writer.write_revision.assert_not_awaited()
        graph.provider.update_node_fields_if_match.assert_not_awaited()

    @pytest.mark.parametrize("setting", ["skip_blob", "skip_vector_store", "sink_only"])
    async def test_runs_that_store_no_record_write_nothing(self, setting: str) -> None:
        orch = _orchestrator(_graph())

        await _index(orch, TransformContext(record=_record(), settings={setting: True}))

        orch.artifact_writer.write_revision.assert_not_awaited()

    async def test_writes_the_rev_after_the_stored_record(self) -> None:
        order: list[str] = []
        graph = _graph()
        orch = _orchestrator(graph)
        orch.blob_storage.apply = AsyncMock(side_effect=lambda ctx: order.append("blob"))
        orch.artifact_writer.write_revision = AsyncMock(
            side_effect=lambda *a, **kw: order.append("artifacts") or _written()
        )
        reconciliation = {"hash_to_block_ids": {"h": ["b"]}, "block_id_to_index": {"b": 0}}

        await _index(orch, TransformContext(
            record=_record(), reconciliation_context=ReconciliationContext(new_metadata=reconciliation),
        ))

        assert order == ["blob", "artifacts"]
        args = orch.artifact_writer.write_revision.await_args
        assert args.args[1:4] == ("org-1", "vr-1", REV)
        assert args.kwargs["reconciliation_metadata"] == reconciliation

    @pytest.mark.parametrize(("content_rev", "expected"), [(REV, REV), (None, None)])
    async def test_the_stored_record_write_is_told_its_revision(
        self, content_rev: str | None, expected: str | None,
    ) -> None:
        graph = _graph(contentRev=content_rev)
        orch = _orchestrator(graph)
        seen: list[Any] = []
        orch.blob_storage.apply = AsyncMock(side_effect=lambda ctx: seen.append(dict(ctx.settings)))

        await _index(orch, TransformContext(record=_record()))

        assert seen[0][STORED_REV] == expected
        if content_rev:
            # Recorded before the pointer moves, so a served set is always the stored revision.
            assert graph.mapping["currentRev"] == REV

    @pytest.mark.parametrize("mapping_write_fails", [False, True])
    async def test_the_vectors_move_only_after_the_stored_revision_is_recorded(
        self, mapping_write_fails: bool,
    ) -> None:
        """Between the stored record's upload and its mapping write the mapping still says the old
        revision for both ``storedRev`` and ``currentRev``, so the old set is served. That is the
        revision the vectors hold until then: they are written after the blob write, and not at all
        when the mapping write fails. Clearing ``storedRev`` first would serve the new stored
        record against the old vectors instead."""
        old = {"id": "vr-1", "record_doc_id": "doc-1", "currentRev": OLD_REV, "storedRev": OLD_REV,
               "manifestDocId": "doc-old"}
        graph = _graph(dict(old))
        orch = _orchestrator(graph)
        seen_between: list[tuple[dict[str, Any], bool]] = []

        async def blob_write(ctx: TransformContext) -> None:
            # The new revision is uploaded; the mapping write comes next.
            seen_between.append((dict(graph.mapping or {}), orch.vector_store.apply.await_count > 0))
            if mapping_write_fails:
                raise RuntimeError("Failed to store virtual record mapping")
            graph.mapping = {**(graph.mapping or {}), "storedRev": ctx.settings[STORED_REV]}

        orch.blob_storage.apply = AsyncMock(side_effect=blob_write)

        if mapping_write_fails:
            with pytest.raises(RuntimeError):
                await _index(orch, TransformContext(record=_record()))
        else:
            await _index(orch, TransformContext(record=_record()))

        assert seen_between == [(old, False)]
        if mapping_write_fails:
            orch.vector_store.apply.assert_not_awaited()
            assert graph.mapping == old
        else:
            orch.vector_store.apply.assert_awaited_once()
            assert (graph.mapping["storedRev"], graph.mapping["currentRev"]) == (REV, REV)

    async def test_a_re_embed_tells_no_revision(self) -> None:
        orch = _orchestrator(_graph())
        ctx = TransformContext(record=_record(), settings={"skip_blob": True})

        await _index(orch, ctx)

        orch.blob_storage.apply.assert_not_awaited()
        assert STORED_REV not in ctx.settings

    async def test_the_stored_copys_identity_goes_to_the_writer(self) -> None:
        orch = _orchestrator(_graph())
        owner = {"id": "rec-owner", "record_name": "Owner's name"}

        def store(ctx: TransformContext) -> None:
            ctx.settings[STORED_RECORD_FIELDS] = owner

        orch.blob_storage.apply = AsyncMock(side_effect=store)

        await _index(orch, TransformContext(record=_record()))

        assert orch.artifact_writer.write_revision.await_args.kwargs["stored_record_fields"] == owner

    async def test_without_a_report_of_the_stored_copy_record_json_is_the_record(self) -> None:
        orch = _orchestrator(_graph())

        await _index(orch, TransformContext(record=_record()))

        assert orch.artifact_writer.write_revision.await_args.kwargs["stored_record_fields"] is None

    async def test_sql_tables_keep_the_same_row_limit_as_the_stored_record(self) -> None:
        rows = [Block(index=i, type=BlockType.TABLE_ROW, data={"row": i}) for i in range(25)]
        group = BlockGroup(index=0, type=GroupType.TABLE, sub_type=GroupSubType.SQL_TABLE,
                           children=BlockGroupChildren.from_indices(list(range(25))))
        orch = _orchestrator(_graph())
        stored: list[int] = []
        orch.blob_storage.apply = AsyncMock(
            side_effect=lambda ctx: stored.append(len(ctx.record.block_containers.blocks))
        )
        ctx = TransformContext(record=_record(rows, [group]))

        await _index(orch, ctx)

        sent = orch.artifact_writer.write_revision.await_args.args[4]
        assert len(sent.blocks) == stored[0] == SinkOrchestrator.LIMIT_SQL_ROW_BLOCKS_TO
        assert len(ctx.record.block_containers.blocks) == 25

    async def test_a_failed_write_never_fails_indexing(self, caplog: pytest.LogCaptureFixture) -> None:
        graph = _graph()
        orch = _orchestrator(graph)
        orch.artifact_writer.write_revision = AsyncMock(
            side_effect=aiohttp.ClientError(f"PUT https://bucket/x?sig={SIGNATURE} failed")
        )
        counted = MagicMock()

        with patch.object(sink_module, "record_artifact_write", counted), caplog.at_level(logging.WARNING):
            await _index(orch, TransformContext(record=_record()))

        orch.vector_store.apply.assert_awaited_once()
        assert graph.provider.batch_update_nodes.await_args.args[0][0]["indexingStatus"] == "COMPLETED"
        counted.assert_called_once_with("failed")
        assert "not written" in caplog.text and SIGNATURE not in caplog.text
        graph.provider.update_node_fields_if_match.assert_not_awaited()


def _ticket_graph(ticket_doc: dict[str, Any] | None, **record_doc: Any) -> Graph:  # noqa: ANN401
    graph = _graph(**{"recordType": RecordType.TICKET.value, **record_doc})
    plain = graph.provider.get_document.side_effect

    async def get(key: str, collection: str) -> dict[str, Any] | None:
        if collection == CollectionNames.TICKETS.value:
            return None if ticket_doc is None else dict(ticket_doc)
        return await plain(key, collection)

    graph.provider.get_document = AsyncMock(side_effect=get)
    return graph


def _collections_read(graph: Graph) -> list[str]:
    return [c.args[1] for c in graph.provider.get_document.await_args_list]


_BASE_DOC = {"_key": "rec-1", "orgId": "org-1", "recordName": "PA-1", "recordType": "TICKET",
             "externalRecordId": "PA-1", "version": 1, "origin": "CONNECTOR", "connectorName": "JIRA",
             "connectorId": "conn-1", "createdAtTimestamp": 1, "updatedAtTimestamp": 2}


class TestTicketProperties:
    async def test_a_ticket_reads_its_tickets_document_once(self) -> None:
        graph = _ticket_graph({"status": "open", "assignee": "Ada", "assigneeEmail": "ada@example.com"},
                              **_BASE_DOC)
        orch = _orchestrator(graph)

        await _index(orch, TransformContext(record=_record()))

        assert _collections_read(graph).count(CollectionNames.TICKETS.value) == 1
        properties = orch.artifact_writer.write_revision.await_args.kwargs["properties"]
        assert {k: v.value for k, v in properties.values.items()} == {
            "status": "OPEN", "assignee": "Ada", "assigneeEmail": "ada@example.com",
        }

    async def test_hidden_emails_stay_out(self) -> None:
        graph = _ticket_graph({"assignee": "Ada", "assigneeEmail": "583231", "is_email_hidden": True}, **_BASE_DOC)
        orch = _orchestrator(graph)

        await _index(orch, TransformContext(record=_record()))

        properties = orch.artifact_writer.write_revision.await_args.kwargs["properties"]
        assert set(properties.values) == {"assignee"}

    @pytest.mark.parametrize("record_type", ["FILE", "WEBPAGE", "MAIL", None])
    async def test_other_records_read_no_tickets_document(self, record_type: str | None) -> None:
        graph = _graph(recordType=record_type)
        orch = _orchestrator(graph)

        await _index(orch, TransformContext(record=_record()))

        assert CollectionNames.TICKETS.value not in _collections_read(graph)
        assert orch.artifact_writer.write_revision.await_args.kwargs["properties"] is None

    async def test_with_artifact_writes_off_a_ticket_reads_nothing_more(self) -> None:
        graph = _ticket_graph({"status": "OPEN"}, **_BASE_DOC)

        await _index(_orchestrator(graph, flag=False), TransformContext(record=_record()))

        assert CollectionNames.TICKETS.value not in _collections_read(graph)

    @pytest.mark.parametrize("failure", ["missing", "unreadable", "raises"])
    async def test_a_ticket_document_it_cannot_use_never_fails_indexing(self, failure: str) -> None:
        graph = _ticket_graph(None if failure == "missing" else {"status": "OPEN"},
                              **({**_BASE_DOC, "origin": "NOT AN ORIGIN"} if failure == "unreadable" else _BASE_DOC))
        if failure == "raises":
            plain = graph.provider.get_document.side_effect

            async def get(key: str, collection: str) -> dict[str, Any] | None:
                if collection == CollectionNames.TICKETS.value:
                    raise ConnectionError("graph down")
                return await plain(key, collection)

            graph.provider.get_document = AsyncMock(side_effect=get)
        orch = _orchestrator(graph)

        await _index(orch, TransformContext(record=_record()))

        assert orch.artifact_writer.write_revision.await_args.kwargs["properties"] is None
        assert graph.provider.batch_update_nodes.await_args.args[0][0]["indexingStatus"] == "COMPLETED"


class TestRevisionPointer:
    async def test_flips_when_the_pointer_still_holds_what_was_read(self) -> None:
        graph = _graph({"id": "vr-1", "record_doc_id": "doc-1", "currentRev": OLD_REV, "manifestDocId": "doc-old"})

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        graph.provider.update_node_fields_if_match.assert_awaited_once_with(
            "vr-1", MAPPING,
            {"currentRev": REV, "previousRev": OLD_REV, "manifestDocId": "doc-rev-1"},
            {"currentRev": OLD_REV},
        )
        assert graph.mapping["currentRev"] == REV

    async def test_first_flip_expects_no_current_rev(self) -> None:
        graph = _graph()

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        assert graph.provider.update_node_fields_if_match.await_args.args[3] == {"currentRev": None}
        assert graph.mapping == {"id": "vr-1", "record_doc_id": "doc-1", "currentRev": REV,
                                 "previousRev": None, "manifestDocId": "doc-rev-1"}

    async def test_a_lost_race_leaves_the_pointer(self) -> None:
        graph = _graph({"id": "vr-1", "currentRev": OLD_REV})
        graph.race_lost = True

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        graph.provider.update_node_fields_if_match.assert_awaited_once()
        assert graph.mapping["currentRev"] == OLD_REV

    async def test_a_run_the_record_has_left_does_not_move_the_pointer(self) -> None:
        """A newer revision indexed meanwhile: the slower, older run must not point back at its rev."""
        graph = _graph({"id": "vr-1", "currentRev": OLD_REV, "manifestDocId": "doc-old"})
        reads = 0
        original = graph._get

        async def get(key: str, collection: str) -> dict[str, Any] | None:
            nonlocal reads
            doc = await original(key, collection)
            if collection != MAPPING:
                reads += 1
                # The first read is the run's own; by the flip the record is on a newer rev.
                if reads > 1 and doc is not None:
                    doc["contentRev"] = "ffffffffffffffff"
            return doc

        graph.provider.get_document = AsyncMock(side_effect=get)

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        graph.provider.update_node_fields_if_match.assert_not_awaited()
        assert graph.mapping["currentRev"] == OLD_REV

    async def test_already_current_is_left_alone(self) -> None:
        graph = _graph({"id": "vr-1", "currentRev": REV, "manifestDocId": "doc-rev-1"})

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        graph.provider.update_node_fields_if_match.assert_not_awaited()

    async def test_no_flip_without_a_manifest(self) -> None:
        graph = _graph()
        orch = _orchestrator(graph)
        orch.artifact_writer.write_revision = AsyncMock(return_value=None)

        await _index(orch, TransformContext(record=_record()))

        graph.provider.update_node_fields_if_match.assert_not_awaited()

    async def test_no_flip_when_the_record_is_gone_before_completed(self) -> None:
        graph = _graph()
        graph.provider.batch_update_nodes = AsyncMock(return_value=False)

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        graph.provider.update_node_fields_if_match.assert_not_awaited()

    async def test_a_graph_error_on_the_flip_never_fails_indexing(self) -> None:
        graph = _graph()
        graph.provider.update_node_fields_if_match = AsyncMock(side_effect=RuntimeError("graph down"))

        await _index(_orchestrator(graph), TransformContext(record=_record()))

        assert graph.provider.batch_update_nodes.await_args.args[0][0]["indexingStatus"] == "COMPLETED"


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


async def test_end_to_end_the_stored_record_keeps_images_inline(storage: FakeStorage) -> None:
    """The stored record is written from the record as parsed; only the artifact
    set externalises images."""
    graph = _graph()
    orch = _orchestrator(graph)
    logger = logging.getLogger("test-sink-artifacts")
    orch.artifact_writer = ArtifactWriter(NodeArtifactStore(storage.blob_storage(), logger), logger)
    stored_blocks: list[dict] = []
    orch.blob_storage.apply = AsyncMock(
        side_effect=lambda ctx: stored_blocks.extend(
            b.model_dump(mode="json") for b in ctx.record.block_containers.blocks
        )
    )
    image = Block(index=0, type=BlockType.IMAGE, data={"uri": PNG_URI})

    await _index(orch, TransformContext(record=_record([image])))

    assert stored_blocks[0]["data"] == {"uri": PNG_URI}
    root = FakeStorage.root("vr-1", REV)
    artifact_block = storage.puts[f"{root}/blocks/part-0000.jsonl"].data
    assert b'"asset":"images/' in artifact_block and PNG_URI.encode() not in artifact_block
    assert storage.put_order[-1] == "manifest.json"
    assert graph.mapping["currentRev"] == REV
    assert graph.mapping["manifestDocId"] == FakeStorage.document_id("vr-1", REV)
