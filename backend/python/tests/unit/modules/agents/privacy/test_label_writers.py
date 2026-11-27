"""The tools and services that put a record or a container in front of a model
name it for the answer's label, by record id, as they show it.

Each test binds a ledger that records what it is told, runs the real writer,
and checks the ids it named.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.models import NavigationView, NodeRef, NodeRow
from app.modules.agents.privacy.ledger import LabelLedger, bind_ledger


class _Recording(LabelLedger):
    labels = True

    def __init__(self) -> None:
        self.records: set[str] = set()
        self.vrids: set[str] = set()
        self.nodes: set[str] = set()
        self.artifacts: list[tuple[str, str | None]] = []
        self.unrun: list[tuple[str, str | None, int | None]] = []

    def shown(self, *, records=(), vrids=(), nodes=(), call_id=None) -> None:
        self.records.update(records)
        self.vrids.update(vrids)
        self.nodes.update(nodes)

    def artifact_read(
        self, artifact_id: str, run_id: str | None = None, *, conversation_id: str | None = None,
        written_at: int | None = None,
    ) -> None:
        self.artifacts.append((artifact_id, run_id))
        if conversation_id is not None or written_at is not None:
            self.unrun.append((artifact_id, conversation_id, written_at))


def _row(node_id: str, node_type: str) -> NodeRow:
    return NodeRow(
        id=node_id, name=node_id, node_type=node_type, sub_type=None,
        is_record=node_type in ("record", "folder"), has_children=False, detail=None,
    )


class TestNavigation:
    def test_a_navigation_names_its_records_and_its_containers_and_the_path_to_them(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import (
            _label_ids_in_view,
        )

        view = NavigationView(
            current=NodeRef(id="rg-falcon", name="Falcon", node_type="recordGroup", sub_type=None, is_record=False),
            breadcrumbs=[
                NodeRef(id="app-drive", name="Drive", node_type="app", sub_type=None, is_record=False),
                NodeRef(id="folder-deals", name="Deals", node_type="folder", sub_type=None, is_record=True),
            ],
            rows=[_row("rec-deck", "record"), _row("folder-q3", "folder"), _row("rg-sub", "recordGroup")],
            related=[_row("rec-memo", "record")],
            pagination=None, web_url=None, indexing_status=None, connector=None,
        )

        ids = _label_ids_in_view(view)

        assert set(ids["records"]) == {"folder-deals", "rec-deck", "folder-q3", "rec-memo"}
        assert set(ids["nodes"]) == {"rg-falcon", "app-drive", "rg-sub"}


class TestListingCitations:
    async def test_each_record_a_listing_cites_is_named(self) -> None:
        from app.agents.actions.knowledge_graph import listing_citations

        graph = MagicMock()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"_key": "rec-1", "recordName": "Deck"}])
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch.object(listing_citations, "resolve_frontend_url", AsyncMock(return_value="https://app.example")),
            patch.object(listing_citations, "attach_connector_instance_names", AsyncMock()),
        ):
            refs = await listing_citations.cite_listed_records({}, graph, "org-1", {"rec-1": "created Oct 26"})

        assert refs and ledger.records == {"rec-1"}


class TestDownloadLink:
    async def test_the_record_behind_a_download_link_is_named(self) -> None:
        from app.agents.actions.artifacts.artifacts import ArtifactManager
        from app.config.constants.arangodb import OriginTypes

        record = MagicMock(origin=OriginTypes.UPLOAD, external_record_id="blob-1", record_name="deck.pdf", mime_type="x")
        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(return_value=record)
        blob = MagicMock()
        blob.get_download_url = AsyncMock(return_value="https://signed.example/x")
        state = {"graph_provider": graph, "blob_store": blob, "org_id": "org-1", "user_id": "user-1"}
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch("app.services.record_content.TieredRecordAuthorizer.authorize", AsyncMock()),
            patch("app.agents.actions.knowledge_graph.ops.scope.ids_outside_scope", AsyncMock(return_value=set())),
        ):
            ok, _ = await ArtifactManager(state).get_record_download_url("rec-deck")

        assert ok and ledger.records == {"rec-deck"}

    async def test_a_refused_record_is_not_named(self) -> None:
        from app.agents.actions.artifacts.artifacts import ArtifactManager
        from app.services.record_content import RecordAccessDeniedError

        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(return_value=MagicMock())
        state = {"graph_provider": graph, "blob_store": MagicMock(), "org_id": "org-1", "user_id": "user-1"}
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch(
                "app.services.record_content.TieredRecordAuthorizer.authorize",
                AsyncMock(side_effect=RecordAccessDeniedError("no")),
            ),
        ):
            ok, _ = await ArtifactManager(state).get_record_download_url("rec-deck")

        assert not ok and ledger.records == set()


class TestCodeGraph:
    async def test_the_records_a_check_admits_are_named(self) -> None:
        from app.agents.actions.code_graph.ops import RecordAccess

        graph = MagicMock()
        graph.filter_accessible_record_ids = AsyncMock(return_value={"rec-a"})
        ledger = _Recording()
        with bind_ledger(ledger):
            admitted = await RecordAccess(graph, "org-1", "user-1").readable(["rec-a", "rec-b"])

        assert admitted == {"rec-a"} and ledger.records == {"rec-a"}


class TestSqlTables:
    async def test_live_rows_are_named_by_the_connector_they_were_read_from(self) -> None:
        from app.utils import execute_query

        tool = execute_query.create_execute_query_tool(
            config_service=MagicMock(), graph_provider=MagicMock(), org_id="org-1", user_id="user-1",
            allowed_connector_ids={"pg-1"},
        )
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch.object(execute_query, "_connector_is_accessible", AsyncMock(return_value=True)),
            patch.object(execute_query, "_connector_is_public_only", AsyncMock(return_value=False)),
            patch.object(execute_query, "_execute_query_impl", AsyncMock(return_value={"ok": False, "error": "x"})),
        ):
            graph_doc = AsyncMock(return_value={"type": "POSTGRESQL"})
            tool_kwargs = {"query": "select 1", "source_name": "PostgreSQL", "connector_id": "pg-1"}
            with patch.object(execute_query, "_connector_doc", graph_doc, create=True):
                await tool.coroutine(**tool_kwargs)

        assert ledger.nodes == {"pg-1"}


class TestSlack:
    def test_thread_records_returned_are_named(self) -> None:
        from app.utils.fetch_slack_thread import _note_shown_records

        ledger = _Recording()
        with bind_ledger(ledger):
            _note_shown_records([{"id": "rec-msg-1", "virtual_record_id": "v-1"}, {"_key": "rec-msg-2"}])

        assert ledger.records == {"rec-msg-1", "rec-msg-2"} and ledger.vrids == {"v-1"}

    async def test_a_live_channel_read_is_named_by_its_record_group(self) -> None:
        from app.utils import fetch_slack_nearby_messages as nearby

        graph = MagicMock()
        graph.get_record_group_by_external_id = AsyncMock(return_value={"id": "rg-chan", "orgId": "org-1"})
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch("app.utils.fetch_slack_thread.resolve_user_key", AsyncMock(return_value="ukey")),
            patch("app.utils.fetch_slack_thread.user_can_access_node", AsyncMock(return_value=True)),
            patch("app.utils.chat_helpers.accessible_node_ids", AsyncMock(return_value={"rg-chan"})),
        ):
            allowed = await nearby._user_can_read_channel(
                graph, connector_id="slack-1", channel_id="C1", user_id="user-1", org_id="org-1",
            )

        assert allowed and ledger.nodes == {"rg-chan"}


class TestSelectedNodes:
    async def test_the_selected_nodes_named_in_the_prompt_are_named(self) -> None:
        from app.agents.actions.knowledge_graph.ops import scope
        from app.modules.retrieval.selection_scope import NamedNode

        named = [NamedNode(key="recordGroups", id="rg-falcon", doc={"name": "Falcon"})]
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch("app.modules.retrieval.selection_scope.resolve_user_key", AsyncMock(return_value="ukey")),
            patch("app.modules.retrieval.selection_scope.resolve_request_scopes", AsyncMock(return_value=(None, []))),
            patch("app.modules.retrieval.selection_scope.readable_named_nodes", AsyncMock(return_value=named)),
        ):
            text = await scope.list_selected_nodes(
                {"filters": {"recordGroups": ["rg-falcon"]}}, MagicMock(), "user-1", "org-1",
            )

        assert text and "rg-falcon" in text and ledger.nodes == {"rg-falcon"}


class TestArtifactRegistry:
    async def test_an_artifact_read_or_listed_is_named_with_the_run_that_wrote_it(self) -> None:
        from app.services.artifact_registry.registry import ArtifactRegistryService

        registry = ArtifactRegistryService(MagicMock(), MagicMock())
        registry._access = MagicMock()
        # The record, and the version the reader is shown (the first: the creating run's).
        registry._access.authorize_version = AsyncMock(return_value=({"externalRecordId": "blob-1"}, 1))
        ledger = _Recording()
        with bind_ledger(ledger):
            record = await registry._authorized_record(MagicMock(), {"_key": "art-1", "runId": "run-0"})

        assert record is not None
        assert ledger.artifacts == [("art-1", "run-0")]

    async def test_an_artifact_no_run_wrote_is_named_with_its_chat_and_when_that_version_was_written(self) -> None:
        # As an older release stored it: no run on its document. Its record says when version 1 was written.
        from app.services.artifact_registry.registry import ArtifactRegistryService

        registry = ArtifactRegistryService(MagicMock(), MagicMock())
        registry._access = MagicMock()
        registry._access.authorize_version = AsyncMock(
            return_value=({"externalRecordId": "blob-1", "createdAtTimestamp": 1_000, "updatedAtTimestamp": 2_000}, 1),
        )
        ledger = _Recording()
        with bind_ledger(ledger):
            await registry._authorized_record(MagicMock(), {"_key": "art-1", "conversationId": "conv-1"})

        assert ledger.artifacts == [("art-1", None)]
        assert ledger.unrun == [("art-1", "conv-1", 1_000)]

    async def test_a_later_version_no_run_kept_is_named_with_the_time_its_entry_says(self) -> None:
        # Versioned before runs were kept per version: its entry has a time and no run.
        from app.services.artifact_registry.registry import ArtifactRegistryService

        registry = ArtifactRegistryService(MagicMock(), MagicMock())
        registry._access = MagicMock()
        registry._access.authorize_version = AsyncMock(return_value=({"externalRecordId": "blob-1", "createdAtTimestamp": 1_000}, 2))
        doc = {
            "_key": "art-1",
            "conversationId": "conv-1",
            "runId": "run-0",
            "versions": [{"registryVersion": 1, "createdAt": 1_000}, {"registryVersion": 2, "createdAt": 3_000}],
        }
        ledger = _Recording()
        with bind_ledger(ledger):
            await registry._authorized_record(MagicMock(), doc)

        assert ledger.unrun == [("art-1", "conv-1", 3_000)]

    async def test_an_artifact_the_reader_may_not_open_is_not_named(self) -> None:
        from app.services.artifact_registry.registry import ArtifactRegistryService

        registry = ArtifactRegistryService(MagicMock(), MagicMock())
        registry._access = MagicMock()
        registry._access.authorize_version = AsyncMock(side_effect=PermissionError("no"))
        ledger = _Recording()
        with bind_ledger(ledger):
            assert await registry._authorized_record(MagicMock(), {"_key": "art-1"}) is None

        assert ledger.artifacts == []


class TestArtifactReadById:
    """A read by id (content, a download link) authorizes against the artifact's record, which does not hold the run
    that wrote it; that is on the artifact's own document. Told no run, the label takes even the run's own new file
    for an earlier answer's, and the answer that saved a file is hidden from everyone, its asker included."""

    @staticmethod
    def _registry(artifact_doc: dict | None, *, fails: bool = False) -> Any:  # noqa: ANN401
        from app.services.artifact_registry.registry import ArtifactRegistryService

        graph = MagicMock()
        graph.get_document = AsyncMock(side_effect=ConnectionError("graph down")) if fails else AsyncMock(return_value=artifact_doc)
        registry = ArtifactRegistryService(graph, MagicMock())
        registry._access = MagicMock()
        registry._access.authorize_version = AsyncMock(
            return_value=({"externalRecordId": "blob-1", "version": 1, "orgId": "org-1"}, 1),
        )
        # The file's owner, who is given the signed link.
        registry._access.opens_every_version = AsyncMock(return_value=True)
        registry._urls = MagicMock()
        registry._urls.get_download_url = AsyncMock(return_value="https://files.example/signed")
        return registry

    @staticmethod
    async def _read(registry: Any, read: str) -> None:  # noqa: ANN401
        actor = MagicMock(org_id="org-1")
        with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", AsyncMock(return_value=b"x")):
            await getattr(registry, read)(actor=actor, artifact_id="art-1")

    @pytest.mark.parametrize("read", ["get_download_url", "get_content"])
    async def test_it_is_named_with_the_run_that_wrote_it(self, read: str) -> None:
        registry, ledger = self._registry({"_key": "art-1", "runId": "run-1"}), _Recording()
        with bind_ledger(ledger):
            await self._read(registry, read)
        assert ledger.artifacts == [("art-1", "run-1")]

    @pytest.mark.parametrize("read", ["get_download_url", "get_content"])
    async def test_one_no_run_wrote_is_named_with_its_chat_and_when_it_was_written(self, read: str) -> None:
        registry, ledger = self._registry({"_key": "art-1", "conversationId": "conv-1"}), _Recording()
        registry._access.authorize_version = AsyncMock(
            return_value=({"externalRecordId": "blob-1", "version": 1, "orgId": "org-1", "createdAtTimestamp": 5_000}, 1),
        )
        with bind_ledger(ledger):
            await self._read(registry, read)
        assert ledger.artifacts == [("art-1", None)]
        assert ledger.unrun == [("art-1", "conv-1", 5_000)]

    @pytest.mark.parametrize("fails", [False, True], ids=["no-document", "read-fails"])
    async def test_when_the_writer_cant_be_read_it_names_no_run(self, fails: bool) -> None:
        registry, ledger = self._registry(None, fails=fails), _Recording()
        with bind_ledger(ledger):
            await self._read(registry, "get_download_url")
        # No run named: the label takes it for an earlier run's, which hides the answer more, never less. Nothing
        # says it was written before labels either.
        assert ledger.artifacts == [("art-1", None)]
        assert ledger.unrun == []

    async def test_outside_a_labelled_request_nothing_more_is_read(self) -> None:
        registry = self._registry({"_key": "art-1", "runId": "run-1"})
        await self._read(registry, "get_download_url")
        registry._graph_provider.get_document.assert_not_called()


