"""Connector-level tests for behaviour introduced with the Linear gateway, listing
checkpoints, record removal and reconciliation: what the sync does around them."""

import base64
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.registry.filters import Filter, FilterCollection
from app.connectors.sources.linear import connector as connector_module
from app.connectors.sources.linear.api_gateway import (
    LinearApiError,
    LinearAuthError,
    LinearGateway,
    LinearNotFound,
    LinearRateLimited,
    LinearTransientError,
)
from app.connectors.sources.linear.connector import (
    MAX_INLINE_IMAGE_BYTES,
    LinearConnector,
    file_record_id,
    link_destination,
)
from app.connectors.sources.linear.listing_checkpoint import ListingCheckpoint
from app.connectors.sources.linear.record_removal import RecordRemover
from app.connectors.sources.linear.source_reconciler import SourceReconciler
from app.connectors.sources.linear.team_access import WORKSPACE_MEMBERS_GROUP_ID
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    LinkRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.sources.client.graphql.response import GraphQLResponse

CONNECTOR_ID = "linear-conn-1"
NOW = 1_705_400_000_000
UPLOAD = "https://uploads.linear.app/org/spec.pdf"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_sync_point() -> MagicMock:
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


def _make_connector() -> LinearConnector:
    processor = MagicMock()
    processor.org_id = "org-1"
    for name, value in (
        ("get_all_active_users", [MagicMock(email="alice@test.com")]),
        ("get_record_by_external_id", None),
        ("get_record_by_weburl", None),
        ("get_records_by_parent", []),
        ("get_records_by_record_type", []),
        ("get_records_in_record_group", []),
        ("get_nodes_by_filters", []),
        ("on_new_app_users", None),
        ("on_new_user_groups", None),
        ("on_new_record_groups", None),
        ("on_new_records", None),
        ("on_record_content_update", None),
        ("on_records_deleted_cascade", None),
        ("on_records_detached_from_parent", None),
        ("on_record_group_deleted", None),
        ("on_user_group_deleted", None),
    ):
        setattr(processor, name, AsyncMock(return_value=value))

    connector = LinearConnector(
        logger=MagicMock(),
        data_entities_processor=processor,
        data_store_provider=MagicMock(),
        config_service=AsyncMock(),
        connector_id=CONNECTOR_ID,
        scope="team",
        created_by="test-user",
    )
    for name in (
        "issues_sync_point", "attachments_sync_point", "documents_sync_point",
        "projects_sync_point", "deletion_sync_point", "reconcile_sync_point",
    ):
        setattr(connector, name, _memory_sync_point())
    connector._reconciler._sync_point = connector.reconcile_sync_point
    connector._sleep = AsyncMock()
    connector._api._sleep = connector._sleep
    connector._now = MagicMock(return_value=NOW)
    connector.notify = AsyncMock()
    connector.sync_filters = None
    connector.indexing_filters = None
    return connector


def _use_datasource(connector, datasource) -> MagicMock:
    connector.data_source = MagicMock()
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)
    return datasource


def _ok(data) -> GraphQLResponse:
    return GraphQLResponse(success=True, data=data)


def _page(nodes, has_next=False, end_cursor=None) -> dict:
    return {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}}


def _team_rg(team_id="team-1", key="ENG", name="Engineering") -> RecordGroup:
    return RecordGroup(
        id=f"rg-{team_id}", org_id="org-1", external_group_id=team_id, connector_id=CONNECTOR_ID,
        connector_name=Connectors.LINEAR, name=name, short_name=key, group_type=RecordGroupType.PROJECT,
    )


def _ticket(external_id="issue-1", team_id="team-1", record_id=None, **overrides) -> TicketRecord:
    return TicketRecord(**{
        "id": record_id or f"node-{external_id}", "org_id": "org-1", "record_name": external_id,
        "record_type": RecordType.TICKET, "external_record_id": external_id, "version": 1,
        "origin": OriginTypes.CONNECTOR, "connector_name": Connectors.LINEAR, "connector_id": CONNECTOR_ID,
        "external_record_group_id": team_id, "record_group_type": RecordGroupType.PROJECT,
        **overrides,
    })


def _issue(issue_id="issue-1", identifier="ENG-1", team_id="team-1", **overrides) -> dict:
    data = {
        "id": issue_id,
        "identifier": identifier,
        "title": f"Issue {identifier}",
        "description": "",
        "url": f"https://linear.app/org/issue/{identifier}",
        "priority": 2,
        "state": {"name": "Todo", "type": "unstarted"},
        "assignee": None,
        "creator": None,
        "parent": None,
        "team": {"id": team_id},
        "labels": {"nodes": []},
        "relations": {"nodes": []},
        "comments": {"nodes": []},
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestCollaborators:
    def test_connector_owns_a_gateway_a_remover_and_a_reconciler(self) -> None:
        connector = _make_connector()
        assert isinstance(connector._api, LinearGateway)
        assert isinstance(connector._remover, RecordRemover)
        assert isinstance(connector._reconciler, SourceReconciler)
        # The reconciler removes through the same remover and talks through the same gateway.
        assert connector._reconciler._remover is connector._remover
        assert connector._reconciler._gateway is connector._api

    @pytest.mark.asyncio
    async def test_gateway_asks_the_connector_for_a_fresh_datasource_each_call(self) -> None:
        connector = _make_connector()
        datasource = MagicMock()
        datasource.execute_operation = AsyncMock(return_value=_ok({"viewer": {"id": "u1"}}))
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)

        assert await connector._api.call("viewer") == {"viewer": {"id": "u1"}}
        assert await connector._api.call("viewer") == {"viewer": {"id": "u1"}}

        assert connector._get_fresh_datasource.await_count == 2

    def test_checkpoints_use_the_connectors_clock(self) -> None:
        connector = _make_connector()
        checkpoint = connector._checkpoint(connector.issues_sync_point, "anything")
        assert isinstance(checkpoint, ListingCheckpoint)
        assert checkpoint._now() == NOW

    def test_team_issue_checkpoint_is_keyed_by_team_id(self) -> None:
        connector = _make_connector()
        checkpoint = connector._team_issue_checkpoint("5f2c-team-uuid")
        assert checkpoint._key == "team_issues_5f2c-team-uuid"
        assert checkpoint._sync_point is connector.issues_sync_point

    def test_per_key_checkpoint_helpers_are_gone(self) -> None:
        for removed in (
            "_get_team_sync_checkpoint", "_update_team_sync_checkpoint",
            "_get_team_project_sync_checkpoint", "_update_team_project_sync_checkpoint",
            "_get_attachments_sync_checkpoint", "_update_attachments_sync_checkpoint",
            "_get_documents_sync_checkpoint", "_update_documents_sync_checkpoint",
            "_get_deletion_sync_checkpoint", "_update_deletion_sync_checkpoint",
            "_fetch_projects_for_team_batch",
        ):
            assert not hasattr(LinearConnector, removed), removed


# ---------------------------------------------------------------------------
# Files: only Linear uploads, one record per parent
# ---------------------------------------------------------------------------


