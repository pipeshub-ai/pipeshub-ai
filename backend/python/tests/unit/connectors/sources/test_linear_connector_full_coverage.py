"""Comprehensive unit tests for the Linear connector - full coverage."""

import base64
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import (
    Connectors,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.linear.api_gateway import (
    LinearAuthError,
    LinearRateLimited,
    LinearTransientError,
)
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    PROJECTS_CHECKPOINT_KEY,
    LinearConnector,
    file_record_id,
)
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.graphql.response import GraphQLResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_sync_point() -> MagicMock:
    """An in-memory stand-in for SyncPoint: what is written is what is read back."""
    store = {}

    async def read(key) -> dict:
        return dict(store.get(key) or {})

    async def update(key, data) -> None:
        store[key] = dict(data)

    sync_point = MagicMock()
    sync_point.store = store
    sync_point.read_sync_point = AsyncMock(side_effect=read)
    sync_point.update_sync_point = AsyncMock(side_effect=update)
    return sync_point


def _use_memory_state(connector) -> None:
    """Checkpoints live in memory, retries do not really sleep, and the processor
    calls made when records are removed or refreshed can be awaited."""
    for name in (
        "issues_sync_point", "attachments_sync_point", "documents_sync_point",
        "projects_sync_point", "deletion_sync_point", "reconcile_sync_point",
    ):
        setattr(connector, name, _memory_sync_point())
    connector._reconciler._sync_point = connector.reconcile_sync_point
    connector._sleep = AsyncMock()
    connector._api._sleep = connector._sleep
    # The cheap "did anything change" probe is tested on its own.
    connector._team_has_changes = AsyncMock(return_value=True)
    processor = connector.data_entities_processor
    for name, value in (
        ("get_records_by_parent", []),
        ("get_records_by_record_type", []),
        ("get_records_in_record_group", []),
        ("get_nodes_by_filters", []),
        ("on_records_deleted_cascade", None),
        ("on_records_detached_from_parent", None),
        ("on_record_content_update", None),
        ("on_record_group_deleted", None),
        ("on_user_group_deleted", None),
    ):
        if not isinstance(getattr(processor, name), AsyncMock):
            setattr(processor, name, AsyncMock(return_value=value))


def _make_connector():
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_store_provider = MagicMock()

    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store

        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())
    config_service = AsyncMock()
    connector_id = "linear-conn-1"
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
        scope="personal",
        created_by="test-user-id",
    )
    connector._tx_store = mock_tx_store
    _use_memory_state(connector)
    return connector


def _mock_response(success=True, data=None, message=""):
    response = MagicMock()
    response.success = success
    response.data = data
    response.message = message
    return response


def _mock_linear_org():
    return _mock_response(
        data={
            "organization": {
                "id": "org-linear-1",
                "name": "Test Org",
                "urlKey": "test-org",
            }
        }
    )


def _mock_paginated(key, nodes, has_next=False, end_cursor=None):
    return _mock_response(
        data={
            key: {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": has_next,
                    "endCursor": end_cursor,
                },
            }
        }
    )


def _make_issue_data(
    issue_id="issue-1",
    identifier="ENG-1",
    title="Test Issue",
    team_id="team-1",
    description="Some description",
    created_at="2024-01-15T10:00:00.000Z",
    updated_at="2024-01-15T12:00:00.000Z",
    priority=2,
    state=None,
    assignee=None,
    creator=None,
    parent=None,
    labels=None,
    relations=None,
    comments=None,
    trashed=False,
    archived_at=None,
    url="https://linear.app/test-org/issue/ENG-1",
    attachments=None,
    documents=None,
):
    data = {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "priority": priority,
        "state": state or {"name": "In Progress", "type": "started"},
        "assignee": assignee,
        "creator": creator,
        "parent": parent,
        "labels": labels or {"nodes": []},
        "relations": relations or {"nodes": []},
        "comments": comments or {"nodes": []},
        "trashed": trashed,
        "url": url,
        "team": {"id": team_id},
    }
    if archived_at:
        data["archivedAt"] = archived_at
    if attachments:
        data["attachments"] = attachments
    if documents:
        data["documents"] = documents
    return data


def _make_project_data(
    project_id="proj-1",
    name="Test Project",
    slug_id="test-project",
    content="Project content",
    description="Project desc",
    created_at="2024-01-10T10:00:00.000Z",
    updated_at="2024-01-15T12:00:00.000Z",
    status=None,
    lead=None,
    url="https://linear.app/test-org/project/test-project",
    external_links=None,
    documents=None,
    issues=None,
    comments=None,
    milestones=None,
    project_updates=None,
    trashed=False,
    archived_at=None,
):
    data = {
        "id": project_id,
        "name": name,
        "slugId": slug_id,
        "content": content,
        "description": description,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "status": status,
        "lead": lead,
        "url": url,
        "priorityLabel": "High",
        "externalLinks": external_links or {"nodes": []},
        "documents": documents or {"nodes": []},
        "issues": issues or {"nodes": []},
        "comments": comments or {"nodes": []},
        "projectMilestones": milestones or {"nodes": []},
        "projectUpdates": project_updates or {"nodes": []},
        "trashed": trashed,
    }
    if archived_at:
        data["archivedAt"] = archived_at
    return data


def _make_team_record_group(team_id="team-1", name="Engineering", key="ENG"):
    rg = RecordGroup(
        id="rg-1",
        org_id="org-1",
        external_group_id=team_id,
        connector_id="linear-conn-1",
        connector_name=Connectors.LINEAR,
        name=name,
        short_name=key,
        group_type=RecordGroupType.PROJECT,
    )
    perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
    return rg, perms


# ===================================================================
# Projects: _project_home, _fetch_full_project, _project_to_records
# ===================================================================


def _teams(*teams) -> dict:
    return {"nodes": [{"id": team_id, "private": private} for team_id, private in teams]}


def _page(nodes, has_next=False, end_cursor=None) -> dict:
    return {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}}


class TestProjectHome:
    """A project is filed under its lead team, else its first synced team by id."""

    def test_the_lead_team_is_the_home(self) -> None:
        connector = _make_connector()
        project = {"leadTeamId": "team-b", "teams": _teams(("team-a", False), ("team-b", True))}
        assert connector._project_home(project, {"team-a", "team-b"}) == "team-b"

    @pytest.mark.parametrize("lead_team_id", [None, "team-0"])
    def test_without_a_synced_lead_team_the_first_by_id_is_the_home(self, lead_team_id) -> None:
        connector = _make_connector()
        project = {"leadTeamId": lead_team_id, "teams": _teams(("team-b", False), ("team-a", True))}
        assert connector._project_home(project, {"team-a", "team-b"}) == "team-a"

    def test_first_synced_team_by_id_whatever_its_visibility(self) -> None:
        connector = _make_connector()
        project = {"teams": _teams(("team-b", False), ("team-a", True))}
        assert connector._project_home(project, {"team-a", "team-b"}) == "team-a"

    def test_the_choice_does_not_depend_on_the_order(self) -> None:
        connector = _make_connector()
        scope = {"team-a", "team-b", "team-c"}
        one = {"teams": _teams(("team-c", False), ("team-a", True), ("team-b", False))}
        other = {"teams": _teams(("team-b", False), ("team-c", False), ("team-a", True))}
        assert connector._project_home(one, scope) == connector._project_home(other, scope) == "team-a"

    def test_teams_outside_the_sync_are_ignored(self) -> None:
        connector = _make_connector()
        project = {"teams": _teams(("team-0", False), ("team-b", True), ("team-a", True))}
        assert connector._project_home(project, {"team-a", "team-b"}) == "team-a"

    @pytest.mark.parametrize("project", [
        {"teams": _teams(("team-x", False))},
        {"teams": {"nodes": []}},
        {"teams": None},
        {},
    ])
    def test_no_synced_team_means_no_home(self, project) -> None:
        connector = _make_connector()
        assert connector._project_home(project, {"team-a"}) is None