class TestListFiles:
    async def test_a_listing_names_its_containers_as_well_as_its_records(self) -> None:
        from types import SimpleNamespace

        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files
        from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope

        items = [
            SimpleNamespace(nodeType=SimpleNamespace(value="record"), id="rec-1", name="Deck"),
            SimpleNamespace(nodeType=SimpleNamespace(value="recordGroup"), id="rg-falcon", name="Falcon"),
            SimpleNamespace(nodeType=SimpleNamespace(value="app"), id="app-drive", name="Drive"),
        ]
        service = AsyncMock()
        service.get_nodes = AsyncMock(return_value=SimpleNamespace(success=True, items=items, error=None, total=3))
        state: dict[str, Any] = {"graph_provider": AsyncMock(), "org_id": "org-1", "user_id": "u-1", "logger": MagicMock()}
        ledger = _Recording()
        with (
            bind_ledger(ledger),
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock, return_value=KnowledgeScope(app_ids=("app-1",), kb_ids=()),
            ),
            patch("app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService", return_value=service),
            patch("app.agents.actions.knowledge_graph.ops.listing.cite_listed_records", AsyncMock(return_value={})),
            patch("app.utils.chat_helpers.get_record_id_shortener_if_enabled", return_value=None),
        ):
            ok, _ = await execute_list_files(state)

        assert ok
        assert ledger.records == {"rec-1"}
        assert ledger.nodes == {"rg-falcon", "app-drive"}