class TestFileLinksInMarkdown:
    FOREIGN = [
        "https://example.com/report.pdf",
        "http://uploads.linear.app/report.pdf",
        "https://uploads.linear.app.evil.example/report.pdf",
        "https://evil.example/uploads.linear.app/report.pdf",
        "https://user:secret@uploads.linear.app/report.pdf",
        "https://uploads.linear.app:8443/report.pdf",
        "https://uploads.linear.app@evil.example/report.pdf",
    ]

    @pytest.mark.parametrize("url", FOREIGN)
    def test_links_to_other_hosts_are_not_files(self, url) -> None:
        connector = _make_connector()
        for markdown in (f"See [report.pdf]({url}) and ![shot]({url})",
                         f"See [report.pdf](<{url}>) and ![shot](<{url}> \"title\")"):
            assert connector._extract_file_urls_from_markdown(markdown) == []
            assert connector._extract_file_urls_from_markdown(markdown, exclude_images=True) == []

    @pytest.mark.parametrize("raw, url", [
        (f"{UPLOAD}", UPLOAD),
        (f" {UPLOAD} ", UPLOAD),
        (f"<{UPLOAD}>", UPLOAD),
        (f'{UPLOAD} "Spec v2"', UPLOAD),
        (f'<{UPLOAD}> "Spec v2"', UPLOAD),
        ("<https://uploads.linear.app/org/no-end", "<https://uploads.linear.app/org/no-end"),
        ("", ""),
    ])
    def test_link_destination_forms(self, raw, url) -> None:
        assert link_destination(raw) == url

    def test_an_upload_in_angle_brackets_is_a_file(self) -> None:
        """How Linear stores a description written through its API (seen on a live workspace)."""
        connector = _make_connector()
        markdown = f"Spec: [spec.pdf](<{UPLOAD}>) and [report.pdf](<https://evil.example/uploads.linear.app/r.pdf>)"
        assert connector._extract_file_urls_from_markdown(markdown, exclude_images=True) == [
            {"url": UPLOAD, "filename": "spec.pdf", "alt_text": "spec.pdf"},
        ]

    def test_only_the_upload_is_taken_from_mixed_text(self) -> None:
        connector = _make_connector()
        markdown = (
            f"[spec.pdf]({UPLOAD}) "
            "[phish.pdf](https://uploads.linear.app.evil.example/phish.pdf) "
            "![shot](https://uploads.linear.app/org/shot.png) "
            "![tracker](https://evil.example/pixel.png)"
        )

        assert connector._extract_file_urls_from_markdown(markdown) == [
            {"url": "https://uploads.linear.app/org/shot.png", "filename": "shot", "alt_text": "shot"},
            {"url": UPLOAD, "filename": "spec.pdf", "alt_text": "spec.pdf"},
        ]
        assert connector._extract_file_urls_from_markdown(markdown, exclude_images=True) == [
            {"url": UPLOAD, "filename": "spec.pdf", "alt_text": "spec.pdf"},
        ]

    @pytest.mark.asyncio
    async def test_foreign_link_creates_no_record_and_no_request(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.get_file_size = AsyncMock(return_value=1)

        records, children = await connector._extract_files_from_markdown(
            markdown_text="[phish.pdf](https://uploads.linear.app.evil.example/phish.pdf)",
            parent_external_id="issue-1", parent_node_id="node-1",
            parent_record_type=RecordType.TICKET, team_id="team-1",
        )

        assert records == [] and children == []
        datasource.get_file_size.assert_not_awaited()
        connector.data_entities_processor.get_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_file_record_is_identified_by_url_and_parent(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.get_file_size = AsyncMock(return_value=2048)

        records, children = await connector._extract_files_from_markdown(
            markdown_text=f"[spec.pdf]({UPLOAD})",
            parent_external_id="issue-1", parent_node_id="node-1",
            parent_record_type=RecordType.TICKET, team_id="team-1",
            parent_weburl="https://linear.app/org/issue/ENG-1",
        )

        assert children == []
        (file_record, permissions), = records
        assert isinstance(file_record, FileRecord)
        assert file_record.external_record_id == f"{UPLOAD}#parent=issue-1"
        assert file_record.parent_external_record_id == "issue-1"
        assert file_record.external_record_group_id == "team-1"
        assert file_record.size_in_bytes == 2048
        assert permissions == []
        # The existing record is looked up under the same per-parent id...
        connector.data_entities_processor.get_record_by_external_id.assert_awaited_once_with(
            connector_id=CONNECTOR_ID, external_record_id=f"{UPLOAD}#parent=issue-1")
        # ...and the size is asked for with the upload URL itself.
        datasource.get_file_size.assert_awaited_once_with(UPLOAD)

    @pytest.mark.asyncio
    async def test_same_upload_under_two_issues_is_two_records(self) -> None:
        connector = _make_connector()
        _use_datasource(connector, MagicMock()).get_file_size = AsyncMock(return_value=1)
        ids = []
        for issue_id in ("issue-1", "issue-2"):
            records, _ = await connector._extract_files_from_markdown(
                markdown_text=f"[spec.pdf]({UPLOAD})",
                parent_external_id=issue_id, parent_node_id=f"node-{issue_id}",
                parent_record_type=RecordType.TICKET, team_id="team-1",
            )
            ids.append(records[0][0].external_record_id)

        assert ids == [file_record_id(UPLOAD, "issue-1"), file_record_id(UPLOAD, "issue-2")]

    @pytest.mark.asyncio
    async def test_file_already_stored_for_this_parent_is_a_child_not_a_new_record(self) -> None:
        connector = _make_connector()
        existing = MagicMock(id="node-file", record_type=RecordType.FILE, record_name="spec.pdf")
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        records, children = await connector._extract_files_from_markdown(
            markdown_text=f"[spec.pdf]({UPLOAD})",
            parent_external_id="issue-1", parent_node_id="node-1",
            parent_record_type=RecordType.TICKET, team_id="team-1",
        )

        assert records == []
        assert [(c.child_id, c.child_name) for c in children] == [("node-file", "spec.pdf")]


class TestInlineImages:
    def _datasource(self, connector, files) -> AsyncIterator[list]:
        requested = []

        async def download(url) -> AsyncIterator[bytes]:
            requested.append(url)
            for chunk in files[url]:
                yield chunk

        datasource = _use_datasource(connector, MagicMock())
        datasource.download_file = download
        return requested

    @pytest.mark.asyncio
    async def test_image_on_another_host_is_never_fetched(self) -> None:
        connector = _make_connector()
        requested = self._datasource(connector, {})
        markdown = "![pixel](https://evil.example/pixel.png) ![x](https://uploads.linear.app.evil.example/x.png)"

        assert await connector._convert_images_to_base64_in_markdown(markdown) == markdown

        assert requested == []
        # Not even a token is prepared when there is nothing of Linear's to fetch.
        connector._get_fresh_datasource.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_is_inlined_and_the_foreign_image_is_left(self) -> None:
        connector = _make_connector()
        image = "https://uploads.linear.app/org/shot.png"
        requested = self._datasource(connector, {image: [b"png-", b"bytes"]})
        markdown = f"![shot]({image}) ![pixel](https://evil.example/pixel.png)"

        result = await connector._convert_images_to_base64_in_markdown(markdown)

        encoded = base64.b64encode(b"png-bytes").decode()
        assert result == f"![shot](data:image/png;base64,{encoded}) ![pixel](https://evil.example/pixel.png)"
        assert requested == [image]

    def test_inline_limit(self) -> None:
        assert MAX_INLINE_IMAGE_BYTES == 5 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_image_above_the_limit_is_left_as_a_link(self) -> None:
        connector = _make_connector()
        big = "https://uploads.linear.app/org/big.png"
        small = "https://uploads.linear.app/org/small.png"
        chunks_read = []

        async def download(url) -> AsyncIterator[bytes]:
            for chunk in ([b"12345", b"67890", b"X", b"never-read"] if url == big else [b"ok"]):
                chunks_read.append((url, chunk))
                yield chunk

        _use_datasource(connector, MagicMock()).download_file = download
        markdown = f"![big]({big}) ![small]({small})"

        with patch.object(connector_module, "MAX_INLINE_IMAGE_BYTES", 10):
            result = await connector._convert_images_to_base64_in_markdown(markdown)

        encoded = base64.b64encode(b"ok").decode()
        assert result == f"![big]({big}) ![small](data:image/png;base64,{encoded})"
        # Reading stops as soon as the limit is passed.
        assert (big, b"never-read") not in chunks_read

    @pytest.mark.asyncio
    async def test_image_exactly_at_the_limit_is_inlined(self) -> None:
        connector = _make_connector()
        image = "https://uploads.linear.app/org/edge.png"
        self._datasource(connector, {image: [b"12345", b"67890"]})

        with patch.object(connector_module, "MAX_INLINE_IMAGE_BYTES", 10):
            result = await connector._convert_images_to_base64_in_markdown(f"![edge]({image})")

        assert result == f"![edge](data:image/png;base64,{base64.b64encode(b'1234567890').decode()})"

    @pytest.mark.asyncio
    async def test_images_stop_being_inlined_once_the_text_is_full(self) -> None:
        connector = _make_connector()
        first = "https://uploads.linear.app/org/1.png"
        second = "https://uploads.linear.app/org/2.png"
        third = "https://uploads.linear.app/org/3.png"
        self._datasource(connector, {first: [b"12345"], second: [b"67890"], third: [b"abc"]})
        markdown = f"![1]({first}) ![2]({second}) ![3]({third})"

        with patch.object(connector_module, "MAX_INLINE_IMAGES_TOTAL_BYTES", 8):
            result = await connector._convert_images_to_base64_in_markdown(markdown)

        # 5 bytes fit, 5 more do not, 3 more do.
        assert result == (
            f"![1](data:image/png;base64,{base64.b64encode(b'12345').decode()}) "
            f"![2]({second}) "
            f"![3](data:image/png;base64,{base64.b64encode(b'abc').decode()})"
        )


# ---------------------------------------------------------------------------
# Parsing: a comment or update whose author is gone
# ---------------------------------------------------------------------------


class TestAuthorlessComments:
    @pytest.mark.asyncio
    async def test_issue_comment_with_null_user(self) -> None:
        connector = _make_connector()
        issue = _issue(description="Body", comments={"nodes": [
            {"id": "c1", "body": "From a deleted account", "user": None, "parent": None,
             "url": "https://linear.app/org/issue/ENG-1#c1",
             "createdAt": "2024-01-02T10:00:00.000Z", "updatedAt": "2024-01-02T10:00:00.000Z"},
            {"id": "c2", "body": "From Alice", "user": {"id": "u1", "displayName": "Alice"},
             "url": "https://linear.app/org/issue/ENG-1#c2",
             "createdAt": "2024-01-03T10:00:00.000Z", "updatedAt": "2024-01-03T10:00:00.000Z"},
        ]})

        container = await connector._parse_issue_to_blocks(issue, weburl=issue["url"])

        names = [group.name for group in container.block_groups]
        assert "Comment by Unknown" in names
        assert "Comment by Alice" in names

    @pytest.mark.asyncio
    async def test_project_comment_update_and_update_comment_with_null_user(self) -> None:
        connector = _make_connector()
        project = {
            "id": "proj-1",
            "name": "Alpha",
            "url": "https://linear.app/org/project/alpha",
            "description": "Desc",
            "content": "Body",
            "comments": {"nodes": [{
                "id": "pc1", "body": "Orphaned project comment", "user": None,
                "url": "https://linear.app/org/project/alpha#pc1",
                "createdAt": "2024-01-02T10:00:00.000Z", "updatedAt": "2024-01-02T10:00:00.000Z",
            }]},
            "projectMilestones": {"nodes": []},
            "projectUpdates": {"nodes": [{
                "id": "upd-1", "body": "Shipped", "user": None,
                "url": "https://linear.app/org/project/alpha/updates/1",
                "createdAt": "2024-06-01T10:00:00.000Z",
                "comments": {"nodes": [{
                    "id": "uc1", "body": "Orphaned update comment", "user": None,
                    "url": "https://linear.app/org/project/alpha/updates/1#uc1",
                    "createdAt": "2024-06-01T11:00:00.000Z", "updatedAt": "2024-06-01T11:00:00.000Z",
                }]},
            }]},
        }

        container = await connector._parse_project_to_blocks(project, weburl=project["url"])

        dumped = container.model_dump_json()
        assert "Project Update by Unknown" in dumped
        assert "Orphaned project comment" in dumped
        assert "Orphaned update comment" in dumped


# ---------------------------------------------------------------------------
# Streaming: what a failed Linear call means to a reader
# ---------------------------------------------------------------------------


class TestStreamError:
    @pytest.mark.parametrize("error,expected_status", [
        (LinearNotFound("gone", 400), 404),
        (LinearRateLimited("slow down", 400), 429),
        (LinearRateLimited("slow down", 429), 429),
        (LinearAuthError("token revoked", 401), 409),
        (LinearAuthError("forbidden", 403), 409),
        (LinearAuthError("no status", None), 409),
        (LinearTransientError("bad gateway", 502), 502),
        (LinearTransientError("unavailable", 503), 502),
        (LinearTransientError("connection reset", None), 502),
        (LinearApiError("bad variable", 400), 422),
    ])
    def test_mapping(self, error, expected_status) -> None:
        connector = _make_connector()
        result = connector._stream_error(error)
        assert isinstance(result, HTTPException)
        assert result.status_code == expected_status

    def test_reader_is_never_shown_linears_own_message(self) -> None:
        connector = _make_connector()
        result = connector._stream_error(LinearAuthError("Bearer lin_api_SECRET is invalid", 401))
        assert "SECRET" not in str(result.detail)

    @pytest.mark.asyncio
    async def test_fetch_for_stream_returns_the_entity(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.document = AsyncMock(return_value=_ok({"document": {"id": "doc-1", "content": "x"}}))

        entity = await connector._fetch_for_stream("document", lambda ds: ds.document(id="doc-1"), "document")

        assert entity == {"id": "doc-1", "content": "x"}

    @pytest.mark.asyncio
    async def test_fetch_for_stream_chains_the_cause(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.document = AsyncMock(
            return_value=GraphQLResponse(success=False, message="Unauthorized", status_code=401))

        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_for_stream("document", lambda ds: ds.document(id="doc-1"), "document")

        assert exc_info.value.status_code == 409
        assert isinstance(exc_info.value.__cause__, LinearAuthError)


# ---------------------------------------------------------------------------
# Issues: children that are gone, and an issue that changed team
# ---------------------------------------------------------------------------


class TestIssueToRecords:
    @pytest.mark.asyncio
    async def test_new_issue_has_no_children_to_settle(self) -> None:
        connector = _make_connector()

        records = await connector._issue_to_records(_issue(), "team-1")

        assert [type(r).__name__ for r, _ in records] == ["TicketRecord"]
        assert records[0][0].external_record_group_id == "team-1"
        assert "issue-1" in connector._refreshed_issue_ids
        connector.data_entities_processor.get_records_by_parent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_file_no_longer_linked_from_the_issue_is_removed(self) -> None:
        connector = _make_connector()
        processor = connector.data_entities_processor
        existing = _ticket()
        processor.get_record_by_external_id = AsyncMock(side_effect=lambda connector_id, external_record_id: (
            existing if external_record_id == "issue-1" else None))

        def child(record_type, external_id) -> MagicMock:
            return MagicMock(record_type=record_type, external_record_id=external_id)

        still_linked = child(RecordType.FILE, file_record_id(UPLOAD, "issue-1"))
        in_comment = child(RecordType.FILE, file_record_id("https://uploads.linear.app/org/c.pdf", "issue-1"))
        unlinked = child(RecordType.FILE, file_record_id("https://uploads.linear.app/org/old.pdf", "issue-1"))
        legacy = child(RecordType.FILE, UPLOAD)
        attachment = child(RecordType.LINK, "att-1")
        document = child(RecordType.WEBPAGE, "doc-1")
        sub_issue = child(RecordType.TICKET, "issue-2")
        processor.get_records_by_parent = AsyncMock(return_value=[
            still_linked, in_comment, unlinked, legacy, attachment, document, sub_issue])
        connector._remover.remove_record = AsyncMock()
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))

        issue = _issue(
            description=f"[spec.pdf]({UPLOAD})",
            comments={"nodes": [{"id": "c1", "body": "[c.pdf](https://uploads.linear.app/org/c.pdf)"}]},
        )
        await connector._issue_to_records(issue, "team-1")

        removed = [c.args[0] for c in connector._remover.remove_record.await_args_list]
        # Gone from the text, or stored the old way under the bare URL. Attachments
        # and documents have their own listings; a sub-issue is an issue of its own.
        assert removed == [unlinked, legacy]

    @pytest.mark.asyncio
    async def test_issue_that_changed_team_takes_its_children_along(self) -> None:
        connector = _make_connector()
        processor = connector.data_entities_processor
        stored_issue = _ticket(team_id="team-old", record_id="node-issue")
        stored_file = MagicMock(
            id="node-file", record_type=RecordType.FILE, record_name="spec.pdf", version=0,
            size_in_bytes=10, created_at=1, external_record_id=file_record_id(UPLOAD, "issue-1"))
        stored = {"issue-1": stored_issue, file_record_id(UPLOAD, "issue-1"): stored_file}
        processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: stored.get(external_record_id))
        processor.get_records_by_parent = AsyncMock(return_value=[])

        datasource = _use_datasource(connector, MagicMock())

        async def execute_operation(operation, variables) -> GraphQLResponse:
            assert variables == {"id": "issue-1", "first": 100}
            if operation == "issueAttachments":
                return _ok({"issue": {"attachments": _page([
                    {"id": "att-1", "url": "https://github.com/org/repo/pull/1", "title": "PR"},
                    {"id": "att-no-url", "title": "broken"},
                ])}})
            return _ok({"issue": {"documents": _page([
                {"id": "doc-1", "url": "https://linear.app/org/document/doc-1", "title": "Design"},
            ])}})

        datasource.execute_operation = AsyncMock(side_effect=execute_operation)

        records = await connector._issue_to_records(
            _issue(description=f"[spec.pdf]({UPLOAD})", team_id="team-new"), "team-new")

        summary = [(type(r).__name__, r.external_record_id, r.external_record_group_id) for r, _ in records]
        assert summary == [
            ("TicketRecord", "issue-1", "team-new"),
            # The file exists already; it is emitted again so it is filed under the new team.
            ("FileRecord", file_record_id(UPLOAD, "issue-1"), "team-new"),
            ("LinkRecord", "att-1", "team-new"),
            ("WebpageRecord", "doc-1", "team-new"),
        ]
        assert records[0][0].id == "node-issue"
        assert records[1][0].id == "node-file"
        assert isinstance(records[2][0], LinkRecord) and isinstance(records[3][0], WebpageRecord)
        assert all(r.parent_external_record_id == "issue-1" for r, _ in records[1:])
        assert [c.args[0] for c in datasource.execute_operation.await_args_list] == [
            "issueAttachments", "issueDocuments"]

    @pytest.mark.asyncio
    async def test_issue_in_the_same_team_does_not_refile_its_children(self) -> None:
        connector = _make_connector()
        processor = connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(return_value=_ticket(team_id="team-1"))
        datasource = _use_datasource(connector, MagicMock())
        datasource.execute_operation = AsyncMock()

        records = await connector._issue_to_records(_issue(), "team-1")

        assert len(records) == 1
        datasource.execute_operation.assert_not_awaited()


# ---------------------------------------------------------------------------
# Comment activity
# ---------------------------------------------------------------------------


class TestCommentActivity:
    @pytest.mark.asyncio
    async def test_first_run_only_sets_the_checkpoint(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.execute_operation = AsyncMock()

        await connector._sync_comment_activity({"team-1"})

        # Everything was just read with its comments: nothing to ask.
        datasource.execute_operation.assert_not_awaited()
        assert connector.issues_sync_point.store["comment_activity"]["last_sync_time"] == NOW

    @pytest.mark.asyncio
    async def test_parents_of_new_comments_are_refreshed_and_reindexed(self) -> None:
        connector = _make_connector()
        connector.issues_sync_point.store["comment_activity"] = {"last_sync_time": NOW - 3_600_000}
        connector._refreshed_issue_ids = {"issue-already"}
        connector._refreshed_project_ids = {"proj-already"}
        processor = connector.data_entities_processor
        known_issue = _ticket("issue-known")
        placeholder = _ticket("issue-stub", is_placeholder=True)
        stored = {"issue-known": known_issue, "issue-stub": placeholder, "proj-known": MagicMock(id="node-proj")}
        processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: stored.get(external_record_id))

        datasource = _use_datasource(connector, MagicMock())

        async def execute_operation(operation, variables) -> GraphQLResponse:
            if operation == "commentActivity":
                return _ok({"comments": _page([
                    {"id": "c1", "issue": {"id": "issue-known"}},
                    {"id": "c2", "issue": {"id": "issue-known"}},
                    {"id": "c3", "issue": {"id": "issue-new"}},
                    {"id": "c4", "issue": {"id": "issue-stub"}},
                    {"id": "c5", "issue": {"id": "issue-already"}},
                    {"id": "c6", "issue": {"id": "issue-elsewhere"}},
                    {"id": "c7", "issue": None, "project": {"id": "proj-known"}},
                    {"id": "c8", "issue": None, "project": None,
                     "projectUpdate": {"id": "upd-1", "project": {"id": "proj-from-update-comment"}}},
                    {"id": "c9", "issue": None, "project": None, "projectUpdate": None},
                ])})
            if operation == "projectUpdateActivity":
                return _ok({"projectUpdates": _page([
                    {"id": "upd-2", "project": {"id": "proj-new"}},
                    {"id": "upd-3", "project": {"id": "proj-already"}},
                    {"id": "upd-4", "project": None},
                ])})
            raise AssertionError(operation)

        datasource.execute_operation = AsyncMock(side_effect=execute_operation)
        datasource.issues = AsyncMock(return_value=_ok({"issues": _page([
            _issue("issue-known"), _issue("issue-new"), _issue("issue-stub"),
            _issue("issue-elsewhere", team_id="team-9"),
        ])}))

        async def issue_to_records(issue_data, team_id, *args) -> list:
            return [(_ticket(issue_data["id"], team_id, record_id=f"fresh-{issue_data['id']}"), [])]

        connector._issue_to_records = AsyncMock(side_effect=issue_to_records)

        async def project_to_records(project_id, scope, *args) -> list:
            if project_id == "proj-new":
                return []  # not in a synced team
            return [(MagicMock(id=f"child-of-{project_id}"), []), (MagicMock(id=f"fresh-{project_id}"), [])]

        connector._project_to_records = AsyncMock(side_effect=project_to_records)

        await connector._sync_comment_activity({"team-1"})

        since = connector._linear_datetime_from_timestamp(NOW - 3_600_000 - 60_000)
        assert [c.args for c in datasource.execute_operation.await_args_list] == [
            ("commentActivity", {"filter": {"updatedAt": {"gt": since}}, "first": 250}),
            ("projectUpdateActivity", {"filter": {"updatedAt": {"gt": since}}, "first": 250}),
        ]
        # One request for the issues this sync has not already read.
        datasource.issues.assert_awaited_once_with(
            first=4, filter={"id": {"in": ["issue-elsewhere", "issue-known", "issue-new", "issue-stub"]}})
        # An issue of a team outside the sync is not stored.
        assert [c.args[0]["id"] for c in connector._issue_to_records.await_args_list] == [
            "issue-known", "issue-new", "issue-stub"]
        assert [c.args[0] for c in connector._project_to_records.await_args_list] == [
            "proj-from-update-comment", "proj-known", "proj-new"]
        assert processor.on_new_records.await_count == 5
        # Only what was already indexed needs re-indexing: a new record is indexed
        # anyway, a placeholder has no content, and for a project it is the project
        # record (the last one) that carries the discussion.
        reindexed = [c.args[0].id for c in processor.on_record_content_update.await_args_list]
        assert reindexed == ["fresh-issue-known", "fresh-proj-known"]
        assert connector.issues_sync_point.store["comment_activity"]["last_sync_time"] == NOW

    @pytest.mark.asyncio
    async def test_failed_activity_listing_keeps_the_checkpoint(self) -> None:
        connector = _make_connector()
        connector.issues_sync_point.store["comment_activity"] = {"last_sync_time": NOW - 3_600_000}
        datasource = _use_datasource(connector, MagicMock())
        datasource.execute_operation = AsyncMock(
            return_value=GraphQLResponse(success=False, message="Internal error", status_code=500))

        with pytest.raises(LinearTransientError):
            await connector._sync_comment_activity({"team-1"})

        assert connector.issues_sync_point.store["comment_activity"] == {"last_sync_time": NOW - 3_600_000}
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ---------------------------------------------------------------------------
# Stale teams and reconciliation
# ---------------------------------------------------------------------------


class TestRemoveStaleTeams:
    @pytest.mark.asyncio
    async def test_the_team_filter_is_not_consulted(self) -> None:
        """Only the synced teams go to the reconciler, which removes what Linear deleted or
        hides; what the filter leaves out is unlinked by the full sync a filter change runs."""
        connector = _make_connector()
        connector._reconciler.remove_stale_teams = AsyncMock(return_value=[])

        await connector._remove_stale_teams({"team-a"})

        connector._reconciler.remove_stale_teams.assert_awaited_once_with({"team-a"})

    @pytest.mark.asyncio
    async def test_removed_team_forgets_its_issue_checkpoint(self) -> None:
        connector = _make_connector()
        connector.issues_sync_point.store["team_issues_team-gone"] = {
            "last_sync_time": NOW - 1000, "resume_cursor": "cursor-1",
            "resume_since": 5, "resume_started_at": NOW - 500, "held_runs": 1,
        }
        connector.issues_sync_point.store["team_issues_team-kept"] = {"last_sync_time": NOW - 1000}
        connector._reconciler.remove_stale_teams = AsyncMock(return_value=["team-gone"])

        await connector._remove_stale_teams({"team-kept"})

        # If the team comes back it is read from the beginning.
        assert connector.issues_sync_point.store["team_issues_team-gone"] == {
            "last_sync_time": 0, "resume_cursor": "", "resume_since": 0, "resume_started_at": 0, "held_runs": 0}
        assert connector.issues_sync_point.store["team_issues_team-kept"] == {"last_sync_time": NOW - 1000}


class TestDateScope:
    """An issue or project the date filters leave out may stay stored (a full sync left it
    unlinked); nothing under it is synced, so nothing makes it readable again."""

    @staticmethod
    def _connector() -> LinearConnector:
        connector = _make_connector()
        connector.sync_filters = FilterCollection(filters=[
            Filter(key="created", type="datetime", operator="is_between", value={"start": 1000, "end": 2000}),
            Filter(key="modified", type="datetime", operator="is_before", value={"start": None, "end": 3000}),
        ])
        return connector

    @pytest.mark.parametrize("created, updated, inside", [
        (1500, 2500, True),
        (500, 2500, False),   # created before the range
        (2500, 2500, False),  # created after it
        (1500, 3500, False),  # modified after the modified range
        (None, None, True),   # a stub without dates is judged by its own listing
    ])
    def test_created_and_modified_ranges(self, created, updated, inside) -> None:
        record = _ticket(source_created_at=created, source_updated_at=updated)
        assert self._connector()._in_date_scope(record) is inside

    def test_without_date_filters_everything_is_in_scope(self) -> None:
        connector = _make_connector()
        assert connector._in_date_scope(_ticket(source_created_at=1, source_updated_at=1)) is True


class TestReindexRespectsTheFilters:
    """A reindex writes what changed in Linear. What the filters leave out stays unlinked, and
    an issue moved to a team the filter leaves out is removed, not filed under that team."""

    ISSUE_CREATED = 1704103200000  # _issue()'s createdAt, 2024-01-01T10:00:00Z

    @staticmethod
    def _connector(*filters: Filter) -> LinearConnector:
        connector = _make_connector()
        connector.sync_filters = FilterCollection(filters=list(filters))
        connector._remover.remove_record = AsyncMock()
        return connector

    @staticmethod
    def _teams(operator: str, *team_ids: str) -> Filter:
        return Filter(key="team_ids", type="list", operator=operator, value=list(team_ids))

    def test_the_team_filter(self) -> None:
        include = self._connector(self._teams("in", "team-1"))
        exclude = self._connector(self._teams("not_in", "team-1"))
        assert include._team_in_filter("team-1") and not include._team_in_filter("team-2")
        assert not exclude._team_in_filter("team-1") and exclude._team_in_filter("team-2")
        assert _make_connector()._team_in_filter("team-2"), "without a team filter every team is synced"

    @staticmethod
    async def _refetch(connector: LinearConnector, issue: dict, stored: TicketRecord) -> object:
        datasource = _use_datasource(connector, MagicMock())
        datasource.issue = AsyncMock(return_value=_ok({"issue": issue}))
        return await connector._check_and_fetch_updated_issue(stored)

    @pytest.mark.asyncio
    async def test_an_issue_moved_to_a_deselected_team_is_removed(self) -> None:
        connector = self._connector(self._teams("in", "team-1"))
        # The deselected team's group is still stored, unlinked by the full sync
        connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock())
        stored = _ticket("issue-1", team_id="team-1", source_updated_at=1)

        assert await self._refetch(connector, _issue(team_id="team-2"), stored) is None
        connector._remover.remove_record.assert_awaited_once_with(stored)

    @pytest.mark.asyncio
    async def test_an_issue_of_a_deselected_team_is_not_written(self) -> None:
        connector = self._connector(self._teams("in", "team-2"))
        stored = _ticket("issue-1", team_id="team-1", source_updated_at=1)

        assert await self._refetch(connector, _issue(team_id="team-1"), stored) is None
        connector._remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("start, written", [(ISSUE_CREATED + 1, False), (ISSUE_CREATED - 1, True)])
    async def test_the_date_filters_decide_what_is_written(self, start, written) -> None:
        connector = self._connector(Filter(key="created", type="datetime", operator="is_after",
                                           value={"start": start, "end": None}))
        stored = _ticket("issue-1", team_id="team-1", source_updated_at=1)

        assert (await self._refetch(connector, _issue(team_id="team-1"), stored) is not None) is written
        connector._remover.remove_record.assert_not_awaited()


