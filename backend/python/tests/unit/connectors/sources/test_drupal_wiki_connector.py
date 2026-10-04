"""Tests for the Drupal Wiki connector.

The data source and processor are mocked, so these cover the sync rules themselves:
checkpoints, permissions, deletions, filters and streaming.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager, ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.drupal_wiki.connector import (
    DrupalWikiConnector,
    DrupalWikiSyncError,
)
from app.models.entities import Record, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.external.drupal_wiki.graphql import DrupalWikiGraphQLError

BASE_URL = "https://wiki.example.com"


def response(payload: object = None, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.json.return_value = payload
    return resp


def spring_page(content: list[dict], last: bool = True) -> dict:
    return {"content": content, "last": last}


def page_payload(page_id: int, space_id: int = 12, last_modified: int = 1000, page_type: str = "DOCUMENT") -> dict:
    return {
        "id": page_id,
        "title": f"Page {page_id}",
        "type": page_type,
        "homeSpace": space_id,
        "lastModified": last_modified,
    }


def stored_record(
    page_id: int,
    space_id: int = 12,
    internal_id: str | None = None,
    parent: str | None = None,
) -> Record:
    record = MagicMock(spec=Record)
    record.id = internal_id or f"internal-{page_id}"
    record.external_record_id = f"page:{page_id}"
    record.external_record_group_id = str(space_id)
    record.external_revision_id = "1000"
    record.parent_external_record_id = parent
    record.is_placeholder = False
    record.record_type = RecordType.WEBPAGE
    record.record_name = f"Page {page_id}"
    record.mime_type = "text/html"
    return record


def attachment_record(attachment_id: int, page_id: int = 733) -> Record:
    record = MagicMock(spec=Record)
    record.id = f"internal-att-{attachment_id}"
    record.external_record_id = f"attachment:{attachment_id}"
    record.parent_external_record_id = f"page:{page_id}"
    record.external_record_group_id = "12"
    record.record_type = RecordType.FILE
    record.record_name = f"attachment-{attachment_id}.pdf"
    return record


def read_permission(email: str) -> Permission:
    return Permission(
        external_id=None, email=email, type=PermissionType.READ, entity_type=EntityType.USER
    )


@pytest.fixture()
def processor() -> MagicMock:
    proc = MagicMock()
    proc.org_id = "org-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.on_records_deleted_cascade = AsyncMock()
    proc.on_record_group_deleted = AsyncMock()
    proc.on_user_group_deleted = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_records_in_record_group = AsyncMock(return_value=[])
    return proc


@pytest.fixture()
def connector(processor: MagicMock) -> DrupalWikiConnector:
    instance = DrupalWikiConnector(
        logging.getLogger("test.drupal_wiki"),
        processor,
        MagicMock(),
        MagicMock(),
        "conn-1",
        "team",
        "creator-1",
    )
    instance.base_url = BASE_URL
    instance.data_source = MagicMock()
    instance.sync_filters = FilterCollection()
    instance.indexing_filters = FilterCollection()
    instance.record_sync_point.read_sync_point = AsyncMock(return_value={})
    instance.record_sync_point.update_sync_point = AsyncMock()
    # init() always builds the GraphQL data source; a space whose tree cannot be read
    # is the failure a test opts into, not a missing client.
    instance.graphql = MagicMock()
    instance.graphql.get_space_tree = AsyncMock(side_effect=DrupalWikiGraphQLError("no tree"))
    instance.graphql.get_space_members = AsyncMock(side_effect=DrupalWikiGraphQLError("no members"))
    return instance


class TestInit:
    @pytest.mark.asyncio
    async def test_bad_config_raises_user_facing_error(self, connector: DrupalWikiConnector) -> None:
        with patch(
            "app.connectors.sources.drupal_wiki.connector.DrupalWikiClient.build_from_services",
            AsyncMock(side_effect=ValueError("Base URL is required")),
        ):
            with pytest.raises(ConnectorInitError, match="Base URL is required"):
                await connector.init()

    @pytest.mark.asyncio
    async def test_init_builds_both_data_sources(
        self, connector: DrupalWikiConnector
    ) -> None:
        with self._patched_init(MagicMock()):
            assert await connector.init() is True

        assert connector.data_source is not None
        assert connector.graphql is not None

    @staticmethod
    def _patched_init(graphql_client: MagicMock) -> AbstractContextManager[MagicMock]:
        """Patch the client, data source and GraphQL client used by ``init``."""

        client = MagicMock()
        client.get_base_url.return_value = BASE_URL
        client.get_client.return_value = MagicMock()
        data_source = MagicMock()
        data_source.list_users = AsyncMock(return_value=response({}))

        stack = ExitStack()
        stack.enter_context(patch(
            "app.connectors.sources.drupal_wiki.connector.DrupalWikiClient.build_from_services",
            AsyncMock(return_value=client),
        ))
        stack.enter_context(patch(
            "app.connectors.sources.drupal_wiki.connector.DrupalWikiDataSource",
            MagicMock(return_value=data_source),
        ))
        stack.enter_context(patch(
            "app.connectors.sources.drupal_wiki.connector.DrupalWikiGraphQLClient.build_from_rest_client",
            MagicMock(return_value=graphql_client),
        ))

        class _Patched:
            def __enter__(self) -> MagicMock:
                return data_source

            def __exit__(self, *exc: object) -> None:
                stack.close()

        return _Patched()


class TestConnectionTest:
    @pytest.mark.asyncio
    async def test_unauthorized_without_retry_raises(self, connector: DrupalWikiConnector) -> None:
        connector.client = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=response(status=401))

        with pytest.raises(ConnectorInitError, match="rejected the personal access token"):
            await connector.test_connection_and_access()

    @pytest.mark.asyncio
    async def test_forbidden_reports_missing_access(self, connector: DrupalWikiConnector) -> None:
        connector.client = MagicMock()
        connector.client.get_client.return_value = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=response(status=403))

        with pytest.raises(ConnectorInitError, match="no access to users"):
            await connector.test_connection_and_access()


class TestUsersAndGroups:
    @pytest.mark.asyncio
    async def test_users_are_paged_and_email_less_users_skipped(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_users = AsyncMock(side_effect=[
            response(spring_page([
                {"id": 1, "email": "a@example.com", "userName": "a", "status": "ENABLED"},
                {"id": 2, "email": "", "userName": "ghost"},
            ], last=False)),
            response(spring_page([{"id": 3, "email": "c@example.com", "userName": "c", "status": "ENABLED"}])),
        ])

        users_by_id = await connector._sync_users()

        assert set(users_by_id) == {"1", "3"}
        # Disabled accounts are excluded by the wiki, not by us.
        assert connector.data_source.list_users.await_args.kwargs["only_active"] is True
        # Each page is written as it arrives rather than accumulated into one call.
        assert [
            [user.email for user in call.args[0]]
            for call in processor.on_new_app_users.await_args_list
        ] == [["a@example.com"], ["c@example.com"]]

    @pytest.mark.asyncio
    async def test_user_without_an_id_is_skipped(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Group membership resolves users by id, so an id-less user would land under
        # "None" and be handed out as a member of any group naming an id-less user.
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"email": "ghost@example.com", "userName": "ghost", "status": "ENABLED"},
            {"id": 1, "email": "a@example.com", "userName": "a", "status": "ENABLED"},
        ])))

        users_by_id = await connector._sync_users()

        assert set(users_by_id) == {"1"}
        assert [u.email for u in processor.on_new_app_users.await_args.args[0]] == ["a@example.com"]

    @pytest.mark.asyncio
    async def test_group_members_exclude_disabled_users(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        active = MagicMock(is_active=True, email="a@example.com")
        disabled = MagicMock(is_active=False, email="c@example.com")
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([{"id": 5, "name": "Dev"}])))
        connector.data_source.get_group = AsyncMock(return_value=response({
            "id": 5, "members": [{"id": 1}, {"id": 3}, {"id": 99}],
        }))

        await connector._sync_groups({"1": active, "3": disabled})

        group, members = processor.on_new_user_groups.await_args.args[0][0]
        assert group.source_user_group_id == "5"
        assert members == [active]

    @pytest.mark.asyncio
    async def test_failed_group_fetch_keeps_existing_membership(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([{"id": 5, "name": "Dev"}])))
        connector.data_source.get_group = AsyncMock(return_value=response(status=500))

        await connector._sync_groups({})

        # Sending an empty member list would wipe the group's edges.
        processor.on_new_user_groups.assert_not_awaited()
        assert connector._failures

    @pytest.mark.asyncio
    async def test_user_fields_are_mapped_from_the_payload(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"id": 42, "email": "alice@example.com", "firstName": "Alice",
             "lastName": "Doe", "userName": "alice", "status": "ENABLED"},
            # No names and no status: falls back to the username, and an account the
            # wiki does not report as enabled is inactive.
            {"id": 7, "email": "bob@example.com", "userName": "bob"},
        ])))

        await connector._sync_users()

        alice, bob = processor.on_new_app_users.await_args.args[0]
        assert (alice.source_user_id, alice.full_name, alice.is_active) == ("42", "Alice Doe", True)
        assert (bob.source_user_id, bob.full_name, bob.is_active) == ("7", "bob", False)
        assert alice.app_name == Connectors.DRUPAL_WIKI

    @pytest.mark.asyncio
    async def test_group_records_where_it_came_from(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([
            {"id": 5, "name": "Dev-Team", "groupSource": "LDAP"},
        ])))
        connector.data_source.get_group = AsyncMock(return_value=response({"id": 5, "members": []}))

        await connector._sync_groups({})

        group, _ = processor.on_new_user_groups.await_args.args[0][0]
        assert group.source_user_group_id == "5"
        assert group.name == "Dev-Team"
        assert group.description == "source=LDAP"


class TestSpaces:
    @pytest.mark.asyncio
    async def test_open_space_gets_org_permission(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 3, "name": "Blog", "type": "CORPORATEBLOG", "accessStatus": "AUTHENTICATED"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {3}
        group, permissions = processor.on_new_record_groups.await_args.args[0][0]
        assert group.external_group_id == "3"
        assert group.group_type == RecordGroupType.DRUPAL_WIKI_SPACE
        # A space never inherits from another, so it carries its own audience.
        assert group.inherit_permissions is False
        # Access lives on the record group; pages inherit it and carry none of their own.
        assert permissions[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_private_space_uses_graphql_members(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.graphql = MagicMock()
        connector.graphql.get_space_members = AsyncMock(return_value={
            "access_status": "PRIVATE",
            "users": [{"email": "alice@example.com", "roles": [{"machine": "space_read"}]}],
            "groups": [],
        })
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 12, "name": "HR", "type": "BASIC", "accessStatus": "PRIVATE"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {12}
        _, permissions = processor.on_new_record_groups.await_args.args[0][0]
        assert permissions[0].email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_member_fetch_failure_leaves_space_untouched(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.graphql = MagicMock()
        connector.graphql.get_space_members = AsyncMock(side_effect=DrupalWikiGraphQLError("denied"))
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 12, "name": "HR", "type": "BASIC", "accessStatus": "PRIVATE"},
        ])))

        spaces = await connector._sync_spaces()

        assert spaces == {}
        processor.on_new_record_groups.assert_not_awaited()
        assert connector._failures

    @pytest.mark.asyncio
    async def test_space_with_an_unusable_id_is_skipped(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # A bare int() here used to raise out of the phase and fail the whole run.
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": "not-a-number", "name": "Broken", "type": "BASIC", "accessStatus": "ANONYMOUS"},
            {"id": 3, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {3}
        assert not connector._failures

    @pytest.mark.asyncio
    async def test_space_with_no_audience_is_still_created_without_grants(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The group has to exist so its pages have somewhere to belong. Granting
        # someone the read right later then only adds an edge, instead of needing a
        # full resync to discover a space that was never created.
        connector.graphql.get_space_members = AsyncMock(return_value={"users": [], "groups": []})
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 7, "name": "Nobody's space", "type": "BASIC", "accessStatus": "PRIVATE"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {7}
        group, permissions = processor.on_new_record_groups.await_args.args[0][0]
        assert group.external_group_id == "7"
        assert permissions == []

    @pytest.mark.asyncio
    async def test_a_space_whose_members_cannot_be_read_is_still_skipped(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Unknown audience is not the same as no audience: writing it with no grants
        # would strip whatever access it already had, so it is left untouched.
        connector.graphql.get_space_members = AsyncMock(
            side_effect=DrupalWikiGraphQLError("graphql 502")
        )
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 7, "name": "Private", "type": "BASIC", "accessStatus": "PRIVATE"},
        ])))

        spaces = await connector._sync_spaces()

        assert spaces == {}
        processor.on_new_record_groups.assert_not_awaited()
        assert connector._failures

    @pytest.mark.asyncio
    async def test_every_space_type_is_synced(self, connector: DrupalWikiConnector) -> None:
        # No type is filtered out: which spaces are worth syncing is the user's call,
        # made through the space filter, exactly as the Confluence connector does it.
        connector.graphql.get_space_members = AsyncMock(return_value={
            "access_status": "PRIVATE",
            "users": [{"email": "a@example.com", "roles": [{"machine": "space_read"}]}],
            "groups": [],
        })
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 1, "name": "Workspace", "type": "USERSPACE", "accessStatus": "PRIVATE"},
            {"id": 2, "name": "Trash", "type": "TRASH", "accessStatus": "AUTHENTICATED"},
            {"id": 3, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_space_filter_include_and_exclude(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 3, "name": "A", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
            {"id": 4, "name": "B", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
        ])))
        connector._get_space_id_filters = MagicMock(return_value=({"4"}, set()))

        spaces = await connector._sync_spaces()
        assert set(spaces) == {4}

        connector._get_space_id_filters = MagicMock(return_value=(set(), {"4"}))
        spaces = await connector._sync_spaces()
        assert set(spaces) == {3}


class TestChangedPages:
    @pytest.mark.asyncio
    async def test_pages_and_attachments_are_batched_together(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([page_payload(733)])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 88, "fileName": "a.pdf", "lastModified": 1001},
        ]))

        await connector._sync_pages({12: {"id": 12}})

        batch = processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in batch] == ["page:733", "attachment:88"]
        # Access comes from the space, so records carry no permissions of their own.
        assert all(permissions == [] for _, permissions in batch)

    @pytest.mark.asyncio
    async def test_pages_outside_synced_spaces_are_skipped_but_every_type_syncs(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The home space is the only thing that excludes a page. No page type is
        # filtered out: which content is worth indexing is the user's call.
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            page_payload(1, space_id=99),
            page_payload(2, page_type="DASHBOARD"),
        ])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}})

        batch = processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in batch] == ["page:2"]

    @pytest.mark.asyncio
    async def test_indexing_filters_mark_records_auto_index_off(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([page_payload(733)])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}})

        record, _ = processor.on_new_records.await_args.args[0][0]
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_failed_listing_reports_unclean_run(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(status=500))

        await connector._sync_pages({12: {"id": 12}})

        assert connector._failures
        # The space keeps its checkpoint, so the next run lists the same window again.
        connector.record_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_space_keeps_its_checkpoint_while_others_advance(
        self, connector: DrupalWikiConnector
    ) -> None:
        # Space 12 lists cleanly, space 13 does not. Only 12 may move forward.
        def listing(space_id: int | None = None, **_: object) -> MagicMock:
            if space_id == 13:
                return response(status=500)
            return response(spring_page([page_payload(1, space_id=12)]))

        connector.data_source.list_pages = AsyncMock(side_effect=listing)
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        written = [call.args[0] for call in connector.record_sync_point.update_sync_point.await_args_list]
        assert written == ["WEBPAGE/space/12"]
        assert connector._failures

    @pytest.mark.asyncio
    async def test_checkpoint_is_passed_through_as_modified_after(
        self, connector: DrupalWikiConnector
    ) -> None:
        # The cursor is the wiki's own lastModified, so it is sent verbatim: both
        # sides of the comparison come from the same clock.
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_modified": 10_000}
        )
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([])))

        await connector._sync_pages({12: {"id": 12}})

        assert connector.data_source.list_pages.await_args.kwargs["modified_after"] == 10_000


class TestDependentNodes:
    @pytest.mark.asyncio
    async def test_attachment_names_its_parent_page_node(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Search annotates a dependent hit with its parent's metadata, so the
        # attachment has to carry the page's internal id, not its own.
        stored_page = stored_record(733, internal_id="internal-733")
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(733)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 88, "fileName": "a.pdf", "lastModified": 1001},
        ]))

        processor.get_record_by_external_id = AsyncMock(return_value=stored_page)

        await connector._sync_pages({12: {"id": 12}})

        page_record, attachment = (r for r, _ in processor.on_new_records.await_args.args[0])
        assert page_record.is_dependent_node is False
        assert attachment.is_dependent_node is True
        assert attachment.parent_node_id == "internal-733"

    @pytest.mark.asyncio
    async def test_a_brand_new_page_lends_its_own_id(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(733)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 88, "fileName": "a.pdf", "lastModified": 1001},
        ]))

        await connector._sync_pages({12: {"id": 12}})

        page_record, attachment = (r for r, _ in processor.on_new_records.await_args.args[0])
        assert attachment.parent_node_id == page_record.id

class TestNoDeletion:
    """This connector syncs new, changed and moved pages; it never removes anything."""

    @pytest.mark.asyncio
    async def test_page_moved_between_synced_spaces_is_resaved_under_the_new_space(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The external id is stable, so a move is an update: the record follows the
        # page to space 13 instead of being deleted from 12 and recreated. That relies
        # on the move bumping lastModified, which the API does not document.
        moved = page_payload(733, space_id=13)
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([moved])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        record, _ = processor.on_new_records.await_args.args[0][0]
        assert record.external_record_id == "page:733"
        assert record.external_record_group_id == "13"
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reindex_keeps_a_record_the_source_no_longer_returns(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.get_page = AsyncMock(return_value=response(status=404))

        await connector.reindex_records([stored_record(733)])

        processor.on_records_deleted_cascade.assert_not_awaited()
        processor.on_new_records.assert_not_awaited()

class TestMalformedPayloads:
    @pytest.mark.asyncio
    async def test_page_without_an_id_is_skipped_in_the_delta(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # list_attachments drops null query params, so an id-less page would pull
        # every attachment in the wiki onto a "page:None" record.
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            {"title": "No id", "type": "DOCUMENT", "homeSpace": 12, "lastModified": 1000},
            page_payload(733),
        ])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}})

        batch = processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in batch] == ["page:733"]

class TestParentPreservation:
    @pytest.mark.asyncio
    async def test_unknown_tree_keeps_the_stored_parent(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Re-saving a page with no parent deletes its PARENT_CHILD edge, so when the
        # tree cannot be read the stored parent has to be carried over.
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([page_payload(733)])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        processor.get_record_by_external_id = AsyncMock(
            return_value=stored_record(733, parent="page:700")
        )

        await connector._sync_pages({12: {"id": 12}})

        record, _ = processor.on_new_records.await_args.args[0][0]
        assert record.parent_external_record_id == "page:700"
        assert record.parent_record_type == RecordType.WEBPAGE

    @pytest.mark.asyncio
    async def test_known_tree_overrides_the_stored_parent(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.graphql = MagicMock()
        connector.graphql.get_space_tree = AsyncMock(return_value=[
            {"id": "n1", "pageId": 800, "parentId": None},
            {"id": "n2", "pageId": 733, "parentId": "n1"},
        ])
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([page_payload(733)])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        processor.get_record_by_external_id = AsyncMock(
            return_value=stored_record(733, parent="page:700")
        )

        await connector._sync_pages({12: {"id": 12}})

        record, _ = processor.on_new_records.await_args.args[0][0]
        assert record.parent_external_record_id == "page:800"

class TestGroupBatching:
    @pytest.mark.asyncio
    async def test_groups_are_sent_in_one_call(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # One transaction for all groups, the way the Jira connector does it.
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([
            {"id": 5, "name": "Dev"}, {"id": 6, "name": "Ops"},
        ])))
        connector.data_source.get_group = AsyncMock(side_effect=[
            response({"id": 5, "members": []}), response({"id": 6, "members": []}),
        ])

        await connector._sync_groups({})

        processor.on_new_user_groups.assert_awaited_once()
        sent = processor.on_new_user_groups.await_args.args[0]
        assert [group.source_user_group_id for group, _ in sent] == ["5", "6"]

    @pytest.mark.asyncio
    async def test_a_failed_group_is_skipped_not_wiped(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([
            {"id": 5, "name": "Dev"}, {"id": 6, "name": "Ops"},
        ])))
        connector.data_source.get_group = AsyncMock(side_effect=[
            response(status=500), response({"id": 6, "members": []}),
        ])

        await connector._sync_groups({})

        sent = processor.on_new_user_groups.await_args.args[0]
        # Group 5 keeps the membership it already had rather than being emptied.
        assert [group.source_user_group_id for group, _ in sent] == ["6"]
        assert connector._failures


class TestStreamAndReindex:
    @pytest.mark.asyncio
    async def test_stream_page_returns_html(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.get_page = AsyncMock(return_value=response({
            "id": 733, "title": "Runbook", "body": "<p>Body</p>", "tags": [], "categories": [],
        }))
        record = stored_record(733)

        result = await connector.stream_record(record)

        assert result.media_type == "text/html"

    @pytest.mark.asyncio
    async def test_unsupported_record_type_is_rejected(self, connector: DrupalWikiConnector) -> None:
        # Only WEBPAGE and FILE are ever created; anything else is a caller error.
        record = MagicMock(spec=Record)
        record.record_type = RecordType.MAIL
        record.external_record_id = "page:1"

        with pytest.raises(HTTPException) as excinfo:
            await connector.stream_record(record)

        assert excinfo.value.status_code == 400
        assert "Unsupported record type" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_stream_missing_page_maps_to_not_found(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.get_page = AsyncMock(return_value=response(status=404))
        with pytest.raises(Exception) as excinfo:
            await connector.stream_record(stored_record(733))
        assert getattr(excinfo.value, "status_code", None) == 404

    @pytest.mark.asyncio
    async def test_stream_attachment_downloads_file(self, connector: DrupalWikiConnector) -> None:
        async def chunks(*args: object, **kwargs: object) -> AsyncIterator[bytes]:
            yield b"data"

        connector.data_source.download_attachment = chunks
        connector.data_source.get_attachment = AsyncMock(return_value=response({"id": 88}))
        record = MagicMock(spec=Record)
        record.record_type = RecordType.FILE
        record.external_record_id = "attachment:88"
        record.record_name = "a.pdf"
        record.mime_type = "application/pdf"

        result = await connector.stream_record(record)

        assert result.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_reindex_updates_changed_and_keeps_unchanged(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        changed = stored_record(1)
        unchanged = stored_record(2)
        connector.data_source.get_page = AsyncMock(side_effect=[
            response({"id": 1, "lastModified": 2000}),
            response({"id": 2, "lastModified": 1000}),
        ])

        await connector.reindex_records([changed, unchanged])

        batch = processor.on_new_records.await_args.args[0]
        assert len(batch) == 1
        assert processor.reindex_existing_records.await_args.args[0] == [unchanged]


class TestReindexEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_revision_does_not_trigger_an_update(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # str(None) would never match the stored revision, re-indexing for ever.
        connector.data_source.get_page = AsyncMock(return_value=response({"id": 1}))

        await connector.reindex_records([stored_record(1)])

        processor.on_new_records.assert_not_awaited()
        processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_changed_attachment_keeps_its_attachment_edge(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        record = attachment_record(88)
        record.external_revision_id = "1000"
        record.parent_record_type = None
        connector.data_source.get_attachment = AsyncMock(
            return_value=response({"id": 88, "lastModified": 2000})
        )

        await connector.reindex_records([record])

        updated = processor.on_new_records.await_args.args[0][0][0]
        # Without this the processor would write PARENT_CHILD instead of ATTACHMENT.
        assert updated.parent_record_type == RecordType.WEBPAGE


class TestFilterOptions:
    @pytest.mark.asyncio
    async def test_space_options_are_paginated(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_spaces = AsyncMock(return_value=response({
            "content": [
                {"id": 3, "name": "Team", "type": "BASIC"},
                {"id": 4, "name": "Trash", "type": "TRASH"},
            ],
            "last": False,
        }))

        result = await connector.get_filter_options("space_ids", page=2, limit=10)

        connector.data_source.list_spaces.assert_awaited_once_with(search=None, page=1, size=10)
        assert result.success is True
        # Every space is offered; the user decides which ones to sync.
        assert [option.id for option in result.options] == ["3", "4"]
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_unknown_filter_key_is_rejected(self, connector: DrupalWikiConnector) -> None:
        result = await connector.get_filter_options("unknown_key")
        assert result.success is False


class TestHelpers:
    @pytest.mark.asyncio
    async def test_page_listing_walks_until_last(self, connector: DrupalWikiConnector) -> None:
        # GET /page answers with a Spring envelope, so ``last`` ends the walk.
        connector.data_source.list_pages = AsyncMock(side_effect=[
            response(spring_page([page_payload(1)], last=False)),
            response(spring_page([page_payload(2)], last=True)),
        ])

        pages = [page async for page in connector._fetch_pages(12, None)]

        assert [page["id"] for page in pages] == [1, 2]
        # The space is filtered by the server, not by us.
        assert connector.data_source.list_pages.await_args_list[0].kwargs["space_id"] == 12
        assert connector.data_source.list_pages.await_args_list[1].kwargs["page"] == 1

    @pytest.mark.asyncio
    async def test_page_listing_stops_on_an_empty_page(self, connector: DrupalWikiConnector) -> None:
        # An empty page ends the walk even when the envelope claims more follow.
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([], last=False))
        )

        assert [page async for page in connector._fetch_pages(12, None)] == []
        assert connector.data_source.list_pages.await_count == 1

    @pytest.mark.asyncio
    async def test_page_listing_raises_on_error_status(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(status=500))

        with pytest.raises(DrupalWikiSyncError, match=r"error \(500\)"):
            [page async for page in connector._fetch_pages(12, None)]

    @pytest.mark.asyncio
    async def test_page_shared_into_a_space_is_left_to_its_home_space(
        self, connector: DrupalWikiConnector
    ) -> None:
        # ``space`` returns pages shared into it as well; only space 12's own pages
        # may be written here, so that each record has exactly one record group.
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            page_payload(1, space_id=99),
            page_payload(2, space_id=12),
            {"id": 3, "title": "No home", "lastModified": 1000},
        ])))

        pages = [page async for page in connector._fetch_pages(12, None)]

        assert [page["id"] for page in pages] == [2]

    @pytest.mark.asyncio
    async def test_attachment_listing_walks_until_a_short_array(
        self, connector: DrupalWikiConnector
    ) -> None:
        # GET /attachment answers with a bare array instead of an envelope, so only a
        # page shorter than the one requested says the walk is over.
        full = [{"id": i, "fileName": f"{i}.pdf", "lastModified": 1001} for i in range(100)]
        connector.data_source.list_attachments = AsyncMock(side_effect=[
            response(full),
            response([{"id": 100, "fileName": "100.pdf", "lastModified": 1001}]),
        ])

        records = await connector._build_page_records(page_payload(733), {})

        assert len(records) == 102  # the page itself plus 101 attachments
        assert connector.data_source.list_attachments.await_args_list[1].kwargs["page"] == 1

    def test_space_id_filters_default_to_everything(self, connector: DrupalWikiConnector) -> None:
        assert connector._get_space_id_filters() == (set(), set())

    @pytest.mark.asyncio
    async def test_space_tree_is_unknown_without_graphql(self, connector: DrupalWikiConnector) -> None:
        # None, not {}: an empty dict would mean "this space has no parents" and
        # would delete the stored PARENT_CHILD edges.
        assert await connector._fetch_space_tree(12, "Engineering") is None

    @pytest.mark.asyncio
    async def test_unrecognised_tree_shape_is_unknown_not_flat(
        self, connector: DrupalWikiConnector
    ) -> None:
        # Nodes came back but none carried a page id, so the internal API has changed
        # shape. Reporting that as a flat space would re-save every page with no parent
        # and delete the layout already stored.
        connector.graphql.get_space_tree = AsyncMock(return_value=[
            {"nodeId": "n1", "page": 101},
            {"nodeId": "n2", "page": 102, "parent": "n1"},
        ])

        assert await connector._fetch_space_tree(12, "Engineering") is None

    @pytest.mark.asyncio
    async def test_a_genuinely_flat_space_is_empty_not_unknown(
        self, connector: DrupalWikiConnector
    ) -> None:
        # Every node carries a page id and none has a parent: that really is flat.
        connector.graphql.get_space_tree = AsyncMock(return_value=[
            {"id": "n1", "pageId": 101, "parentId": None},
            {"id": "n2", "pageId": 102, "parentId": None},
        ])

        assert await connector._fetch_space_tree(12, "Engineering") == {}

    @pytest.mark.asyncio
    async def test_space_tree_is_unknown_after_a_graphql_failure(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.graphql = MagicMock()
        connector.graphql.get_space_tree = AsyncMock(side_effect=DrupalWikiGraphQLError("nope"))
        assert await connector._fetch_space_tree(12, "Engineering") is None

    @pytest.mark.asyncio
    async def test_known_tree_sets_the_parent(self, connector: DrupalWikiConnector) -> None:
        connector.graphql = MagicMock()
        connector.graphql.get_space_tree = AsyncMock(return_value=[
            {"id": "n1", "pageId": 700, "parentId": None},
            {"id": "n2", "pageId": 733, "parentId": "n1"},
        ])
        assert await connector._fetch_space_tree(12, "Engineering") == {733: 700}

class TestRunSync:
    @pytest.mark.asyncio
    async def test_clean_run_advances_that_space_checkpoint(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector._sync_users = AsyncMock(return_value={})
        connector._sync_groups = AsyncMock()
        connector._sync_spaces = AsyncMock(return_value={12: {"id": 12}})
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(1, last_modified=1000)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        with patch(
            "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
            AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

        connector.record_sync_point.update_sync_point.assert_awaited_once()
        key, payload = connector.record_sync_point.update_sync_point.await_args.args
        assert key == "WEBPAGE/space/12"
        # One second back, because the source filter is a strict ``>``.
        assert payload == {"last_modified": 999}

    @pytest.mark.asyncio
    async def test_partial_failure_notifies_but_completes_the_run(
        self, connector: DrupalWikiConnector
    ) -> None:
        # One bad group must not bury everything else that synced fine, so the run
        # reports the failure and still finishes.
        connector.notify = AsyncMock()
        connector._sync_users = AsyncMock(return_value={})
        connector._sync_groups = AsyncMock(side_effect=lambda users: connector._fail("group 5 failed"))
        connector._sync_spaces = AsyncMock(return_value={})
        connector._sync_pages = AsyncMock(return_value=(True, 1000))

        with patch(
            "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
            AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

        assert "group 5 failed" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_run_sync_without_a_data_source_raises(self, connector: DrupalWikiConnector) -> None:
        connector.data_source = None
        with pytest.raises(DrupalWikiSyncError, match="not initialized"):
            await connector.run_sync()


class TestMisc:
    @pytest.mark.asyncio
    async def test_signed_url_is_not_supported(self, connector: DrupalWikiConnector) -> None:
        assert await connector.get_signed_url(stored_record(1)) is None

    @pytest.mark.asyncio
    async def test_incremental_sync_delegates_to_run_sync(self, connector: DrupalWikiConnector) -> None:
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_closes_clients(self, connector: DrupalWikiConnector) -> None:
        http_client = MagicMock()
        http_client.close = AsyncMock()
        connector.client = MagicMock()
        connector.client.get_client.return_value = http_client
        connector.graphql_client = MagicMock()
        connector.graphql_client.close = AsyncMock()

        await connector.cleanup()

        http_client.close.assert_awaited_once()
        connector.graphql_client.close.assert_awaited_once()
        assert connector.data_source is None

    def test_connector_name(self, connector: DrupalWikiConnector) -> None:
        assert connector.connector_name == Connectors.DRUPAL_WIKI


class TestOrderingAndIsolation:
    @pytest.mark.asyncio
    async def test_records_are_written_in_source_order(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # No reordering: a child reaching the processor before its parent makes it
        # mint a placeholder, which the real page then promotes. Sorting the batch
        # would only save that round trip, not prevent anything.
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            page_payload(2),
            page_payload(1),
        ])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))
        connector.graphql.get_space_tree = AsyncMock(return_value=[
            {"id": "n1", "pageId": 1, "parentId": None},
            {"id": "n2", "pageId": 2, "parentId": "n1"},
        ])

        await connector._sync_pages({12: {"id": 12}})

        batch = processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in batch] == ["page:2", "page:1"]
        # The child still carries the pointer the placeholder is created from.
        assert batch[0][0].parent_external_record_id == "page:1"

    @pytest.mark.asyncio
    async def test_one_unbuildable_page_does_not_stop_the_others(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            page_payload(1),
            page_payload(2),
        ])))
        connector.data_source.list_attachments = AsyncMock(side_effect=[
            RuntimeError("boom"),
            response([]),
        ])

        # The good page still syncs, but this space's checkpoint stays put so the next
        # run retries the broken one. Nothing else would.
        await connector._sync_pages({12: {"id": 12}})
        assert connector._failures
        connector.record_sync_point.update_sync_point.assert_not_awaited()

        batch = processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in batch] == ["page:2"]

    @pytest.mark.asyncio
    async def test_unreadable_checkpoint_falls_back_to_a_full_window(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.record_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("db down"))
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([])))

        await connector._sync_pages({12: {"id": 12}})
        assert connector.data_source.list_pages.await_args.kwargs["modified_after"] is None


class TestPerSpaceCheckpoints:
    @pytest.mark.asyncio
    async def test_each_space_is_listed_from_its_own_cursor(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: {"WEBPAGE/space/12": {"last_modified": 500}}.get(key, {})
        )
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([])))

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        sent = {
            call.kwargs["space_id"]: call.kwargs["modified_after"]
            for call in connector.data_source.list_pages.await_args_list
        }
        # Space 13 has never synced, so it is listed in full rather than from 12's cursor.
        assert sent == {12: 500, 13: None}

    @pytest.mark.asyncio
    async def test_a_space_the_permission_phase_dropped_is_listed_in_full_later(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Run 1 drops space 13, so it stores no cursor. Run 2 must still find its old
        # pages, which a single shared cursor would have advanced past.
        checkpoints: dict[str, dict] = {}
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: dict(checkpoints.get(key, {}))
        )
        connector.record_sync_point.update_sync_point = AsyncMock(
            side_effect=lambda key, payload: checkpoints.__setitem__(key, dict(payload))
        )

        def listing(space_id: int | None = None, modified_after: int | None = None, **_: object) -> MagicMock:
            rows = {
                12: [page_payload(1, space_id=12, last_modified=2000)],
                13: [page_payload(2, space_id=13, last_modified=1000)],
            }[space_id]
            if modified_after is not None:
                rows = [r for r in rows if r["lastModified"] > modified_after]
            return response(spring_page(rows))

        connector.data_source.list_pages = AsyncMock(side_effect=listing)
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}})
        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        written = [
            record.external_record_id
            for call in processor.on_new_records.await_args_list
            for record, _ in call.args[0]
        ]
        assert "page:2" in written


class TestSyncFailureNotification:
    @pytest.mark.asyncio
    async def test_a_phase_that_dies_is_reported_without_aborting_the_run(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The client tests against their own wiki, so one bad endpoint must cost only
        # its own phase: the rest still syncs and the failure is on record.
        connector.notify = AsyncMock()
        connector._sync_users = AsyncMock(side_effect=RuntimeError("kaboom"))
        connector._sync_spaces = AsyncMock(return_value={12: {"id": 12}})
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(1)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        with patch(
            "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
            AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

        assert "kaboom" in connector.notify.await_args.kwargs["message"]
        # Pages still synced despite the user phase dying.
        assert processor.on_new_records.await_count == 1

    @pytest.mark.asyncio
    async def test_groups_are_skipped_when_the_user_directory_fails(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Syncing groups against a partial directory would strip every member it could
        # not resolve, so the previous membership is left in place instead.
        connector.notify = AsyncMock()
        connector._sync_users = AsyncMock(side_effect=RuntimeError("directory down"))
        connector._sync_spaces = AsyncMock(return_value={})

        with patch(
            "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
            AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

        processor.on_new_user_groups.assert_not_awaited()
        assert any("Group membership was left as it is" in f for f in connector._failures)

    @pytest.mark.asyncio
    async def test_an_error_outside_every_phase_still_aborts_and_notifies(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.notify = AsyncMock()

        with pytest.raises(RuntimeError, match="kaboom"):
            with patch(
                "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
                AsyncMock(side_effect=RuntimeError("kaboom")),
            ):
                await connector.run_sync()

        connector.notify.assert_awaited_once()
        assert "kaboom" in connector.notify.await_args.kwargs["message"]

    def test_failure_preview_is_capped(self, connector: DrupalWikiConnector) -> None:
        for index in range(12):
            connector._fail(f"space {index}")

        preview = connector._failure_preview()

        # A bulleted list, because a run can collect a dozen of these.
        assert preview.startswith("- space 0\n- space 1")
        assert preview.endswith("- ...and 2 more")
        assert preview.count("\n") == 10