class TestFetchFullProject:
    @pytest.mark.asyncio
    async def test_returns_the_project(self) -> None:
        connector = _make_connector()
        project = _make_project_data()
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=_mock_response(data={"project": project}))
        mock_ds.execute_operation = AsyncMock()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            result = await connector._fetch_full_project("proj-1")

        assert result is project
        mock_ds.project.assert_awaited_once_with("proj-1")
        # Nothing was cut short, so no follow-up page is asked for.
        mock_ds.execute_operation.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response", [
        GraphQLResponse(success=False, message="Entity not found: Project", status_code=400),
        GraphQLResponse(success=True, data={"project": None}),
        GraphQLResponse(success=True, data=None),
    ])
    async def test_gone_project_is_none(self, response) -> None:
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=response)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            assert await connector._fetch_full_project("proj-1") is None

    @pytest.mark.asyncio
    async def test_unreadable_project_raises_instead_of_looking_gone(self) -> None:
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=GraphQLResponse(success=False, message="Internal error", status_code=500)
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            with pytest.raises(LinearTransientError):
                await connector._fetch_full_project("proj-1")

        assert mock_ds.project.await_count == 4

    @pytest.mark.asyncio
    async def test_reads_nested_lists_to_their_end(self) -> None:
        connector = _make_connector()
        project = _make_project_data(
            external_links=_page([{"id": "link-1"}], has_next=True, end_cursor="links-1"),
            project_updates=_page([
                {"id": "upd-1", "comments": _page([{"id": "c-1"}], has_next=True, end_cursor="comments-1")},
                {"id": "upd-2", "comments": _page([{"id": "c-9"}])},
            ]),
        )
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=_mock_response(data={"project": project}))

        async def execute_operation(operation, variables) -> GraphQLResponse:
            if operation == "projectExternalLinks" and variables["after"] == "links-1":
                return _mock_response(data={"project": {"externalLinks": _page(
                    [{"id": "link-2"}], has_next=True, end_cursor="links-2")}})
            if operation == "projectExternalLinks" and variables["after"] == "links-2":
                return _mock_response(data={"project": {"externalLinks": _page([{"id": "link-3"}])}})
            if operation == "projectUpdateComments":
                return _mock_response(data={"projectUpdate": {"comments": _page([{"id": "c-2"}])}})
            raise AssertionError(f"unexpected {operation} {variables}")

        mock_ds.execute_operation = AsyncMock(side_effect=execute_operation)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            result = await connector._fetch_full_project("proj-1")

        assert [n["id"] for n in result["externalLinks"]["nodes"]] == ["link-1", "link-2", "link-3"]
        updates = result["projectUpdates"]["nodes"]
        assert [n["id"] for n in updates[0]["comments"]["nodes"]] == ["c-1", "c-2"]
        assert [n["id"] for n in updates[1]["comments"]["nodes"]] == ["c-9"]
        assert [c.args for c in mock_ds.execute_operation.await_args_list] == [
            ("projectExternalLinks", {"id": "proj-1", "first": 100, "after": "links-1"}),
            ("projectExternalLinks", {"id": "proj-1", "first": 100, "after": "links-2"}),
            ("projectUpdateComments", {"id": "upd-1", "first": 100, "after": "comments-1"}),
        ]

    @pytest.mark.asyncio
    async def test_failed_follow_up_page_raises(self) -> None:
        connector = _make_connector()
        project = _make_project_data(
            documents=_page([{"id": "doc-1"}], has_next=True, end_cursor="docs-1"),
        )
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=_mock_response(data={"project": project}))
        mock_ds.execute_operation = AsyncMock(
            return_value=GraphQLResponse(success=False, message="Unauthorized", status_code=401)
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            with pytest.raises(LinearAuthError):
                await connector._fetch_full_project("proj-1")


class TestProjectToRecords:
    UPLOAD = "https://uploads.linear.app/org/spec.pdf"

    def _connector(self, project, existing=None) -> LinearConnector:
        connector = _make_connector()
        connector.indexing_filters = None
        connector._fetch_full_project = AsyncMock(return_value=project)
        processor = connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: (
                existing if external_record_id == "proj-1" else None
            )
        )
        connector._remover.remove_record = AsyncMock()
        return connector

    @pytest.mark.asyncio
    async def test_empty_id_reads_nothing(self) -> None:
        connector = self._connector(_make_project_data())
        assert await connector._project_to_records("", {"team-1"}) == []
        connector._fetch_full_project.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gone_project_yields_nothing(self) -> None:
        connector = self._connector(None)
        assert await connector._project_to_records("proj-1", {"team-1"}) == []
        assert "proj-1" not in connector._refreshed_project_ids

    @pytest.mark.asyncio
    async def test_project_of_unsynced_teams_yields_nothing(self) -> None:
        project = _make_project_data()
        project["teams"] = _teams(("team-9", False))
        connector = self._connector(project)
        assert await connector._project_to_records("proj-1", {"team-1"}) == []
        connector.data_entities_processor.get_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_project_with_links_issues_and_files(self) -> None:
        project = _make_project_data(
            content=f"See [spec.pdf]({self.UPLOAD}) and [other](https://files.example.com/other.pdf)",
            external_links=_page([{
                "id": "link-1", "url": "https://example.com/spec", "label": "Spec",
                "createdAt": "2024-01-10T10:00:00.000Z", "updatedAt": "2024-01-11T10:00:00.000Z",
            }]),
            issues=_page([{"id": "issue-1"}, {"id": None}, {"id": "issue-2"}]),
        )
        project["teams"] = _teams(("team-1", False))
        connector = self._connector(project)

        records = await connector._project_to_records("proj-1", {"team-1"})

        by_type = {type(r).__name__: r for r, _ in records}
        assert sorted(by_type) == ["FileRecord", "LinkRecord", "ProjectRecord"]
        # The project comes first: a child written before it finds no parent in its team.
        project_record = records[0][0]
        assert isinstance(project_record, ProjectRecord)
        assert project_record.external_record_id == "proj-1"
        assert project_record.external_record_group_id == "team-1"
        assert [(r.external_record_id, r.record_type, r.relation_type)
                for r in project_record.related_external_records] == [
            ("issue-1", RecordType.TICKET, RecordRelations.LINKED_TO),
            ("issue-2", RecordType.TICKET, RecordRelations.LINKED_TO),
        ]
        link = by_type["LinkRecord"]
        assert link.external_record_id == "link-1"
        assert link.parent_external_record_id == "proj-1"
        assert link.parent_node_id == project_record.id
        # Only the Linear upload becomes a file, under an id of its own per parent.
        file_record = by_type["FileRecord"]
        assert file_record.external_record_id == file_record_id(self.UPLOAD, "proj-1")
        assert file_record.parent_external_record_id == "proj-1"
        assert file_record.external_record_group_id == "team-1"
        # Access comes from the teams; nothing is granted on the records themselves.
        assert all(permissions == [] for _, permissions in records)
        assert all(r.shared_with_me_record_group_ids == [] for r, _ in records)
        assert "proj-1" in connector._refreshed_project_ids
        # A project seen for the first time has no stored children to settle.
        connector.data_entities_processor.get_records_by_parent.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored_updated_at, refreshed", [("same", False), (1, True)])
    async def test_a_project_counts_as_refreshed_only_when_it_is_reindexed(
        self, stored_updated_at, refreshed,
    ) -> None:
        project = _make_project_data()
        project["teams"] = _teams(("team-1", False))
        connector = self._connector(project)
        same = connector._parse_linear_datetime(project["updatedAt"])
        existing = ProjectRecord(
            id="node-proj", org_id="org-1", record_name="Project", record_type=RecordType.PROJECT,
            external_record_id="proj-1", version=3, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR, connector_id=connector.connector_id,
            external_record_group_id="team-1", record_group_type=RecordGroupType.PROJECT,
            source_updated_at=same if stored_updated_at == "same" else stored_updated_at,
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: existing if external_record_id == "proj-1" else None)
        connector.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[])

        await connector._project_to_records("proj-1", {"team-1"})

        # Stored again unchanged it is not re-indexed, so the comment and
        # children-stamp passes must still be free to refresh it.
        assert ("proj-1" in connector._refreshed_project_ids) is refreshed

    @pytest.mark.asyncio
    async def test_every_record_is_filed_under_the_lead_team_only(self) -> None:
        """Shared into the other teams too, the documents, links and files were listed at
        the top of each of those teams as well as under the project. The complete, empty
        list drops the groups a record was shared into before."""
        project = _make_project_data(
            content=f"[spec.pdf]({self.UPLOAD})",
            external_links=_page([{"id": "link-1", "url": "https://example.com/spec", "label": "Spec"}]),
        )
        project["teams"] = _teams(("team-1", True), ("team-2", True))
        project["leadTeamId"] = "team-2"
        connector = self._connector(project)

        records = await connector._project_to_records("proj-1", {"team-1", "team-2"})

        assert len(records) == 3
        assert isinstance(records[0][0], ProjectRecord)
        for record, permissions in records:
            assert record.external_record_group_id == "team-2"
            assert permissions == []
            assert record.shared_with_me_record_group_ids == []
            assert record.shared_groups_complete is True

    @pytest.mark.asyncio
    async def test_files_are_filed_again_on_every_read(self) -> None:
        """So they follow the project's current teams. A rebuilt file keeps its version,
        so nothing is re-indexed."""
        project = _make_project_data(content=f"[spec.pdf]({self.UPLOAD})")
        project["teams"] = _teams(("team-1", True))
        connector = self._connector(project)
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))

        await connector._project_to_records("proj-1", {"team-1"})

        assert connector._extract_files_from_markdown.await_args.kwargs["is_full_sync"] is True

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self) -> None:
        project = _make_project_data(content="")
        project["teams"] = _teams(("team-1", False))
        connector = self._connector(project)
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        records = await connector._project_to_records("proj-1", {"team-1"})

        assert len(records) == 1
        assert records[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_existing_project_drops_children_it_no_longer_has(self) -> None:
        project = _make_project_data(
            content=f"[spec.pdf]({self.UPLOAD})",
            external_links=_page([{"id": "link-1", "url": "https://example.com/spec", "label": "Spec"}]),
            documents=_page([]),
        )
        project["teams"] = _teams(("team-1", False))
        existing = MagicMock()
        existing.id = "project-node"
        existing.version = 1
        existing.external_record_group_id = "team-1"
        existing.source_updated_at = 0
        existing.created_at = 1
        existing.external_revision_id = "old"
        connector = self._connector(project, existing=existing)

        def child(record_type, external_id, name) -> MagicMock:
            record = MagicMock(name=name)
            record.record_type = record_type
            record.external_record_id = external_id
            return record

        kept_link = child(RecordType.LINK, "link-1", "kept_link")
        stale_link = child(RecordType.LINK, "link-old", "stale_link")
        stale_document = child(RecordType.WEBPAGE, "doc-old", "stale_document")
        kept_file = child(RecordType.FILE, file_record_id(self.UPLOAD, "proj-1"), "kept_file")
        unlinked_file = child(
            RecordType.FILE, file_record_id("https://uploads.linear.app/org/old.pdf", "proj-1"), "unlinked_file")
        legacy_file = child(RecordType.FILE, self.UPLOAD, "legacy_file")
        connector.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[
            kept_link, stale_link, stale_document, kept_file, unlinked_file, legacy_file,
        ])

        records = await connector._project_to_records("proj-1", {"team-1"})

        assert records[0][0].id == "project-node"
        removed = [c.args[0] for c in connector._remover.remove_record.await_args_list]
        assert removed == [stale_link, stale_document, unlinked_file, legacy_file]