class TestReconcile:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("forced", [True, False])
    async def test_runs_when_due(self, forced) -> None:
        connector = _make_connector()
        connector._reconciler.due = AsyncMock(return_value=True)
        connector._reconciler.run = AsyncMock(return_value={})

        await connector._reconcile({"team-1"}, forced=forced)

        connector._reconciler.due.assert_awaited_once_with(forced=forced)
        connector._reconciler.run.assert_awaited_once_with({"team-1"})

    @pytest.mark.asyncio
    async def test_skipped_when_not_due(self) -> None:
        connector = _make_connector()
        connector._reconciler.due = AsyncMock(return_value=False)
        connector._reconciler.run = AsyncMock()

        await connector._reconcile({"team-1"}, forced=False)

        connector._reconciler.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# run_sync around the new steps
# ---------------------------------------------------------------------------


class TestRunStep:
    @pytest.mark.asyncio
    async def test_result_is_passed_through(self) -> None:
        connector = _make_connector()
        step = AsyncMock(return_value=7)
        assert await connector._run_step("reconciliation", step()) == 7
        connector.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_is_reported_not_fatal(self) -> None:
        connector = _make_connector()
        step = AsyncMock(side_effect=LinearTransientError("listing failed", 502))

        assert await connector._run_step("reconciliation", step()) is None

        connector.notify.assert_awaited_once()
        kwargs = connector.notify.await_args.kwargs
        assert "reconciliation" in kwargs["title"]
        assert "listing failed" in kwargs["message"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [
        LinearAuthError("token revoked", 401),
        LinearRateLimited("slow down", 429),
    ])
    async def test_dead_token_or_spent_allowance_stops_the_sync(self, error) -> None:
        connector = _make_connector()
        step = AsyncMock(side_effect=error)

        with pytest.raises(type(error)):
            await connector._run_step("reconciliation", step())

        connector.notify.assert_not_awaited()

    @pytest.mark.parametrize("error,stops", [
        (LinearAuthError("x"), True),
        (LinearRateLimited("x"), True),
        (LinearTransientError("x"), False),
        (LinearNotFound("x"), False),
        (LinearApiError("x"), False),
        (RuntimeError("x"), False),
    ])
    def test_which_errors_stop_the_sync(self, error, stops) -> None:
        assert LinearConnector._stops_the_sync(error) is stops