class TestCoMentions:
    async def test_names_and_counts_name_every_readable_record_they_come_from(self, monkeypatch: Any) -> None:  # noqa: ANN401
        from app.agents.actions.knowledge_graph.ops import co_mentions
        from app.modules.retrieval.entity_permissions import EntityRecordPage

        records = [{"_key": k, "virtualRecordId": f"v-{k}", "recordName": k, "recordType": "FILE"} for k in ("r1", "r2", "r3")]
        monkeypatch.setattr(co_mentions, "load_entity_access_context", AsyncMock(return_value=MagicMock(org_id="org")))
        monkeypatch.setattr(co_mentions, "_named_entities_enabled", AsyncMock(return_value=True))
        monkeypatch.setattr(co_mentions, "resolve_frontend_url", AsyncMock(return_value=None))
        monkeypatch.setattr(
            co_mentions, "list_accessible_entity_records",
            AsyncMock(return_value=EntityRecordPage(records=records, next_cursor=None)),
        )
        monkeypatch.setattr(co_mentions, "record_line", lambda record, *_: f"- {record['_key']}")
        graph = MagicMock()
        graph.get_named_entity_mentions_from_records = AsyncMock(return_value=[
            {"recordId": "r1", "entityId": "e-acme", "kind": "organization", "extractedNames": ["Acme"]},
            {"recordId": "r2", "entityId": "e-acme", "kind": "organization", "extractedNames": ["Acme"]},
            {"recordId": "r3", "entityId": "e-acme", "kind": "organization", "extractedNames": ["Acme"]},
            {"recordId": "r3", "entityId": "p-jane", "kind": "person", "extractedNames": ["Jane"]},
        ])
        graph.resolve_named_entity_redirects = AsyncMock(return_value={})
        ledger = _Recording()
        with bind_ledger(ledger):
            ok, _ = await co_mentions.execute_find_co_mentioned_entities(
                {"graph_provider": graph, "org_id": "org"}, entity_ids=["e-acme"],
            )

        assert ok
        assert ledger.records == {"r1", "r2", "r3"}