# ===================================================================
# _convert_images_to_base64_in_markdown
# ===================================================================


class TestConvertImagesToBase64:
    @pytest.mark.asyncio
    async def test_empty_string(self):
        connector = _make_connector()
        result = await connector._convert_images_to_base64_in_markdown("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_none_input(self):
        connector = _make_connector()
        result = await connector._convert_images_to_base64_in_markdown(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_images(self):
        connector = _make_connector()
        text = "Hello world, no images here"
        result = await connector._convert_images_to_base64_in_markdown(text)
        assert result == text

    @pytest.mark.asyncio
    async def test_non_linear_images_skipped(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![img](https://example.com/image.png)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert result == text

    @pytest.mark.asyncio
    async def test_linear_image_converted(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"fake-image-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![alt](https://uploads.linear.app/image.png)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/png;base64," in result
            encoded = base64.b64encode(b"fake-image-bytes").decode("utf-8")
            assert encoded in result

    @pytest.mark.asyncio
    async def test_jpeg_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"jpg-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![photo](https://uploads.linear.app/photo.jpeg)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/jpeg;base64," in result

    @pytest.mark.asyncio
    async def test_gif_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"gif-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![anim](https://uploads.linear.app/anim.gif)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/gif;base64," in result

    @pytest.mark.asyncio
    async def test_svg_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"<svg></svg>"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![logo](https://uploads.linear.app/logo.svg)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/svg+xml;base64," in result

    @pytest.mark.asyncio
    async def test_webp_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"webp-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![img](https://uploads.linear.app/photo.webp)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/webp;base64," in result

    @pytest.mark.asyncio
    async def test_empty_image_content_skipped(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            return
            yield

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            original = "![alt](https://uploads.linear.app/empty.png)"
            result = await connector._convert_images_to_base64_in_markdown(original)
            assert result == original

    @pytest.mark.asyncio
    async def test_download_error_skipped(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def failing_download(url):
            raise Exception("Download failed")
            yield

        mock_ds = MagicMock()
        mock_ds.download_file = failing_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            original = "![alt](https://uploads.linear.app/fail.png)"
            result = await connector._convert_images_to_base64_in_markdown(original)
            assert result == original

    @pytest.mark.asyncio
    async def test_multiple_images(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = (
                "![a](https://uploads.linear.app/a.png)\n"
                "![b](https://uploads.linear.app/b.jpg)"
            )
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert result.count("data:image/") == 2


# ===================================================================
# stream_record
# ===================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_ticket(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.id = "rec-1"
        record.external_record_group_id = "team-1"

        with patch.object(
            connector,
            "_process_issue_blockgroups_for_streaming",
            new_callable=AsyncMock,
        ) as mock_process:
            mock_process.return_value = b'{"block_groups": []}'
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_project(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.id = "rec-1"
        record.external_record_group_id = "team-1"

        with patch.object(
            connector,
            "_process_project_blockgroups_for_streaming",
            new_callable=AsyncMock,
        ) as mock_process:
            mock_process.return_value = b'{"block_groups": []}'
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_link(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = "https://example.com/doc"
        record.record_name = "My Link"

        response = await connector.stream_record(record)
        assert response.media_type == MimeTypes.MARKDOWN.value

    @pytest.mark.asyncio
    async def test_stream_link_missing_weburl(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = None

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_stream_webpage(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"

        with patch.object(
            connector, "_fetch_document_content", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = "# Document Content"
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.MARKDOWN.value

    @pytest.mark.asyncio
    async def test_stream_file(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        record.id = "rec-1"
        record.record_name = "file"
        record.mime_type = MimeTypes.PDF.value
        record.weburl = "https://uploads.linear.app/file.pdf"
        record.extension = "pdf"

        async def fake_download(url):
            yield b"file-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.PDF.value

    @pytest.mark.asyncio
    async def test_stream_file_missing_external_record_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = None
        record.id = "rec-1"

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        record.external_record_id = "unknown-1"

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_inits_datasource_if_none(self):
        connector = _make_connector()
        connector.data_source = None

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = "https://example.com"
        record.record_name = "Link"

        with patch.object(connector, "init", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            response = await connector.stream_record(record)
            mock_init.assert_called_once()


# ===================================================================
# Block processing - _create_blockgroup & _organize_comments_by_thread
# ===================================================================


class TestBlockProcessing:
    def test_create_blockgroup_basic(self):
        connector = _make_connector()
        bg = connector._create_blockgroup(
            name="Test",
            weburl="https://example.com",
            data="# Hello",
            index=0,
        )
        assert bg.name == "Test"
        assert str(bg.weburl) == "https://example.com/"
        assert bg.data == "# Hello"
        assert bg.requires_processing is True

    def test_create_blockgroup_missing_weburl(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="weburl is required"):
            connector._create_blockgroup(name="Test", weburl="", data="content")

    def test_create_blockgroup_missing_data(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="data is required"):
            connector._create_blockgroup(
                name="Test", weburl="https://example.com", data=""
            )

    def test_create_blockgroup_with_comments(self):
        connector = _make_connector()
        comments = [[
            BlockComment(
                text="Hello",
                format=DataFormat.MARKDOWN,
                thread_id="t1",
            )
        ]]
        bg = connector._create_blockgroup(
            name="Test",
            weburl="https://example.com",
            data="content",
            index=0,
            comments=comments,
        )
        assert len(bg.comments) == 1

    def test_organize_comments_empty(self):
        connector = _make_connector()
        result = connector._organize_comments_by_thread([])
        assert result == []

    def test_organize_comments_single_thread(self):
        connector = _make_connector()
        c1 = BlockComment(
            text="A",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="B",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c2, c1])
        assert len(result) == 1
        assert result[0][0].text == "A"
        assert result[0][1].text == "B"

    def test_organize_comments_multiple_threads(self):
        connector = _make_connector()
        c1 = BlockComment(
            text="Thread1-A",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="Thread2-A",
            format=DataFormat.MARKDOWN,
            thread_id="t2",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1, c2])
        assert len(result) == 2
        assert result[0][0].text == "Thread2-A"

    def test_organize_comments_no_thread_id(self):
        connector = _make_connector()
        c1 = BlockComment(
            text="No thread",
            format=DataFormat.MARKDOWN,
            thread_id=None,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1])
        assert len(result) == 1


# ===================================================================
# _check_and_fetch_updated_* methods
# ===================================================================


class TestCheckAndFetchUpdated:
    @pytest.mark.asyncio
    async def test_check_updated_issue_not_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705312800000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"

        issue_data = _make_issue_data(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"
        record.version = 1

        issue_data = _make_issue_data(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is not None
            updated_record, perms = result
            assert isinstance(updated_record, TicketRecord)

    @pytest.mark.asyncio
    async def test_check_updated_issue_not_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-missing"
        record.id = "rec-1"

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(success=False, message="Not found")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_no_team_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = ""
        record.id = "rec-1"

        issue_data = _make_issue_data(updated_at="2024-01-16T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"
        record.version = 0

        project_data = _make_project_data(
            updated_at="2024-01-16T12:00:00.000Z",
            issues={"nodes": [{"id": "issue-x"}]},
        )

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is not None
            updated_record, _ = result
            assert isinstance(updated_record, ProjectRecord)

    @pytest.mark.asyncio
    async def test_check_updated_project_not_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705312800000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"

        project_data = _make_project_data(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_no_team_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = ""
        record.id = "rec-1"

        project_data = _make_project_data(updated_at="2024-01-16T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_document_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.id = "rec-1"
        record.version = 0

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_document_no_parent_node_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = None
        record.id = "rec-1"

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_document_with_project_parent(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.id = "rec-1"
        record.version = 0

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "project": {"id": "proj-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_record_dispatches_ticket(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.id = "rec-1"

        with patch.object(
            connector,
            "_check_and_fetch_updated_issue",
            new_callable=AsyncMock,
        ) as mock_issue:
            mock_issue.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_issue.assert_called_once_with(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_dispatches_project(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.id = "rec-1"

        with patch.object(
            connector,
            "_check_and_fetch_updated_project",
            new_callable=AsyncMock,
        ) as mock_proj:
            mock_proj.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_proj.assert_called_once_with(record)

    @pytest.mark.asyncio
    async def test_check_updated_record_file_returns_none(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_unsupported_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_link_issue_attachment(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "attach-1"
        record.parent_external_record_id = "issue-1"
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.record_type = RecordType.TICKET
        parent_record.id = "parent-rec-1"

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        with patch.object(
            connector,
            "_check_and_fetch_updated_issue_link",
            new_callable=AsyncMock,
        ) as mock_link:
            mock_link.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_updated_record_link_no_parent(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = None
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_link_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "attach-1"
        record.parent_external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"
        record.version = 0
        record.weburl = None

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"

        attach_data = {
            "id": "attach-1",
            "url": "https://example.com/attachment",
            "title": "Attachment",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.attachment = AsyncMock(
            return_value=_mock_response(data={"attachment": attach_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue_link(
                record, parent_record
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_issue_link_no_external_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = ""
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_issue_link(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = "proj-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"
        record.version = 0
        record.weburl = None

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"
        parent_record.external_record_id = "proj-1"

        project_data = _make_project_data(
            external_links={
                "nodes": [
                    {
                        "id": "link-1",
                        "url": "https://example.com",
                        "label": "Link",
                        "createdAt": "2024-01-10T10:00:00.000Z",
                        "updatedAt": "2024-01-16T12:00:00.000Z",
                    }
                ]
            }
        )

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project_link(
                record, parent_record
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_not_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-missing"
        record.parent_external_record_id = "proj-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"
        parent_record.external_record_id = "proj-1"

        project_data = _make_project_data(external_links={"nodes": []})

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project_link(
                record, parent_record
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_no_project_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = ""
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.external_record_id = ""

        result = await connector._check_and_fetch_updated_project_link(
            record, parent_record
        )
        assert result is None


# ===================================================================
# reindex_records
# ===================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_reindex_empty_list(self):
        connector = _make_connector()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_reindex_with_updated_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"

        updated_ticket = MagicMock(spec=TicketRecord)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = (updated_ticket, [])
                await connector.reindex_records([ticket])
                connector.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_with_non_updated_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([ticket])
                connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_skips_base_record_class(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=Record)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.id = "rec-1"
        type(record).__name__ = "Record"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([record])
                connector.data_entities_processor.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_reindex_check_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record1 = MagicMock(spec=TicketRecord)
        record1.record_type = RecordType.TICKET
        record1.external_record_id = "issue-1"
        record1.id = "rec-1"
        type(record1).__name__ = "TicketRecord"

        record2 = MagicMock(spec=TicketRecord)
        record2.record_type = RecordType.TICKET
        record2.external_record_id = "issue-2"
        record2.id = "rec-2"
        type(record2).__name__ = "TicketRecord"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.side_effect = [Exception("fail"), None]
                await connector.reindex_records([record1, record2])

    @pytest.mark.asyncio
    async def test_reindex_not_implemented_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("not impl")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([ticket])

    @pytest.mark.asyncio
    async def test_reindex_inits_datasource_if_none(self):
        connector = _make_connector()
        connector.data_source = None

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        with patch.object(connector, "init", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            connector.data_source = MagicMock()
            with patch.object(
                connector, "_get_fresh_datasource", new_callable=AsyncMock
            ):
                with patch.object(
                    connector,
                    "_check_and_fetch_updated_record",
                    new_callable=AsyncMock,
                ) as mock_check:
                    mock_check.return_value = None
                    await connector.reindex_records([ticket])


# ===================================================================
# _sync_deleted_issues & _sync_deleted_projects
# ===================================================================


class TestSyncDeleted:
    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        connector = _make_connector()
        await connector._sync_deleted_issues([])
        assert connector.deletion_sync_point.store == {}

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_initial_sync(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.trashed_issues = AsyncMock(return_value=_mock_paginated("issues", []))

        rg, perms = _make_team_record_group()
        with patch(
            "app.connectors.sources.linear.connector.get_epoch_timestamp_in_ms",
            return_value=1705000000000,
        ), patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            await connector._sync_deleted_issues([(rg, perms)])

        # The whole trash is listed on the first run, then the checkpoint is set to
        # the moment the run started.
        assert mock_ds.trashed_issues.await_args.kwargs["filter"]["archivedAt"] == {"null": False}
        assert connector.deletion_sync_point.store["trashed_issues"]["last_sync_time"] == 1705000000000

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_with_trashed(self):
        connector = _make_connector()
        connector.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": 1705000000000}

        trashed_issue = _make_issue_data(
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )
        second_page_issue = _make_issue_data(
            issue_id="issue-2",
            identifier="ENG-2",
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.trashed_issues = AsyncMock(side_effect=[
            _mock_paginated("issues", [trashed_issue], has_next=True, end_cursor="cursor-1"),
            _mock_paginated("issues", [second_page_issue]),
        ])
        # issue-1 is stored, issue-2 was never synced.
        connector._remover.remove = AsyncMock(side_effect=[True, False])

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_deleted_issues([(rg, perms)])

        assert [c.args[0] for c in connector._remover.remove.await_args_list] == ["issue-1", "issue-2"]
        assert [c.kwargs["after"] for c in mock_ds.trashed_issues.await_args_list] == [None, "cursor-1"]

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_fetch_failure(self) -> None:
        connector = _make_connector()
        connector.notify = AsyncMock()
        connector.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": 1705000000000}

        trashed_issue = _make_issue_data(
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )
        mock_ds = MagicMock()
        # The first page arrives, the second never does.
        failure = GraphQLResponse(success=False, message="Internal error", status_code=500)
        mock_ds.trashed_issues = AsyncMock(side_effect=[
            _mock_paginated("issues", [trashed_issue], has_next=True, end_cursor="cursor-1"),
            failure, failure, failure, failure,
        ])
        connector._remover.remove = AsyncMock(return_value=True)

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_deleted_issues([(rg, perms)])

        # A failed page is not the end of the list: the checkpoint stays where it
        # was and the cursor of the last stored page is kept for the next run.
        saved = connector.deletion_sync_point.store["trashed_issues"]
        assert saved["last_sync_time"] == 1705000000000
        assert saved["resume_cursor"] == "cursor-1"
        connector._remover.remove.assert_awaited_once_with("issue-1")
        connector.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_resumes_from_saved_cursor(self) -> None:
        connector = _make_connector()
        connector.deletion_sync_point.store["trashed_issues"] = {
            "last_sync_time": 1705000000000,
            "resume_cursor": "cursor-1",
            "resume_since": 1704999940000,
            "resume_started_at": 1705000300000,
        }
        mock_ds = MagicMock()
        mock_ds.trashed_issues = AsyncMock(return_value=_mock_paginated("issues", []))

        rg, perms = _make_team_record_group()
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            await connector._sync_deleted_issues([(rg, perms)])

        assert mock_ds.trashed_issues.await_args.kwargs["after"] == "cursor-1"
        # The interrupted run is the one that finishes: the checkpoint is its start.
        saved = connector.deletion_sync_point.store["trashed_issues"]
        assert saved["last_sync_time"] == 1705000300000
        assert saved["resume_cursor"] == ""

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_empty_teams(self) -> None:
        connector = _make_connector()
        connector._reconciler.run_projects = AsyncMock()
        await connector._sync_deleted_projects([])
        connector._reconciler.run_projects.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_removes_what_the_listing_no_longer_has(self) -> None:
        connector = _make_connector()
        rg, perms = _make_team_record_group()

        kept = MagicMock(id="rec-kept", external_record_id="proj-1",
                         external_record_group_id="team-1", is_placeholder=False, is_deleted=False)
        gone = MagicMock(id="rec-gone", external_record_id="proj-gone",
                         external_record_group_id="team-1", is_placeholder=False, is_deleted=False)
        trashed = MagicMock(id="rec-trashed", external_record_id="proj-trashed",
                            external_record_group_id="team-1", is_placeholder=False, is_deleted=False)
        other_team = MagicMock(id="rec-other", external_record_id="proj-other",
                               external_record_group_id="team-9", is_placeholder=False, is_deleted=False)
        processor = connector.data_entities_processor
        processor.get_records_by_record_type = AsyncMock(return_value=[kept, gone, trashed, other_team])

        mock_ds = MagicMock()
        mock_ds.execute_operation = AsyncMock(return_value=_mock_paginated("projects", [
            {"id": "proj-1", "trashed": False},
            {"id": "proj-trashed", "trashed": True},
        ]))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            await connector._sync_deleted_projects([(rg, perms)])

        mock_ds.execute_operation.assert_awaited_once_with("projectIds", {
            "filter": {"accessibleTeams": {"some": {"id": {"in": ["team-1"]}}}},
            "includeArchived": True,
            "first": 25,
        })
        removed = [c.args[0] for c in processor.on_records_deleted_cascade.await_args_list]
        assert removed == [["rec-gone"], ["rec-trashed"]]

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_listing_failure_removes_nothing(self) -> None:
        connector = _make_connector()
        rg, perms = _make_team_record_group()
        stored = MagicMock(id="rec-1", external_record_id="proj-1",
                           external_record_group_id="team-1", is_placeholder=False, is_deleted=False)
        processor = connector.data_entities_processor
        processor.get_records_by_record_type = AsyncMock(return_value=[stored])

        mock_ds = MagicMock()
        mock_ds.execute_operation = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            await connector._sync_deleted_projects([(rg, perms)])

        assert mock_ds.execute_operation.await_count == 4
        processor.on_records_deleted_cascade.assert_not_awaited()


# ===================================================================
# _mark_record_and_children_deleted
# ===================================================================


class TestMarkRecordDeleted:
    @pytest.mark.asyncio
    async def test_mark_deleted_not_found(self):
        connector = _make_connector()
        processor = connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._mark_record_and_children_deleted("ext-1", "issue")
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mark_deleted_with_children(self):
        connector = _make_connector()
        parent = MagicMock()
        parent.id = "parent-id"
        parent.external_record_id = "ext-parent"
        parent.record_type = RecordType.TICKET

        child = MagicMock()
        child.id = "child-id"
        child.external_record_id = "ext-child"
        child.record_type = RecordType.WEBPAGE

        sub_issue = MagicMock()
        sub_issue.id = "sub-issue-id"
        sub_issue.external_record_id = "ext-sub-issue"
        sub_issue.record_type = RecordType.TICKET

        processor = connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(return_value=parent)
        processor.get_records_by_parent = AsyncMock(
            side_effect=[[child, sub_issue], []]
        )

        await connector._mark_record_and_children_deleted("ext-parent", "issue")

        # One delete for the document and the issue; the sub-issue stays and loses
        # its parent link.
        processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["child-id", "parent-id"], "linear-conn-1", cascade_children=False, include_trashed_roots=True
        )
        processor.on_records_detached_from_parent.assert_awaited_once_with(["sub-issue-id"])


# ===================================================================
# _apply_date_filters_to_linear_filter
# ===================================================================


class TestApplyDateFilters:
    def test_no_filters(self):
        connector = _make_connector()
        connector.sync_filters = None
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert linear_filter == {}

    def test_modified_after_from_checkpoint(self):
        connector = _make_connector()
        connector.sync_filters = None
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, 1705312800000)
        assert "updatedAt" in linear_filter
        assert "gt" in linear_filter["updatedAt"]

    def test_modified_after_from_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "updatedAt" in linear_filter

    def test_modified_after_merge_with_checkpoint(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, 1705500000000)
        assert "updatedAt" in linear_filter

    def test_modified_before(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "updatedAt" in linear_filter
        assert "lte" in linear_filter["updatedAt"]

    def test_modified_before_with_after(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "gt" in linear_filter["updatedAt"]
        assert "lte" in linear_filter["updatedAt"]

    def test_created_after(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "createdAt" in linear_filter
        assert "gte" in linear_filter["createdAt"]

    def test_created_before(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "createdAt" in linear_filter
        assert "lte" in linear_filter["createdAt"]

    def test_created_before_with_after(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "gte" in linear_filter["createdAt"]
        assert "lte" in linear_filter["createdAt"]


# ===================================================================
# _linear_datetime_from_timestamp
# ===================================================================


class TestLinearDatetimeConversion:
    def test_valid_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(1705312800000)
        assert "2024-01-15" in result
        assert result.endswith("Z")

    def test_invalid_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(-99999999999999999)
        assert result == ""


# ===================================================================
# _parse_linear_datetime_to_datetime
# ===================================================================


class TestParseLinearDatetimeToDatetime:
    def test_valid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("2024-01-15T10:30:00.000Z")
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_empty_string(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("")
        assert result is None

    def test_none(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime(None)
        assert result is None

    def test_invalid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("not-a-date")
        assert result is None


# ===================================================================
# _extract_file_urls_from_markdown
# ===================================================================


class TestExtractFileUrls:
    def test_empty_input(self):
        connector = _make_connector()
        assert connector._extract_file_urls_from_markdown("") == []
        assert connector._extract_file_urls_from_markdown(None) == []

    def test_no_linear_urls(self):
        connector = _make_connector()
        text = "[doc](https://example.com/file.pdf)"
        assert connector._extract_file_urls_from_markdown(text) == []

    def test_image_urls(self):
        connector = _make_connector()
        text = "![alt](https://uploads.linear.app/image.png)"
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1
        assert result[0]["url"] == "https://uploads.linear.app/image.png"

    def test_exclude_images(self):
        connector = _make_connector()
        text = "![alt](https://uploads.linear.app/image.png)"
        result = connector._extract_file_urls_from_markdown(text, exclude_images=True)
        assert len(result) == 0

    def test_file_links(self):
        connector = _make_connector()
        text = "[report.pdf](https://uploads.linear.app/report.pdf)"
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"

    def test_mixed_images_and_links(self):
        connector = _make_connector()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "[doc](https://uploads.linear.app/doc.pdf)"
        )
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 2

    def test_exclude_images_keeps_links(self):
        connector = _make_connector()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "[doc](https://uploads.linear.app/doc.pdf)"
        )
        result = connector._extract_file_urls_from_markdown(text, exclude_images=True)
        assert len(result) == 1
        assert result[0]["filename"] == "doc"

    def test_deduplication(self):
        connector = _make_connector()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "![img2](https://uploads.linear.app/img.png)"
        )
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1


# ===================================================================
# _get_mime_type_from_url
# ===================================================================


class TestGetMimeType:
    def test_pdf(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "report.pdf") == MimeTypes.PDF.value

    def test_png(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "img.png") == MimeTypes.PNG.value

    def test_docx(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "file.docx") == MimeTypes.DOCX.value

    def test_from_url(self):
        connector = _make_connector()
        assert (
            connector._get_mime_type_from_url("https://example.com/file.xlsx?token=abc", "")
            == MimeTypes.XLSX.value
        )

    def test_unknown(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("https://example.com/file", "") == MimeTypes.UNKNOWN.value

    def test_csv(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "data.csv") == MimeTypes.CSV.value


# ===================================================================
# Transformations
# ===================================================================


class TestTransformIssueToTicketRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        issue_data = _make_issue_data()
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.record_name == "[ENG-1] Test Issue"
        assert ticket.external_record_id == "issue-1"
        assert ticket.record_type == RecordType.TICKET
        assert ticket.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_issue_to_ticket_record({"id": ""}, "team-1")

    def test_missing_team_id_raises(self):
        connector = _make_connector()
        issue_data = _make_issue_data()
        with pytest.raises(ValueError, match="team_id is required"):
            connector._transform_issue_to_ticket_record(issue_data, "")

    def test_priority_mapping(self):
        connector = _make_connector()
        for priority_num, expected_str in [(0, "none"), (1, "Urgent"), (2, "High"), (3, "Medium"), (4, "Low")]:
            issue_data = _make_issue_data(priority=priority_num)
            ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
            assert ticket is not None

    def test_no_priority(self):
        connector = _make_connector()
        issue_data = _make_issue_data(priority=None)
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket is not None

    def test_sub_issue_type(self):
        connector = _make_connector()
        issue_data = _make_issue_data(parent={"id": "parent-issue"})
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.type == ItemType.SUB_ISSUE

    def test_version_increment(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.source_updated_at = 1705000000000

        issue_data = _make_issue_data(updated_at="2024-01-16T12:00:00.000Z")
        ticket = connector._transform_issue_to_ticket_record(
            issue_data, "team-1", existing
        )
        assert ticket.version == 2
        assert ticket.id == "existing-id"

    def test_no_version_change(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1705320000000

        issue_data = _make_issue_data(updated_at="2024-01-15T12:00:00.000Z")
        ticket = connector._transform_issue_to_ticket_record(
            issue_data, "team-1", existing
        )
        assert ticket.version == 3

    def test_with_relations(self):
        connector = _make_connector()
        issue_data = _make_issue_data(
            relations={
                "nodes": [
                    {
                        "type": "blocks",
                        "relatedIssue": {"id": "related-1"},
                    }
                ]
            }
        )
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert len(ticket.related_external_records) >= 0

    def test_missing_identifier_and_title(self):
        connector = _make_connector()
        issue_data = _make_issue_data(identifier="", title="")
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.record_name == "issue-1"

    def test_assignee_and_creator(self):
        connector = _make_connector()
        issue_data = _make_issue_data(
            assignee={"email": "dev@test.com", "displayName": "Dev"},
            creator={"email": "pm@test.com", "name": "PM"},
        )
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.assignee_email == "dev@test.com"
        assert ticket.creator_email == "pm@test.com"


class TestTransformToProjectRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        project_data = _make_project_data()
        record = connector._transform_to_project_record(project_data, "team-1")
        assert record.record_name == "Test Project"
        assert record.record_type == RecordType.PROJECT
        assert record.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_to_project_record({"id": ""}, "team-1")

    def test_missing_name_uses_slug(self):
        connector = _make_connector()
        data = _make_project_data(name="", slug_id="my-slug")
        record = connector._transform_to_project_record(data, "team-1")
        assert record.record_name == "my-slug"

    def test_missing_name_and_slug_uses_id(self):
        connector = _make_connector()
        data = _make_project_data(name="", slug_id="")
        record = connector._transform_to_project_record(data, "team-1")
        assert record.record_name == "proj-1"

    def test_with_lead(self):
        connector = _make_connector()
        data = _make_project_data(
            lead={"id": "lead-1", "displayName": "Lead Dev", "email": "lead@test.com"}
        )
        record = connector._transform_to_project_record(data, "team-1")
        assert record.lead_name == "Lead Dev"
        assert record.lead_email == "lead@test.com"


class TestTransformAttachmentToLinkRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        attachment_data = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "My File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }
        link = connector._transform_attachment_to_link_record(
            attachment_data, "issue-1", "node-1", "team-1"
        )
        assert link.record_name == "My File"
        assert link.weburl == "https://example.com/file"
        assert link.is_public == LinkPublicStatus.UNKNOWN

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_attachment_to_link_record(
                {"id": "", "url": "x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_attachment_to_link_record(
                {"id": "a", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_uses_label_as_title(self):
        connector = _make_connector()
        data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External Link",
            "createdAt": "",
            "updatedAt": "",
        }
        link = connector._transform_attachment_to_link_record(
            data, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert link.record_name == "External Link"
        assert link.parent_record_type == RecordType.PROJECT


class TestTransformDocumentToWebpageRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }
        webpage = connector._transform_document_to_webpage_record(
            doc_data, "issue-1", "node-1", "team-1"
        )
        assert webpage.record_name == "Design Doc"
        assert webpage.record_type == RecordType.WEBPAGE

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_document_to_webpage_record(
                {"id": "", "url": "x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_document_to_webpage_record(
                {"id": "d", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_no_title_uses_id(self):
        connector = _make_connector()
        doc_data = {
            "id": "doc-abcdef12",
            "url": "https://linear.app/doc/1",
            "title": "",
            "createdAt": "",
            "updatedAt": "",
        }
        webpage = connector._transform_document_to_webpage_record(
            doc_data, "issue-1", "node-1", "team-1"
        )
        assert "doc-abcd" in webpage.record_name


# ===================================================================
# _transform_file_url_to_file_record
# ===================================================================


class TestTransformFileUrlToFileRecord:
    @pytest.mark.asyncio
    async def test_basic_transform(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=1024)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            file_record = await connector._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/report.pdf",
                filename="report.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
            assert file_record.record_name == "report.pdf"
            assert file_record.record_type == RecordType.FILE
            assert file_record.extension == "pdf"
            assert file_record.size_in_bytes == 1024

    @pytest.mark.asyncio
    async def test_file_size_error_fallback(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(side_effect=Exception("network error"))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            file_record = await connector._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/file.txt",
                filename="file.txt",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
            assert file_record.size_in_bytes == 0


# ===================================================================
# _sync_issues_for_teams
# ===================================================================


class TestSyncIssuesForTeams:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_issues_for_teams([])

    @pytest.mark.asyncio
    async def test_team_missing_external_group_id(self):
        connector = _make_connector()
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            external_group_id=None,
            connector_id="linear-conn-1",
            connector_name=Connectors.LINEAR,
            name="NoID",
            short_name="NO",
            group_type=RecordGroupType.PROJECT,
        )
        perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        await connector._sync_issues_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_issues_processes_batches(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._now = MagicMock(return_value=1705400000000)

        rg, perms = _make_team_record_group()

        ticket = MagicMock(spec=TicketRecord)
        ticket.source_updated_at = 1705312800000
        batch = [(ticket, [])]

        async def fake_batch_gen(**kwargs):
            yield batch

        with patch.object(
            connector,
            "_fetch_issues_for_team_batch",
            side_effect=lambda **kw: fake_batch_gen(**kw),
        ) as mock_fetch:
            result = await connector._sync_issues_for_teams([(rg, perms)])

        connector.data_entities_processor.on_new_records.assert_awaited_once_with(batch)
        assert result == ({"team-1"}, [])
        # The listing is given the run and the checkpoint so it can save its cursor.
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["team_id"] == "team-1"
        assert kwargs["team_key"] == "ENG"
        assert kwargs["last_sync_time"] is None
        assert kwargs["run"].started_at == 1705400000000
        assert kwargs["failures"] == []
        assert connector.issues_sync_point.store["team_issues_team-1"]["last_sync_time"] == 1705400000000

    @pytest.mark.asyncio
    async def test_sync_issues_incremental_reads_since_last_run_with_overlap(self) -> None:
        connector = _make_connector()
        connector._now = MagicMock(return_value=1705400000000)
        connector.issues_sync_point.store["team_issues_team-1"] = {"last_sync_time": 1705300000000}
        rg, perms = _make_team_record_group()

        async def fake_batch_gen(**kwargs) -> AsyncIterator[list]:
            if False:
                yield []

        with patch.object(
            connector,
            "_fetch_issues_for_team_batch",
            side_effect=lambda **kw: fake_batch_gen(**kw),
        ) as mock_fetch:
            result = await connector._sync_issues_for_teams([(rg, perms)])

        assert mock_fetch.call_args.kwargs["last_sync_time"] == 1705300000000 - 60_000
        assert result == (set(), [])
        assert connector.issues_sync_point.store["team_issues_team-1"]["last_sync_time"] == 1705400000000

    @pytest.mark.asyncio
    async def test_sync_issues_resumes_an_interrupted_run(self) -> None:
        connector = _make_connector()
        connector.issues_sync_point.store["team_issues_team-1"] = {
            "last_sync_time": 0,
            "resume_cursor": "cursor-7",
            "resume_since": 0,
            "resume_started_at": 1705350000000,
        }
        rg, perms = _make_team_record_group()

        async def fake_batch_gen(**kwargs) -> AsyncIterator[list]:
            if False:
                yield []

        with patch.object(
            connector,
            "_fetch_issues_for_team_batch",
            side_effect=lambda **kw: fake_batch_gen(**kw),
        ) as mock_fetch:
            await connector._sync_issues_for_teams([(rg, perms)])

        run = mock_fetch.call_args.kwargs["run"]
        assert run.cursor == "cursor-7"
        assert run.resumed is True
        assert run.is_full is True
        # Finishing a resumed run moves the checkpoint to when that run began.
        saved = connector.issues_sync_point.store["team_issues_team-1"]
        assert saved["last_sync_time"] == 1705350000000
        assert saved["resume_cursor"] == ""

    @pytest.mark.asyncio
    async def test_sync_issues_holds_checkpoint_when_issues_could_not_be_stored(self) -> None:
        connector = _make_connector()
        connector._now = MagicMock(return_value=1705400000000)
        connector.issues_sync_point.store["team_issues_team-1"] = {"last_sync_time": 1705300000000}
        rg, perms = _make_team_record_group()

        async def fake_batch_gen(**kwargs) -> AsyncIterator[list]:
            kwargs["failures"].append("ENG-7")
            if False:
                yield []

        with patch.object(
            connector,
            "_fetch_issues_for_team_batch",
            side_effect=lambda **kw: fake_batch_gen(**kw),
        ):
            outcomes = [
                await connector._sync_issues_for_teams([(rg, perms)]) for _ in range(4)
            ]

        # Three syncs in a row keep the checkpoint and report the team; the fourth
        # gives up on the issue and moves on.
        assert [failed for _, failed in outcomes] == [["Engineering"]] * 3 + [[]]
        saved = connector.issues_sync_point.store["team_issues_team-1"]
        assert saved["last_sync_time"] == 1705400000000
        assert saved["held_runs"] == 0

    @pytest.mark.asyncio
    async def test_sync_issues_team_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._now = MagicMock(return_value=1705400000000)
        connector.issues_sync_point.store["team_issues_team-1"] = {"last_sync_time": 1705300000000}
        rg, perms = _make_team_record_group()
        rg2, perms2 = _make_team_record_group(team_id="team-2", name="Design", key="DES")

        ticket = MagicMock(spec=TicketRecord)

        async def fake_batch_gen(**kwargs) -> AsyncIterator[list]:
            if kwargs["team_id"] == "team-1":
                raise LinearTransientError("page 2 could not be read", 502)
            yield [(ticket, [])]

        with patch.object(
            connector,
            "_fetch_issues_for_team_batch",
            side_effect=lambda **kw: fake_batch_gen(**kw),
        ):
            full, failed = await connector._sync_issues_for_teams([(rg, perms), (rg2, perms2)])

        # A listing that could not be read to its end leaves the team's checkpoint
        # untouched; the next team is still synced.
        assert failed == ["Engineering"]
        assert full == {"team-2"}
        assert connector.issues_sync_point.store["team_issues_team-1"] == {"last_sync_time": 1705300000000}
        assert connector.issues_sync_point.store["team_issues_team-2"]["last_sync_time"] == 1705400000000
        connector.data_entities_processor.on_new_records.assert_awaited_once()


# ===================================================================
# _sync_projects_for_teams
# ===================================================================


class TestSyncProjectsForTeams:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_projects_for_teams([])

    @pytest.mark.asyncio
    async def test_team_missing_external_group_id(self):
        connector = _make_connector()
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            external_group_id=None,
            connector_id="linear-conn-1",
            connector_name=Connectors.LINEAR,
            name="NoID",
            short_name="NO",
            group_type=RecordGroupType.PROJECT,
        )
        perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        await connector._sync_projects_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_projects_processes_batches(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._now = MagicMock(return_value=1705400000000)

        rg, perms = _make_team_record_group()
        rg2, perms2 = _make_team_record_group(team_id="team-0", name="Design", key="DES")

        project = MagicMock(spec=ProjectRecord)
        project.source_updated_at = 1705312800000
        batch = [(project, [])]

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(side_effect=[
            _mock_paginated("projects", [{"id": "proj-1"}, {"id": "proj-hidden"}],
                            has_next=True, end_cursor="cursor-1"),
            _mock_paginated("projects", [{"id": "proj-2"}]),
        ])

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ), patch.object(
            connector, "_project_to_records", new_callable=AsyncMock,
            side_effect=[batch, [], batch],
        ) as mock_to_records:
            await connector._sync_projects_for_teams([(rg, perms), (rg2, perms2)])

        # Projects are listed once for all synced teams, ten at a time.
        scope_filter = {"accessibleTeams": {"some": {"id": {"in": ["team-0", "team-1"]}}}}
        assert [c.kwargs for c in mock_ds.projects.await_args_list] == [
            {"first": 10, "after": None, "filter": scope_filter},
            {"first": 10, "after": "cursor-1", "filter": scope_filter},
        ]
        # Each listed project is read in full; one that yields nothing is not stored.
        assert [c.args for c in mock_to_records.await_args_list] == [
            ("proj-1", {"team-0", "team-1"}),
            ("proj-hidden", {"team-0", "team-1"}),
            ("proj-2", {"team-0", "team-1"}),
        ]
        assert connector.data_entities_processor.on_new_records.await_count == 2
        assert connector.projects_sync_point.store[PROJECTS_CHECKPOINT_KEY]["last_sync_time"] == 1705400000000

    @pytest.mark.asyncio
    async def test_sync_projects_incremental_filters_by_update_time(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector._now = MagicMock(return_value=1705400000000)
        connector.projects_sync_point.store[PROJECTS_CHECKPOINT_KEY] = {"last_sync_time": 1705300000000}
        rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=_mock_paginated("projects", [{"id": "proj-1"}]))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ), patch.object(
            connector, "_project_to_records", new_callable=AsyncMock, return_value=[]
        ) as mock_to_records:
            await connector._sync_projects_for_teams([(rg, perms)])

        since = connector._linear_datetime_from_timestamp(1705300000000 - 60_000)
        assert mock_ds.projects.await_args.kwargs["filter"] == {
            "accessibleTeams": {"some": {"id": {"in": ["team-1"]}}},
            "updatedAt": {"gt": since},
        }
        mock_to_records.assert_awaited_once_with("proj-1", {"team-1"})

    @pytest.mark.asyncio
    async def test_a_checkpoint_under_the_old_key_reads_every_project_again(self) -> None:
        """An unchanged project would otherwise keep the team edges from when it was
        shared into its non-lead teams."""
        connector = _make_connector()
        connector.sync_filters = None
        connector._now = MagicMock(return_value=1705400000000)
        connector.projects_sync_point.store["projects"] = {"last_sync_time": 1705300000000}
        rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=_mock_paginated("projects", [{"id": "proj-1"}]))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ), patch.object(
            connector, "_project_to_records", new_callable=AsyncMock, return_value=[]
        ):
            await connector._sync_projects_for_teams([(rg, perms)])

        assert mock_ds.projects.await_args.kwargs["filter"] == {
            "accessibleTeams": {"some": {"id": {"in": ["team-1"]}}},
        }
        assert connector.projects_sync_point.store[PROJECTS_CHECKPOINT_KEY]["last_sync_time"] == 1705400000000

    @pytest.mark.asyncio
    async def test_sync_projects_failed_page_keeps_checkpoint_and_notifies(self) -> None:
        connector = _make_connector()
        connector.notify = AsyncMock()
        connector._now = MagicMock(return_value=1705400000000)
        connector.projects_sync_point.store[PROJECTS_CHECKPOINT_KEY] = {"last_sync_time": 1705300000000}
        rg, perms = _make_team_record_group()

        failure = GraphQLResponse(success=False, message="Internal error", status_code=500)
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(side_effect=[
            _mock_paginated("projects", [{"id": "proj-1"}], has_next=True, end_cursor="cursor-1"),
            failure, failure, failure, failure,
        ])

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ), patch.object(
            connector, "_project_to_records", new_callable=AsyncMock, return_value=[]
        ):
            await connector._sync_projects_for_teams([(rg, perms)])

        saved = connector.projects_sync_point.store[PROJECTS_CHECKPOINT_KEY]
        assert saved["last_sync_time"] == 1705300000000
        assert saved["resume_cursor"] == "cursor-1"
        connector.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_projects_rate_limit_stops_the_sync(self) -> None:
        connector = _make_connector()
        connector.notify = AsyncMock()
        rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=GraphQLResponse(success=False, message="Rate limited", status_code=429)
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            with pytest.raises(LinearRateLimited):
                await connector._sync_projects_for_teams([(rg, perms)])

        # Six waits, then the seventh answer is final.
        assert [c.args[0] for c in connector._sleep.await_args_list] == [5, 15, 30, 60, 120, 240]
        assert mock_ds.projects.await_count == 7
        connector.notify.assert_not_awaited()
        assert PROJECTS_CHECKPOINT_KEY not in connector.projects_sync_point.store


# ===================================================================
# _sync_attachments
# ===================================================================


class TestSyncAttachments:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_attachments([])

    @pytest.mark.asyncio
    async def test_sync_attachments_with_data(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._now = MagicMock(return_value=1705400000000)
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group()

        parent_record = MagicMock()
        parent_record.id = "parent-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )
        connector.data_entities_processor.get_record_by_weburl = AsyncMock(return_value=None)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }

        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(
            return_value=_mock_paginated("attachments", [attachment])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_attachments([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_called()

        assert connector.attachments_sync_point.store["attachments"]["last_sync_time"] == 1705400000000


# ===================================================================
# _sync_documents
# ===================================================================


class TestSyncDocuments:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_documents([])

    @pytest.mark.asyncio
    async def test_sync_documents_with_data(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._now = MagicMock(return_value=1705400000000)
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group()

        parent_record = MagicMock()
        parent_record.id = "parent-id"
        parent_record.external_record_group_id = "team-1"
        parent_record.is_placeholder = False
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: (
                parent_record if external_record_id == "issue-1" else None
            )
        )

        document = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "content": "# Content",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {
                "id": "issue-1",
                "identifier": "ENG-1",
                "team": {"id": "team-1"},
            },
        }

        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(
            return_value=_mock_paginated("documents", [document])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_documents([(rg, perms)])

        (batch,), _ = connector.data_entities_processor.on_new_records.await_args
        assert [(r.external_record_id, r.parent_external_record_id, r.external_record_group_id)
                for r, _ in batch] == [("doc-1", "issue-1", "team-1")]
        assert batch[0][0].parent_node_id == "parent-id"
        assert connector.documents_sync_point.store["documents"]["last_sync_time"] == 1705400000000

    @pytest.mark.asyncio
    async def test_sync_documents_skips_placeholder_parent(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        rg, perms = _make_team_record_group()

        stub = MagicMock()
        stub.id = "stub-id"
        stub.external_record_group_id = "team-1"
        stub.is_placeholder = True
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=stub)

        document = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_mock_paginated("documents", [document]))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            await connector._sync_documents([(rg, perms)])

        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_documents_skips_standalone(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group()

        standalone_doc = {
            "id": "doc-standalone",
            "url": "https://linear.app/doc/standalone",
            "title": "Standalone Doc",
            "content": "# Content",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(
            return_value=_mock_paginated("documents", [standalone_doc])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_documents([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_not_called()


# ===================================================================
# _fetch_document_content
# ===================================================================


class TestFetchDocumentContent:
    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        doc_data = {"content": "# Hello World"}
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_convert_images_to_base64_in_markdown",
                new_callable=AsyncMock,
            ) as mock_convert:
                mock_convert.return_value = "# Hello World"
                result = await connector._fetch_document_content("doc-1")
                assert result == "# Hello World"

    @pytest.mark.asyncio
    async def test_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=GraphQLResponse(
                success=False, message="Entity not found: Document", status_code=400
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with pytest.raises(HTTPException) as exc_info:
                await connector._fetch_document_content("doc-missing")

        # Not found is a definite answer: no retry, and the reader is told 404.
        assert exc_info.value.status_code == 404
        mock_ds.document.assert_awaited_once_with(id="doc-missing")
        connector._sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transport_failure_is_retried_then_reported_unreachable(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(success=False, message="Connection reset")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector._fetch_document_content("doc-1")

        assert exc_info.value.status_code == 502
        assert mock_ds.document.await_count == 4
        assert [c.args[0] for c in connector._sleep.await_args_list] == [1, 3, 9]

    @pytest.mark.asyncio
    async def test_empty_content(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        doc_data = {"content": ""}
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._fetch_document_content("doc-1")
            assert result == ""

    @pytest.mark.asyncio
    async def test_datasource_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_document_content("doc-1")


# ===================================================================
# Other utility methods
# ===================================================================


class TestOtherUtilities:
    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        connector = _make_connector()
        with patch.object(
            connector, "run_sync", new_callable=AsyncMock
        ) as mock_sync:
            await connector.run_incremental_sync()
            mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(
            return_value=_mock_response(data={"organization": {"id": "org-1"}})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.test_connection_and_access()
            assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(
            return_value=_mock_response(success=False, message="Unauthorized")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.test_connection_and_access()
            assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.side_effect = Exception("boom")
            result = await connector.test_connection_and_access()
            assert result is False

    @pytest.mark.asyncio
    async def test_get_signed_url(self):
        connector = _make_connector()
        record = MagicMock()
        result = await connector.get_signed_url(record)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        connector = _make_connector()
        await connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.close = AsyncMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client
        await connector.cleanup()
        mock_internal.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self):
        connector = _make_connector()
        connector.external_client = None
        await connector.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_error(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.side_effect = Exception("fail")
        connector.external_client = mock_client
        await connector.cleanup()


# ===================================================================
# _get_fresh_datasource edge cases
# ===================================================================


class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_api_token_auth_type(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "new-api-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_called_once_with("new-api-token")

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": ""},
            }
        )

        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_same_token_no_update(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "same-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "same-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_not_called()


# ===================================================================
# _get_team_options edge cases
# ===================================================================


class TestGetTeamOptions:
    @pytest.mark.asyncio
    async def test_team_options_with_search(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {"id": "t1", "name": "Engineering", "key": "ENG"},
                    {"id": "t3", "name": "Platform", "key": "PENG"},
                ],
                has_next=True,
                end_cursor="cursor-2",
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.get_filter_options(
                "team_ids", limit=2, search="eng", cursor="cursor-1"
            )

        # Linear does the matching, on name or key, so paging stays correct.
        mock_ds.teams.assert_awaited_once_with(
            first=2,
            after="cursor-1",
            filter={"or": [
                {"name": {"containsIgnoreCase": "eng"}},
                {"key": {"containsIgnoreCase": "eng"}},
            ]},
        )
        # What Linear returned is shown as is: a key match is not filtered away.
        assert [o.label for o in result.options] == ["Engineering (ENG)", "Platform (PENG)"]
        assert result.has_more is True
        assert result.cursor == "cursor-2"

    @pytest.mark.asyncio
    async def test_team_options_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with pytest.raises(RuntimeError, match="Failed to fetch team"):
                await connector.get_filter_options("team_ids")


# ===================================================================
# run_sync
# ===================================================================


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock()]
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                with patch.object(
                    connector, "_fetch_users", new_callable=AsyncMock
                ) as mock_users:
                    mock_users.return_value = []
                    with patch.object(
                        connector, "_fetch_teams", new_callable=AsyncMock
                    ) as mock_teams:
                        mock_teams.return_value = ([], [])
                        with patch.object(
                            connector,
                            "_sync_issues_for_teams",
                            new_callable=AsyncMock,
                            return_value=(set(), []),
                        ):
                            with patch.object(
                                connector,
                                "_sync_attachments",
                                new_callable=AsyncMock,
                            ):
                                with patch.object(
                                    connector,
                                    "_sync_documents",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        connector,
                                        "_sync_projects_for_teams",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            connector,
                                            "_sync_deleted_issues",
                                            new_callable=AsyncMock,
                                        ):
                                            with patch.object(
                                                connector,
                                                "_sync_deleted_projects",
                                                new_callable=AsyncMock,
                                            ):
                                                with patch.object(
                                                    connector,
                                                    "_sweep_placeholder_records",
                                                    new_callable=AsyncMock,
                                                    return_value=0,
                                                ):
                                                    await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_team_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock()]
        )

        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["team-1"]
        mock_filter.get_operator.return_value = MagicMock(value="in")

        mock_sync_filters = MagicMock(spec=FilterCollection)
        mock_sync_filters.get.return_value = mock_filter

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                mock_load.return_value = (mock_sync_filters, FilterCollection())
                with patch.object(
                    connector, "_fetch_users", new_callable=AsyncMock
                ) as mock_users:
                    mock_users.return_value = []
                    with patch.object(
                        connector, "_fetch_teams", new_callable=AsyncMock
                    ) as mock_teams:
                        mock_teams.return_value = ([], [])
                        with patch.object(
                            connector,
                            "_sync_issues_for_teams",
                            new_callable=AsyncMock,
                            return_value=(set(), []),
                        ):
                            with patch.object(
                                connector,
                                "_sync_attachments",
                                new_callable=AsyncMock,
                            ):
                                with patch.object(
                                    connector,
                                    "_sync_documents",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        connector,
                                        "_sync_projects_for_teams",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            connector,
                                            "_sync_deleted_issues",
                                            new_callable=AsyncMock,
                                        ):
                                            with patch.object(
                                                connector,
                                                "_sync_deleted_projects",
                                                new_callable=AsyncMock,
                                            ):
                                                with patch.object(
                                                    connector,
                                                    "_sweep_placeholder_records",
                                                    new_callable=AsyncMock,
                                                    return_value=0,
                                                ):
                                                    await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagated(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.side_effect = Exception("Config error")
            with pytest.raises(Exception, match="Config error"):
                await connector.run_sync()


# ===================================================================
# _process_issue_attachments
# ===================================================================


class TestProcessIssueAttachments:
    @pytest.mark.asyncio
    async def test_creates_new_attachment(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        children = await connector._process_issue_attachments(
            attachments_data=[attachment],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
        )
        assert len(children) == 1
        assert children[0].child_type == ChildType.RECORD

    @pytest.mark.asyncio
    async def test_uses_existing_attachment(self):
        connector = _make_connector()

        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "Existing File"

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "",
            "updatedAt": "",
        }

        children = await connector._process_issue_attachments(
            attachments_data=[attachment],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
        )
        assert len(children) == 1
        assert children[0].child_id == "existing-id"

    @pytest.mark.asyncio
    async def test_skips_empty_id(self):
        connector = _make_connector()
        children = await connector._process_issue_attachments(
            attachments_data=[{"id": ""}],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
        )
        assert len(children) == 0


# ===================================================================
# _process_issue_documents
# ===================================================================


class TestProcessIssueDocuments:
    @pytest.mark.asyncio
    async def test_creates_new_document(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        doc = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        children = await connector._process_issue_documents(
            documents_data=[doc],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
        )
        assert len(children) == 1


# ===================================================================
# _extract_files_from_markdown
# ===================================================================


class TestExtractFilesFromMarkdown:
    @pytest.mark.asyncio
    async def test_empty_markdown(self):
        connector = _make_connector()
        result, existing = await connector._extract_files_from_markdown(
            markdown_text="",
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
        )
        assert result == []
        assert existing == []

    @pytest.mark.asyncio
    async def test_extracts_new_files(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=512)

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        text = "[report.pdf](https://uploads.linear.app/report.pdf)"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result, existing = await connector._extract_files_from_markdown(
                markdown_text=text,
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                exclude_images=True,
            )
            assert len(result) == 1
            assert isinstance(result[0][0], FileRecord)

    @pytest.mark.asyncio
    async def test_returns_existing_files_as_children(self):
        connector = _make_connector()

        existing_file = MagicMock()
        existing_file.id = "existing-file-id"
        existing_file.record_type = RecordType.FILE
        existing_file.record_name = "report.pdf"

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing_file)

        text = "[report.pdf](https://uploads.linear.app/report.pdf)"

        result, existing = await connector._extract_files_from_markdown(
            markdown_text=text,
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
            exclude_images=True,
        )
        assert len(result) == 0
        assert len(existing) == 1
        assert existing[0].child_id == "existing-file-id"


# ===================================================================
# _fetch_teams with filters
# ===================================================================


class TestFetchTeamsWithFilters:
    @pytest.mark.asyncio
    async def test_fetch_teams_with_not_in_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {
                        "id": "team-1",
                        "name": "Engineering",
                        "key": "ENG",
                        "description": "Eng team",
                        "private": False,
                        "parent": None,
                        "members": {"nodes": []},
                    }
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            operator = MagicMock()
            operator.value = "not_in"
            user_groups, record_groups = await connector._fetch_teams(
                team_ids=["team-2"],
                team_ids_operator=operator,
            )
            assert len(record_groups) == 1

    @pytest.mark.asyncio
    async def test_fetch_teams_skips_missing_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {
                        "id": "",
                        "name": "Bad Team",
                        "key": "",
                        "private": False,
                        "members": {"nodes": []},
                    },
                    {
                        "id": "team-1",
                        "name": "Good Team",
                        "key": "GOOD",
                        "description": "",
                        "private": False,
                        "parent": None,
                        "members": {"nodes": []},
                    },
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            _, record_groups = await connector._fetch_teams()
            assert len(record_groups) == 1


# ===================================================================
# _process_project_external_links
# ===================================================================


class TestProcessProjectExternalLinks:
    @pytest.mark.asyncio
    async def test_creates_link_records(self):
        connector = _make_connector()
        connector.indexing_filters = None

        link_data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector.data_entities_processor.get_record_by_weburl = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_external_links(
            external_links_data=[link_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(block_groups) == 0

    @pytest.mark.asyncio
    async def test_creates_block_groups(self):
        connector = _make_connector()
        connector.indexing_filters = None

        link_data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External",
            "createdAt": "",
            "updatedAt": "",
        }

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector.data_entities_processor.get_record_by_weburl = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_external_links(
            external_links_data=[link_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            create_block_groups=True,
        )
        assert len(block_groups) == 1


# ===================================================================
# _process_project_documents
# ===================================================================


class TestProcessProjectDocuments:
    @pytest.mark.asyncio
    async def test_creates_webpage_records(self):
        connector = _make_connector()
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_documents(
            documents_data=[doc_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(block_groups) == 0

    @pytest.mark.asyncio
    async def test_creates_block_groups_for_docs(self):
        connector = _make_connector()
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "",
            "updatedAt": "",
        }

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_documents(
            documents_data=[doc_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            create_block_groups=True,
        )
        assert len(block_groups) == 1