class TestSyncFailureMessage:
    def test_auth(self) -> None:
        message = LinearConnector._sync_failure_message(LinearAuthError("lin_api_SECRET rejected", 401))
        assert "Reconnect Linear" in message
        assert "SECRET" not in message

    def test_rate_limit(self) -> None:
        message = LinearConnector._sync_failure_message(LinearRateLimited("RATELIMITED", 400))
        assert "rate limit" in message
        assert "next run continues" in message

    def test_anything_else_is_quoted_and_cut_short(self) -> None:
        message = LinearConnector._sync_failure_message(RuntimeError("x" * 500))
        assert "x" * 200 in message
        assert "x" * 201 not in message


class TestRunSync:
    def _prepare(self, connector, full_sync_team_ids=frozenset(), failed=()) -> tuple:
        connector.data_source = MagicMock()
        alice = AppUser(
            app_name=Connectors.LINEAR, connector_id=CONNECTOR_ID, source_user_id="u1",
            org_id="org-1", email="alice@test.com", full_name="Alice", is_active=True)
        guest = AppUser(
            app_name=Connectors.LINEAR, connector_id=CONNECTOR_ID, source_user_id="u2",
            org_id="org-1", email="guest@test.com", full_name="Guest", is_active=True)

        async def fetch_users() -> list:
            connector._full_member_emails = {"alice@test.com"}
            return [alice, guest]

        team_group = AppUserGroup(
            id="ug-1", org_id="org-1", source_user_group_id="team-1",
            connector_id=CONNECTOR_ID, app_name=Connectors.LINEAR, name="Engineering")
        connector._register_authenticated_identity = AsyncMock()
        connector._fetch_users = AsyncMock(side_effect=fetch_users)
        connector._fetch_teams = AsyncMock(return_value=(
            [(team_group, [alice, guest])], [(_team_rg(), [])]))
        connector._remove_stale_teams = AsyncMock()
        connector._sync_issues_for_teams = AsyncMock(return_value=(set(full_sync_team_ids), list(failed)))
        connector._sync_attachments = AsyncMock()
        connector._sync_documents = AsyncMock()
        connector._sync_projects_for_teams = AsyncMock()
        connector._sync_comment_activity = AsyncMock()
        connector._sync_deleted_issues = AsyncMock()
        connector._sync_deleted_projects = AsyncMock()
        connector._reconcile = AsyncMock()
        connector._sweep_placeholder_records = AsyncMock(return_value=0)
        return alice, guest, team_group

    async def _run(self, connector) -> None:
        with patch.object(
            connector_module, "load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_workspace_members_group_is_synced_ahead_of_the_teams(self) -> None:
        connector = _make_connector()
        alice, guest, team_group = self._prepare(connector)

        await self._run(connector)

        (groups,), _ = connector.data_entities_processor.on_new_user_groups.await_args
        (workspace_group, workspace_members), (second_group, team_members) = groups
        assert workspace_group.source_user_group_id == WORKSPACE_MEMBERS_GROUP_ID
        # Full members only: the guest is in the team but not in the workspace group.
        assert workspace_members == [alice]
        assert second_group is team_group
        assert team_members == [alice, guest]

    @pytest.mark.asyncio
    async def test_steps_run_in_order_with_the_synced_teams(self) -> None:
        connector = _make_connector()
        self._prepare(connector)
        order = MagicMock()
        for name in (
            "_remove_stale_teams", "_sync_issues_for_teams", "_sync_attachments", "_sync_documents",
            "_sync_projects_for_teams", "_sync_comment_activity", "_sync_deleted_issues",
            "_sync_deleted_projects", "_reconcile", "_sweep_placeholder_records",
        ):
            order.attach_mock(getattr(connector, name), name)

        await self._run(connector)

        assert [c[0] for c in order.mock_calls] == [
            "_remove_stale_teams", "_sync_issues_for_teams", "_sync_attachments", "_sync_documents",
            "_sync_projects_for_teams", "_sync_comment_activity", "_sync_deleted_issues",
            "_sync_deleted_projects", "_reconcile", "_sweep_placeholder_records",
        ]
        connector._remove_stale_teams.assert_called_once_with({"team-1"})
        connector._sync_comment_activity.assert_called_once_with({"team-1"})
        # Nothing was read from the beginning, so reconciliation is not forced.
        connector._reconcile.assert_called_once_with({"team-1"}, forced=False)
        connector.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_team_read_from_the_beginning_resets_the_workspace_wide_listings(self) -> None:
        connector = _make_connector()
        self._prepare(connector, full_sync_team_ids={"team-1"})
        for sync_point, key in (
            (connector.attachments_sync_point, "attachments"),
            (connector.documents_sync_point, "documents"),
            (connector.projects_sync_point, "projects"),
        ):
            sync_point.store[key] = {"last_sync_time": NOW - 1000, "resume_cursor": "cursor-1",
                                     "resume_since": 1, "resume_started_at": NOW - 500}
        seen = {}

        async def sync_attachments(_teams) -> None:
            seen["attachments"] = dict(connector.attachments_sync_point.store["attachments"])
            seen["documents"] = dict(connector.documents_sync_point.store["documents"])
            seen["projects"] = dict(connector.projects_sync_point.store["projects"])

        connector._sync_attachments = AsyncMock(side_effect=sync_attachments)

        await self._run(connector)

        cleared = {"last_sync_time": 0, "resume_cursor": "", "resume_since": 0,
                   "resume_started_at": 0, "held_runs": 0}
        assert seen == {"attachments": cleared, "documents": cleared, "projects": cleared}
        connector._reconcile.assert_called_once_with({"team-1"}, forced=True)
        connector._sweep_placeholder_records.assert_awaited_once_with(
            synced_team_ids={"team-1"}, full_sync_team_ids={"team-1"})

    @pytest.mark.asyncio
    async def test_incremental_sync_leaves_the_workspace_wide_checkpoints(self) -> None:
        connector = _make_connector()
        self._prepare(connector)
        connector.attachments_sync_point.store["attachments"] = {"last_sync_time": NOW - 1000}

        await self._run(connector)

        assert connector.attachments_sync_point.store["attachments"] == {"last_sync_time": NOW - 1000}
        assert connector.documents_sync_point.store == {}
        assert connector.projects_sync_point.store == {}

    @pytest.mark.asyncio
    async def test_teams_whose_issues_failed_are_reported_and_the_sync_goes_on(self) -> None:
        connector = _make_connector()
        self._prepare(connector, failed=["Engineering"])

        await self._run(connector)

        connector.notify.assert_awaited_once()
        assert "Engineering" in connector.notify.await_args.kwargs["message"]
        connector._sync_attachments.assert_awaited_once()
        connector._reconcile.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_optional_step_does_not_stop_the_sync(self) -> None:
        connector = _make_connector()
        self._prepare(connector)
        connector._remove_stale_teams = AsyncMock(side_effect=LinearTransientError("team lookup failed", 502))
        connector._reconcile = AsyncMock(side_effect=RuntimeError("listing failed"))

        await self._run(connector)

        connector._sync_issues_for_teams.assert_awaited_once()
        connector._sweep_placeholder_records.assert_awaited_once()
        titles = [c.kwargs["title"] for c in connector.notify.await_args_list]
        assert len(titles) == 2
        assert "remove deleted teams" in titles[0]
        assert "moved or deleted records" in titles[1]
        # A full sync that skipped a step must not have the platform unlink what it did not write
        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_clean_run_reports_reading_everything(self) -> None:
        """What an earlier run skipped does not stop this run's full sync from unlinking."""
        connector = _make_connector()
        self._prepare(connector)
        connector._read_everything = False

        await self._run(connector)

        assert connector.full_sync_coverage().complete is True

    @pytest.mark.asyncio
    async def test_rejected_token_stops_the_sync_and_asks_to_reconnect(self) -> None:
        connector = _make_connector()
        self._prepare(connector)
        connector._sync_attachments = AsyncMock(side_effect=LinearAuthError("token revoked", 401))

        with pytest.raises(LinearAuthError):
            await self._run(connector)

        connector._sync_documents.assert_not_awaited()
        connector._reconcile.assert_not_called()
        connector.notify.assert_awaited_once()
        assert "Reconnect Linear" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_rate_limit_in_an_optional_step_stops_the_sync_too(self) -> None:
        connector = _make_connector()
        self._prepare(connector)
        connector._sync_comment_activity = AsyncMock(side_effect=LinearRateLimited("RATELIMITED", 400))

        with pytest.raises(LinearRateLimited):
            await self._run(connector)

        connector._sync_deleted_issues.assert_not_awaited()
        connector.notify.assert_awaited_once()
        assert "rate limit" in connector.notify.await_args.kwargs["message"]


class TestChangeProbe:
    """An incremental run asks cheaply whether a team changed before the costly listing."""

    @pytest.mark.asyncio
    async def test_unchanged_team_is_not_listed_and_its_checkpoint_moves(self) -> None:
        from app.connectors.sources.linear import connector as connector_module

        connector = _make_connector()
        connector.sync_filters = None
        connector.issues_sync_point.store["team_issues_team-1"] = {"last_sync_time": 1700000000000}
        datasource = MagicMock()
        datasource.execute_operation = AsyncMock(return_value=GraphQLResponse(
            success=True, data={"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}))
        datasource.issues = AsyncMock()
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        group = MagicMock(external_group_id="team-1", short_name="ENG")
        group.name = "Engineering"

        full, failed = await connector._sync_issues_for_teams([(group, [])])

        assert (full, failed) == (set(), [])
        datasource.issues.assert_not_awaited()
        operation, variables = datasource.execute_operation.await_args.args[:2]
        assert operation == "issueIds" and variables["first"] == 1
        assert variables["filter"]["team"] == {"id": {"eq": "team-1"}}
        assert "gt" in variables["filter"]["updatedAt"]
        assert connector.issues_sync_point.store["team_issues_team-1"]["last_sync_time"] > 1700000000000
        assert connector_module.ISSUE_PAGE_SIZE == 50

    @pytest.mark.asyncio
    async def test_first_sync_of_a_team_skips_the_probe(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector._team_has_changes = AsyncMock(return_value=False)
        datasource = MagicMock()
        datasource.issues = AsyncMock(return_value=GraphQLResponse(
            success=True, data={"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}))
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        group = MagicMock(external_group_id="team-1", short_name="ENG")
        group.name = "Engineering"

        full, _ = await connector._sync_issues_for_teams([(group, [])])

        assert full == {"team-1"}
        connector._team_has_changes.assert_not_awaited()
        datasource.issues.assert_awaited_once()


class TestAncestorStubs:
    """A stub is filed under the child's team, so it may only name a parent the
    readers of that team could see anyway."""

    @pytest.mark.parametrize("parent_team, expected", [
        ("team-child", True),        # same team as the child
        ("team-public", True),       # another synced, public team
        ("team-private", False),     # another synced, private team
        ("team-restricted", False),  # a restricted team is not open to every member
        ("team-unsynced", False),    # a team this connector does not sync
    ])
    def test_which_parents_may_be_named(self, parent_team, expected) -> None:
        connector = _make_connector()
        connector._team_visibility = {
            "team-child": "private", "team-public": "public", "team-private": "private",
            "team-restricted": "restricted"}
        stub = MagicMock(external_record_group_id="team-child")
        synced = {"team-child", "team-public", "team-private", "team-restricted"}

        assert connector._may_describe_ancestor({"team": {"id": parent_team}}, stub, synced) is expected

    def test_a_stub_that_may_not_name_its_parent_still_carries_its_access(self) -> None:
        """Issues below a stub inherit from it, so it is always built: anchored to the
        child's team and inheriting, but with the identifier only."""
        connector = _make_connector()
        stub = MagicMock(external_record_group_id="team-child", external_record_id="parent-1", id="node-1", version=0)
        issue = {"id": "parent-1", "identifier": "OPS-7", "title": "Layoffs plan", "team": {"id": "team-ops"},
                 "state": {"name": "In Progress"}, "priority": 1,
                 "assignee": {"name": "Dana", "email": "dana@acme.example"}, "url": "https://linear.app/a/issue/OPS-7"}

        record = connector._build_ancestor_stub(issue, stub, describe=False)

        assert record.record_name == "OPS-7"
        assert record.external_record_group_id == "team-child"
        assert record.inherit_permissions is True
        assert record.assignee is None and record.assignee_email is None
        assert "Layoffs" not in record.model_dump_json()

    def test_a_parent_without_a_team_is_not_named(self) -> None:
        connector = _make_connector()
        stub = MagicMock(external_record_group_id="team-child")
        assert connector._may_describe_ancestor({"team": None}, stub, {"team-child"}) is False


class TestChildListingsIgnoreDateFilters:
    """Date filters choose issues and projects. Their attachments and documents come
    with them however old they are; only the checkpoint narrows those listings."""

    def test_checkpoint_only(self) -> None:
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        linear_filter = {}

        connector._apply_checkpoint_to_linear_filter(linear_filter, 1700000000000)

        assert linear_filter == {"updatedAt": {"gt": "2023-11-14T22:13:20.000Z"}}
        connector.sync_filters.get.assert_not_called()

    def test_no_checkpoint_means_no_filter(self) -> None:
        connector = _make_connector()
        linear_filter = {}
        connector._apply_checkpoint_to_linear_filter(linear_filter, None)
        assert linear_filter == {}


class TestProjectChildrenStamp:
    """Links and milestones change without touching the project's updatedAt; their
    fingerprint lives in the project's revision."""

    def _project(self, links, milestones, more=False) -> dict:
        return {
            "id": "p1", "name": "Launch", "updatedAt": "2026-01-01T00:00:00.000Z", "createdAt": "2026-01-01T00:00:00.000Z",
            "externalLinks": {"nodes": links, "pageInfo": {"hasNextPage": more}},
            "projectMilestones": {"nodes": milestones},
        }

    def test_same_children_same_stamp_whatever_the_order(self) -> None:
        connector = _make_connector()
        a = self._project([{"id": "l1", "updatedAt": "t1"}, {"id": "l2", "updatedAt": "t2"}], [])
        b = self._project([{"id": "l2", "updatedAt": "t2"}, {"id": "l1", "updatedAt": "t1"}], [])
        assert connector._project_children_stamp(a) == connector._project_children_stamp(b)

    @pytest.mark.parametrize("links, milestones", [
        ([{"id": "l1", "updatedAt": "t1"}], []),                                   # a link removed
        ([{"id": "l1", "updatedAt": "t1"}, {"id": "l2", "updatedAt": "t9"}], []),  # a link edited
        ([{"id": "l1", "updatedAt": "t1"}, {"id": "l2", "updatedAt": "t2"}], [{"id": "m1", "updatedAt": "t1"}]),
    ])
    def test_a_change_changes_the_stamp(self, links, milestones) -> None:
        connector = _make_connector()
        before = self._project([{"id": "l1", "updatedAt": "t1"}, {"id": "l2", "updatedAt": "t2"}], [])
        after = self._project(links, milestones)
        assert connector._project_children_stamp(before) != connector._project_children_stamp(after)

    def test_an_incomplete_list_gives_no_stamp(self) -> None:
        connector = _make_connector()
        assert connector._project_children_stamp(self._project([{"id": "l1", "updatedAt": "t1"}], [], more=True)) is None

    def test_the_stamp_is_part_of_the_revision(self) -> None:
        connector = _make_connector()
        project = self._project([{"id": "l1", "updatedAt": "t1"}], [])
        record = connector._transform_to_project_record(project, "team-1", None)
        stamp = connector._project_children_stamp(project)
        assert record.external_revision_id.endswith(f":{stamp}")


class TestPartialScans:
    """A page that still fails after the retries (#3991, #3993 on main): what was read is
    kept, the checkpoint does not move, since pages are not ordered by updatedAt and an
    unread page may hold older items, and the failure is reported. The next run resumes
    after the last stored page."""

    REFUSED = GraphQLResponse(success=False, message="Query too complex", status_code=400)

    @staticmethod
    def _stored_issue(connector) -> None:
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: (
                _ticket(external_record_id) if external_record_id.startswith("issue-") else None))

    @pytest.mark.asyncio
    async def test_issues_read_before_a_failed_page_are_kept_and_the_team_is_reported(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.issues = AsyncMock(side_effect=[_ok({"issues": _page([_issue()], True, "cur1")}), self.REFUSED])

        _, failed = await connector._sync_issues_for_teams([(_team_rg(), [])])

        assert failed == ["Engineering"]
        assert connector.full_sync_coverage().complete is False
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        stored = connector.issues_sync_point.store["team_issues_team-1"]
        assert not stored.get("last_sync_time"), "a partly read team moved its checkpoint"
        assert stored["resume_cursor"] == "cur1"

    @pytest.mark.asyncio
    async def test_the_issue_checkpoint_moves_once_every_page_was_read(self) -> None:
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        datasource.issues = AsyncMock(side_effect=[
            _ok({"issues": _page([_issue()], True, "cur1")}),
            _ok({"issues": _page([_issue("issue-2", "ENG-2")])}),
        ])

        _, failed = await connector._sync_issues_for_teams([(_team_rg(), [])])

        assert failed == []
        assert connector.full_sync_coverage().complete is True
        assert connector.data_entities_processor.on_new_records.await_count == 2
        stored = connector.issues_sync_point.store["team_issues_team-1"]
        assert stored["last_sync_time"] == NOW and stored["resume_cursor"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("listing, root, node", [
        ("attachments", "attachments", {"id": "att-1", "title": "PR", "url": "https://github.com/a/b/pull/1",
                                        "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}}}),
        ("documents", "documents", {"id": "doc-1", "title": "Plan", "url": "https://linear.app/a/document/d",
                                    "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}}}),
    ])
    async def test_attachments_and_documents_read_before_a_failed_page_are_kept(self, listing, root, node) -> None:
        connector = _make_connector()
        self._stored_issue(connector)
        datasource = _use_datasource(connector, MagicMock())
        setattr(datasource, listing, AsyncMock(side_effect=[_ok({root: _page([node], True, "cur1")}), self.REFUSED]))

        await getattr(connector, f"_sync_{listing}")([(_team_rg(), [])])

        connector.data_entities_processor.on_new_records.assert_awaited_once()
        stored = getattr(connector, f"{listing}_sync_point").store[listing]
        assert not stored.get("last_sync_time") and stored["resume_cursor"] == "cur1"
        connector.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_trash_scan_keeps_its_checkpoint_when_a_later_page_fails(self) -> None:
        connector = _make_connector()
        connector.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": NOW - 3_600_000}
        connector._remover.remove = AsyncMock(return_value=True)
        datasource = _use_datasource(connector, MagicMock())
        trashed = {"id": "issue-1", "identifier": "ENG-1", "trashed": True,
                   "archivedAt": "2024-06-02T00:00:00.000Z", "team": {"id": "team-1"}}
        datasource.trashed_issues = AsyncMock(side_effect=[_ok({"issues": _page([trashed], True, "cur1")}), self.REFUSED])

        await connector._sync_deleted_issues([(_team_rg(), [])])

        connector._remover.remove.assert_awaited_once_with("issue-1")
        assert connector.deletion_sync_point.store["trashed_issues"]["last_sync_time"] == NOW - 3_600_000
        connector.notify.assert_awaited_once()


class TestUnsyncedParentTeams:
    @pytest.mark.asyncio
    async def test_parents_of_synced_teams_are_kept_and_only_restricted_teams_fetch_theirs(self) -> None:
        """A sub-team's group hangs under its parent's, so the parent is kept even when the
        filter leaves it out. Only a restricted team needs its parent's members, so only
        that parent is read from Linear."""
        connector = _make_connector()
        datasource = _use_datasource(connector, MagicMock())
        web = {"id": "team-web", "name": "Web", "key": "WEB", "visibility": "public",
               "parent": {"id": "team-eng"}, "members": _page([])}
        ops = {"id": "team-ops", "name": "Ops", "key": "OPS", "visibility": "restricted",
               "parent": {"id": "team-sec"}, "members": _page([])}
        sec = {"id": "team-sec", "name": "Security", "key": "SEC", "visibility": "private",
               "parent": None, "members": _page([])}
        datasource.teams = AsyncMock(side_effect=[_ok({"teams": _page([web, ops])}), _ok({"teams": _page([sec])})])

        user_groups, record_groups = await connector._fetch_teams(
            team_ids=["team-web", "team-ops"], team_ids_operator=None, user_email_map={})

        assert [rg.external_group_id for rg, _ in record_groups] == ["team-web", "team-ops"]
        assert {g.source_user_group_id for g, _ in user_groups} == {"team-web", "team-ops", "team-sec"}
        assert datasource.teams.await_count == 2
        assert datasource.teams.await_args.kwargs["filter"] == {"id": {"in": ["team-sec"]}}


class TestConnectionCheck:
    """The setup form tries an API key before saving it (checklist 2.2 and 2.4)."""

    @staticmethod
    def _datasource(viewer, teams=None, organization=None) -> MagicMock:
        datasource = MagicMock()
        datasource.viewer = AsyncMock(return_value=viewer)
        datasource.teams = AsyncMock(return_value=teams or _ok({"teams": _page([{"id": "t1"}])}))
        datasource.organization = AsyncMock(return_value=organization or _ok({"organization": {"name": "Acme"}}))
        return datasource

    @pytest.mark.asyncio
    async def test_a_working_key_names_the_workspace_and_user(self) -> None:
        datasource = self._datasource(_ok({"viewer": {"name": "Alice"}}))
        with patch.object(connector_module, "LinearDataSource", return_value=datasource):
            result = await LinearConnector.check_connection({"authType": "API_TOKEN", "apiToken": "lin_api_x"}, MagicMock())
        assert result.success is True and result.message == "Connected to Acme as Alice."

    @pytest.mark.asyncio
    async def test_a_rejected_key_says_how_to_get_a_new_one(self) -> None:
        rejected = GraphQLResponse(success=False, message="Authentication required", status_code=401)
        with patch.object(connector_module, "LinearDataSource", return_value=self._datasource(rejected)):
            result = await LinearConnector.check_connection({"authType": "API_TOKEN", "apiToken": "lin_api_bad"}, MagicMock())
        assert result.success is False and "did not accept this API key" in result.message

    @pytest.mark.asyncio
    async def test_a_key_that_cannot_read_teams_is_refused(self) -> None:
        forbidden = GraphQLResponse(success=False, message="Forbidden", status_code=403)
        datasource = self._datasource(_ok({"viewer": {"name": "Alice"}}), teams=forbidden)
        with patch.object(connector_module, "LinearDataSource", return_value=datasource):
            result = await LinearConnector.check_connection({"authType": "API_TOKEN", "apiToken": "lin_api_x"}, MagicMock())
        assert result.success is False and "cannot read teams" in result.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token", ["", "   ", None])
    async def test_an_empty_key_asks_for_one_without_calling_linear(self, token) -> None:
        with patch.object(connector_module, "LinearDataSource") as datasource:
            result = await LinearConnector.check_connection({"authType": "API_TOKEN", "apiToken": token}, MagicMock())
        assert result.success is False and result.message == "Enter a Linear API key."
        datasource.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_is_left_to_linear(self) -> None:
        result = await LinearConnector.check_connection({"authType": "OAUTH"}, MagicMock())
        assert result.success is True

    def test_the_setup_form_offers_the_check(self) -> None:
        assert LinearConnector.supports_connection_check() is True
