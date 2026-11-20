"""Tests for the Drupal Wiki connector.

The data source and processor are mocked, so these cover the sync rules themselves:
checkpoints, permissions, deletions, filters and streaming.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager, ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import AccessRule, Connectors, ProgressStatus
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    MultiselectOperator,
)
from app.connectors.sources.drupal_wiki.connector import (
    ATTACHMENT_RECHECKS_PER_RUN,
    GRAPHQL_PROBE_TIMEOUT_S,
    PAGE_SIZE,
    PAGE_STRUCTURE_REFUSED_ACTION_CODE,
    TREE_UNREADABLE,
    DrupalWikiAuthError,
    DrupalWikiConnector,
    DrupalWikiSyncError,
    PageListing,
    _grants_digest,
)
from app.models.entities import Record, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.drupal_wiki.drupal_wiki import DrupalWikiRESTClientViaToken
from app.sources.external.drupal_wiki.graphql import (
    DrupalWikiGraphQLAuthError,
    DrupalWikiGraphQLError,
    DrupalWikiGraphQLRefusedError,
    DrupalWikiGraphQLTransientError,
)
from app.utils.url_fetcher import HostCheckError

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
    record.access_rule = AccessRule.STRICT
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
    proc.on_record_content_update = AsyncMock()
    proc.inheritance_when_unreadable = AsyncMock(return_value=False)
    proc.note_kept_records = MagicMock()
    proc.get_nodes_by_filters = AsyncMock(return_value=[])
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_records_in_record_group = AsyncMock(return_value=[])
    proc.get_records_by_parent = AsyncMock(return_value=[])
    proc.get_all_app_users = AsyncMock(return_value=[])
    proc.withdraw_app_user_access = AsyncMock()
    proc.delete_record_by_external_id = AsyncMock()
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
    # An attachment no listing has is removed only on this lookup's 404.
    instance.data_source.get_attachment = AsyncMock(return_value=response(status=404))
    instance.sync_filters = FilterCollection()
    instance.indexing_filters = FilterCollection()
    instance.record_sync_point.read_sync_point = AsyncMock(return_value={})
    instance.record_sync_point.update_sync_point = AsyncMock()
    # init() always builds the GraphQL data source. Spaces are flat by default; a tree
    # that cannot be read is the failure a test opts into.
    instance.graphql = MagicMock()
    instance.graphql.get_space_tree = AsyncMock(return_value=[])
    # A protected space has no members unless a test gives it some.
    instance.graphql.get_space_members = AsyncMock(return_value={"users": [], "groups": []})
    instance._enabled_emails = set()
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

    @pytest.mark.asyncio
    async def test_a_wiki_address_the_deployment_refuses_fails_init(
        self, connector: DrupalWikiConnector
    ) -> None:
        with self._patched_init(MagicMock()), patch(
            "app.connectors.sources.drupal_wiki.connector.check_wiki_address",
            AsyncMock(side_effect=HostCheckError('"169.254.169.254" is a cloud metadata address')),
        ):
            with pytest.raises(ConnectorInitError, match="cloud metadata"):
                await connector.init()

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
            "app.connectors.sources.drupal_wiki.connector.check_wiki_address", AsyncMock(),
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
    async def test_a_login_page_answering_200_fails_the_test(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.client = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=non_json())
        connector.data_source.list_spaces = AsyncMock(return_value=non_json())

        with pytest.raises(ConnectorInitError, match="login or proxy page"):
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
                {"id": 4, "email": "d@example.com", "userName": "d", "status": "DISABLED"},
            ], last=False)),
            response(spring_page([{"id": 3, "email": "c@example.com", "userName": "c", "status": "ENABLED"}])),
        ])

        users_by_id = await connector._sync_users()

        assert set(users_by_id) == {"1", "3"}
        # Everyone is listed, so the disabled accounts are known; they are not written.
        assert connector.data_source.list_users.await_args.kwargs["only_active"] is False
        assert connector._enabled_emails == {"a@example.com", "c@example.com"}
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
    async def test_a_protected_space_grants_its_enabled_members_and_groups(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector._enabled_emails = {"alice@example.com"}
        connector.graphql.get_space_members = AsyncMock(return_value={
            "users": [{"email": "alice@example.com"}, {"email": "blocked@example.com"}], "groups": [{"id": 7}],
        })
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 12, "name": "HR", "type": "BASIC", "accessStatus": "PRIVATE"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {12}
        [(group, permissions)] = processor.on_new_record_groups.await_args.args[0]
        assert group.external_group_id == "12"
        assert sorted((p.entity_type, p.email, p.external_id) for p in permissions) == [
            (EntityType.GROUP, None, "7"), (EntityType.USER, "alice@example.com", None),
        ]
        assert connector.full_sync_coverage().complete is True

    @pytest.mark.asyncio
    async def test_a_protected_space_whose_members_cannot_be_read_is_left_as_it_is(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Not written, its pages not synced, and not removed as a space the run did not sync.
        connector.graphql.get_space_members = AsyncMock(side_effect=DrupalWikiGraphQLTransientError("502"))
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 12, "name": "HR", "type": "BASIC", "accessStatus": "PRIVATE"},
            {"id": 3, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
        ])))
        processor.get_nodes_by_filters = AsyncMock(return_value=[{"externalGroupId": "12"}, {"externalGroupId": "3"}])

        spaces = await connector._sync_spaces()

        assert set(spaces) == {3}
        assert [g.external_group_id for g, _ in processor.on_new_record_groups.await_args.args[0]] == ["3"]
        processor.on_record_group_deleted.assert_not_awaited()
        assert connector._space_unknown(12)
        assert connector.full_sync_coverage().complete is False

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
        # The space it could not identify may be one that is stored, so the run is incomplete.
        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_listing_with_an_unidentified_space_removes_only_what_it_named(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # SpaceExcerptDTO.id may be null (spec). Stored 13 may be the row without an id, so it
        # stays; 91, which the listing names as the trash, still goes.
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 12, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
            {"id": None, "name": "Other", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
            {"id": 91, "name": "Trash", "type": "TRASH", "accessStatus": "AUTHENTICATED"},
        ])))
        processor.get_nodes_by_filters = AsyncMock(return_value=[
            {"externalGroupId": "12"}, {"externalGroupId": "13"}, {"externalGroupId": "91"},
        ])
        processor.on_record_group_deleted = AsyncMock(return_value=True)

        await connector._sync_spaces()

        assert [c.args for c in processor.on_record_group_deleted.await_args_list] == [("91", "conn-1")]
        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_space_listing_that_ends_early_removes_nothing(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_spaces = AsyncMock(side_effect=[
            response(spring_page([{"id": 12, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"}],
                                 last=False)),
            response(spring_page([], last=False)),
        ])
        processor.get_nodes_by_filters = AsyncMock(return_value=[{"externalGroupId": "13"}])

        with pytest.raises(DrupalWikiSyncError, match="not the last"):
            await connector._sync_spaces()

        processor.on_record_group_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_space_with_no_audience_is_still_created_without_grants(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The group has to exist so its pages have somewhere to belong. Granting
        # someone the read right later then only adds an edge, instead of needing a
        # full resync to discover a space that was never created.
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 7, "name": "Nobody's space", "type": "BASIC", "accessStatus": "PRIVATE"},
        ])))

        spaces = await connector._sync_spaces()

        assert set(spaces) == {7}
        group, permissions = processor.on_new_record_groups.await_args.args[0][0]
        assert group.external_group_id == "7"
        assert permissions == []

    @pytest.mark.asyncio
    async def test_only_content_spaces_are_synced(self, connector: DrupalWikiConnector) -> None:
        # The trash is read only by two system roles (node 460) and a personal workspace
        # only by its owner (node 226), whom the API does not name; system and news-draft
        # spaces have no documented readers. Selecting one does not sync it either.
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 1, "name": "Workspace", "type": "USERSPACE", "accessStatus": "PRIVATE"},
            {"id": 2, "name": "Trash", "type": "TRASH", "accessStatus": "AUTHENTICATED"},
            {"id": 3, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
            {"id": 4, "name": "Desk", "type": "DESKTOP", "accessStatus": "PRIVATE"},
            {"id": 5, "name": "System", "type": "SYSTEMSPACE", "accessStatus": "AUTHENTICATED"},
            {"id": 6, "name": "Drafts", "type": "CORPORATENEWSDRAFTS", "accessStatus": "AUTHENTICATED"},
            {"id": 7, "name": "Box", "type": "CONTAINER", "accessStatus": "AUTHENTICATED"},
            {"id": 8, "name": "Blog", "type": "CORPORATEBLOG", "accessStatus": "AUTHENTICATED"},
            {"id": 9, "name": "News", "type": "CORPORATENEWS", "accessStatus": "AUTHENTICATED"},
            {"id": 10, "name": "Odd", "accessStatus": "AUTHENTICATED"},
        ])))
        connector.sync_filters = FilterCollection(filters=[Filter(
            key="space_ids", type=FilterType.LIST, operator=MultiselectOperator.IN, value=["1", "2", "3", "7", "8", "9"],
        )])

        spaces = await connector._sync_spaces()

        assert set(spaces) == {3, 7, 8, 9}

    @pytest.mark.asyncio
    async def test_a_space_the_listing_no_longer_returns_is_removed_with_its_pages(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Deleting a space deletes its content (node 505). A stored trash space from an
        # older version goes too, and so does a space the filter leaves out: the grants an
        # earlier sync wrote on it (every role holder of a protected space) would otherwise
        # stay with its pages.
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            {"id": 3, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
            {"id": 4, "name": "Other", "type": "BASIC", "accessStatus": "AUTHENTICATED"},
            {"id": 91, "name": "Trash", "type": "TRASH", "accessStatus": "AUTHENTICATED"},
        ])))
        connector.sync_filters = FilterCollection(filters=[Filter(
            key="space_ids", type=FilterType.LIST, operator=MultiselectOperator.IN, value=["3"],
        )])
        processor.get_nodes_by_filters = AsyncMock(return_value=[
            {"externalGroupId": "3"}, {"externalGroupId": "4"}, {"externalGroupId": "5"}, {"externalGroupId": "91"},
        ])
        processor.on_record_group_deleted = AsyncMock(return_value=True)

        await connector._sync_spaces()

        assert [c.args for c in processor.on_record_group_deleted.await_args_list] == [
            ("4", "conn-1"), ("5", "conn-1"), ("91", "conn-1"),
        ]

    @pytest.mark.asyncio
    async def test_an_empty_space_listing_removes_nothing(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([])))
        processor.get_nodes_by_filters = AsyncMock(return_value=[{"externalGroupId": "3"}])

        await connector._sync_spaces()

        processor.on_record_group_deleted.assert_not_awaited()
        # Nor may the unlink after a full sync take the stored spaces' links away.
        assert connector.full_sync_coverage().complete is False

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
        # The space keeps its checkpoint, so the next run writes the same pages again.
        assert page_checkpoint_writes(connector) == []

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

        assert [key for key, _ in page_checkpoint_writes(connector)] == ["WEBPAGE/space_pages/12"]
        assert connector._failures

    @pytest.mark.asyncio
    async def test_a_stored_page_older_than_the_checkpoint_is_not_written_again(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The checkpoint is the wiki's own lastModified, so both sides of the comparison
        # come from the same clock. The space is listed in full (a share or a deletion
        # need not move lastModified); only what changed, or is not stored, is written.
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: {"last_modified": 10_000} if key.startswith("WEBPAGE/") else {}
        )
        processor.get_records_in_record_group = AsyncMock(return_value=[stored_record(1), stored_record(2)])
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            page_payload(1, last_modified=10_000),   # stored, not changed
            page_payload(2, last_modified=10_001),   # stored, changed
            page_payload(3, last_modified=9_000),    # not stored: moved in, or missed before
        ])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}})

        assert "modified_after" not in connector.data_source.list_pages.await_args.kwargs
        written = [r.external_record_id for c in processor.on_new_records.await_args_list for r, _ in c.args[0]]
        assert written == ["page:2", "page:3"]


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

class TestMovesAndDeletions:
    """A move is an update; a page the wiki no longer has in its space is removed."""

    @staticmethod
    def wiki(connector: DrupalWikiConnector, processor: MagicMock, listed: dict[int, list[dict]],
             stored: dict[int, list[int]]) -> None:
        connector.data_source.list_pages = AsyncMock(
            side_effect=lambda space_id, **_: response(spring_page(listed.get(space_id, [])))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))
        processor.get_records_in_record_group = AsyncMock(
            side_effect=lambda _c, space, *_a, **_k: [stored_record(p, space_id=int(space)) for p in stored.get(int(space), [])]
        )
        processor.get_record_by_external_id = AsyncMock(side_effect=lambda _c, ext: next(
            (stored_record(p, space_id=s) for s, pages in stored.items() for p in pages if f"page:{p}" == ext), None
        ))

    @pytest.mark.asyncio
    async def test_page_moved_between_synced_spaces_is_resaved_under_the_new_space(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The external id is stable, so a move is an update: the record follows the
        # page to space 13 instead of being deleted from 12 and recreated, whether or
        # not the move changed lastModified (13 has it under no record yet).
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: {"last_modified": 5000} if key.startswith("WEBPAGE/") else {}
        )
        self.wiki(connector, processor, {13: [page_payload(733, space_id=13, last_modified=1000)]}, {12: [733]})

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        record, _ = processor.on_new_records.await_args.args[0][0]
        assert record.external_record_id == "page:733"
        assert record.external_record_group_id == "13"
        processor.delete_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_the_wiki_no_longer_has_is_removed(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: [page_payload(1)]}, {12: [1, 2]})
        connector.data_source.get_page = AsyncMock(return_value=response(status=404))

        await connector._sync_pages({12: {"id": 12}})

        connector.data_source.get_page.assert_awaited_once_with(2)
        processor.delete_record_by_external_id.assert_awaited_once_with("conn-1", "page:2")

    @pytest.mark.asyncio
    async def test_a_page_moved_where_this_run_did_not_write_it_is_removed(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Into a space the sync leaves out, or the trash: the old space's readers must
        # not keep it.
        self.wiki(connector, processor, {12: []}, {12: [2]})
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=99)))

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_awaited_once_with("conn-1", "page:2")

    @pytest.mark.asyncio
    async def test_a_page_the_listing_skipped_is_kept_and_written(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Offset paging can skip a page when another is deleted between two windows.
        self.wiki(connector, processor, {12: []}, {12: [2]})
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=12)))

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_not_awaited()
        assert [r.external_record_id for r, _ in processor.on_new_records.await_args.args[0]] == ["page:2"]

    @pytest.mark.asyncio
    async def test_a_page_that_cannot_be_checked_stays_and_the_run_says_so(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: []}, {12: [2]})
        connector.data_source.get_page = AsyncMock(return_value=response(status=500))

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_not_awaited()
        assert any("page 2 still exists" in failure for failure in connector._failures)

    @pytest.mark.asyncio
    async def test_a_space_that_did_not_list_in_full_removes_nothing(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {}, {12: [1, 2]})
        connector.data_source.list_pages = AsyncMock(side_effect=[
            response(spring_page([page_payload(1)], last=False)), response(status=500),
        ])
        connector.data_source.get_page = AsyncMock(return_value=response(status=404))

        await connector._sync_pages({12: {"id": 12}})

        connector.data_source.get_page.assert_not_awaited()
        processor.delete_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_listing_that_ends_early_removes_nothing(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Twice in a row: an empty window that says more follow must not count as a miss.
        self.wiki(connector, processor, {}, {12: [1, 2]})
        self.remembered(connector)
        connector.data_source.list_pages = AsyncMock(side_effect=lambda **_: [
            response(spring_page([page_payload(1)], last=False)), response(spring_page([], last=False)),
        ][_["page"]])
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=12)))

        await connector._sync_pages({12: {"id": 12}})
        await connector._sync_pages({12: {"id": 12}})

        connector.data_source.get_page.assert_not_awaited()
        processor.delete_record_by_external_id.assert_not_awaited()
        assert any("not the last" in failure for failure in connector._failures)

    @pytest.mark.asyncio
    async def test_a_move_whose_save_failed_does_not_keep_the_old_copy(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Page 2 moved from 12 to 13, and the save under 13 failed. The copy under 12 must
        # not keep 12's readers: it is looked up like any page its space stopped listing.
        self.wiki(connector, processor, {12: [], 13: [page_payload(2, space_id=13)]}, {12: [2]})
        processor.on_new_records = AsyncMock(side_effect=RuntimeError("write failed"))
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=13)))

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        processor.delete_record_by_external_id.assert_awaited_once_with("conn-1", "page:2")

    @pytest.mark.asyncio
    async def test_a_move_saved_before_its_batch_failed_keeps_the_moved_page(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The move into 13 was saved, then the batch raised. The page is now stored under 13,
        # not 12, so the removal pass must not delete it; the next run writes it again even
        # though it is older than 13's checkpoint.
        stored: dict[int, list[int]] = {12: [2]}
        self.wiki(connector, processor, {12: [], 13: [page_payload(2, space_id=13)]}, stored)
        checkpoints = self.remembered(connector)
        checkpoints["WEBPAGE/space_pages/13"] = {"last_modified": 5000}

        def save_then_raise(_batch: list) -> None:
            stored.clear()
            stored[13] = [2]
            raise RuntimeError("publish failed")

        processor.on_new_records = AsyncMock(side_effect=save_then_raise)
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=13)))

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        processor.delete_record_by_external_id.assert_not_awaited()

        processor.on_new_records = AsyncMock()
        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        assert [r.external_record_id for c in processor.on_new_records.await_args_list for r, _ in c.args[0]] == ["page:2"]

    @pytest.mark.asyncio
    async def test_a_page_missing_twice_that_moved_since_is_not_deleted(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Missing from 12 twice, but by the time the removal pass acts it is stored under 13.
        self.wiki(connector, processor, {12: []}, {12: [2]})
        checkpoints = self.remembered(connector)
        checkpoints["removals/space/all"] = {"after": 0, "missing": [2]}
        processor.get_record_by_external_id = AsyncMock(return_value=stored_record(2, space_id=13))

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_whose_attachments_failed_is_written_again_below_the_checkpoint(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # New to 12 (moved in) but older than its checkpoint: once stored, only a note that its
        # attachments are owed brings it back.
        stored: dict[int, list[int]] = {12: []}
        self.wiki(connector, processor, {12: [page_payload(2, last_modified=1000)]}, stored)
        checkpoints = self.remembered(connector)
        checkpoints["WEBPAGE/space_pages/12"] = {"last_modified": 5000}
        connector.data_source.list_attachments = AsyncMock(side_effect=[
            response(status=500), response([{"id": 88, "fileName": "a.pdf", "lastModified": 1000}]),
        ])

        await connector._sync_pages({12: {"id": 12}})
        stored[12] = [2]
        processor.on_new_records.reset_mock()
        await connector._sync_pages({12: {"id": 12}})

        written = [r.external_record_id for c in processor.on_new_records.await_args_list for r, _ in c.args[0]]
        assert written == ["page:2", "attachment:88"]

        # Owed no more: the run after skips it again.
        processor.on_new_records.reset_mock()
        await connector._sync_pages({12: {"id": 12}})
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_listing_with_an_unidentified_row_removes_nothing(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # PageExcerptDTO.id may be null (spec): the row may be stored page 2, so two such
        # listings are not two misses.
        self.wiki(connector, processor, {12: [page_payload(1), {"title": "?", "homeSpace": 12, "lastModified": 1}]},
                  {12: [1, 2]})
        self.remembered(connector)
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=12)))

        await connector._sync_pages({12: {"id": 12}})
        await connector._sync_pages({12: {"id": 12}})

        connector.data_source.get_page.assert_not_awaited()
        processor.delete_record_by_external_id.assert_not_awaited()
        assert any("without an id" in failure for failure in connector._failures)

    @staticmethod
    def page_with_attachments(connector: DrupalWikiConnector, processor: MagicMock, checkpoint: dict,
                              last_modified: int, attachments: MagicMock) -> None:
        """Page 1 of space 12, stored with attachments 88 and 89."""
        TestMovesAndDeletions.wiki(connector, processor, {12: [page_payload(1, last_modified=last_modified)]}, {12: [1]})
        processor.get_records_in_record_group = AsyncMock(side_effect=lambda _c, space, *_a, **_k: [
            stored_record(1), attachment_record(88, page_id=1), attachment_record(89, page_id=1),
        ] if space == "12" else [])
        processor.get_records_by_parent = AsyncMock(
            return_value=[attachment_record(88, page_id=1), attachment_record(89, page_id=1)]
        )
        checkpoints = TestMovesAndDeletions.remembered(connector)
        checkpoints.update({"WEBPAGE/space_pages/12": checkpoint} if checkpoint else {})
        connector.data_source.list_attachments = AsyncMock(
            **({"side_effect": attachments} if isinstance(attachments, list) else {"return_value": attachments})
        )

    @pytest.mark.parametrize(("checkpoint", "last_modified"), [
        ({}, 1000),  # a full sync: no checkpoint, so every page and its attachments are listed
        ({"last_modified": 5000}, 9000),  # the page changed
    ])
    @pytest.mark.asyncio
    async def test_an_attachment_deleted_from_a_page_that_is_written_is_removed(
        self, connector: DrupalWikiConnector, processor: MagicMock, checkpoint: dict, last_modified: int
    ) -> None:
        self.page_with_attachments(connector, processor, checkpoint, last_modified, response([
            {"id": 88, "fileName": "a.pdf", "lastModified": 1000},
        ]))

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_awaited_once_with("conn-1", "attachment:89")

    @pytest.mark.parametrize("attachments", [
        response(status=500),
        response([{"fileName": "no-id.pdf", "lastModified": 1000}]),
    ], ids=["listing failed", "row without an id"])
    @pytest.mark.asyncio
    async def test_an_attachment_listing_that_is_not_complete_removes_no_attachment(
        self, connector: DrupalWikiConnector, processor: MagicMock, attachments: MagicMock
    ) -> None:
        self.page_with_attachments(connector, processor, {}, 1000, attachments)

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_not_awaited()
        # Owed: the next run lists its attachments again, whatever its date.
        assert connector.record_sync_point.update_sync_point.await_args_list[-1].args == (
            "owed/space/all", {"pages": [1]},
        )

    @pytest.mark.asyncio
    async def test_a_row_without_an_id_is_not_written_when_a_later_window_fails(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        full_window = [{"id": 1000 + i, "fileName": f"f{i}.pdf", "lastModified": 1000} for i in range(PAGE_SIZE - 1)]
        full_window.append({"fileName": "no-id.pdf", "lastModified": 1000})
        self.page_with_attachments(connector, processor, {}, 1000, [response(full_window), response(status=500)])

        await connector._sync_pages({12: {"id": 12}})

        written = [r.external_record_id for c in processor.on_new_records.await_args_list for r, _ in c.args[0]]
        assert "attachment:None" not in written
        assert len(written) == PAGE_SIZE  # the page and the rows that have an id
        processor.delete_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_attachment_left_under_a_moved_pages_old_space_is_found_and_removed(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Page 2 moved from 12 to 13, and attachment 89 was deleted. 13 is listed first, before
        # the read of 12's stored records: the page's stored attachments are read by the page.
        self.wiki(connector, processor, {13: [page_payload(2, space_id=13)], 12: []}, {12: [2]})
        processor.get_records_by_parent = AsyncMock(side_effect=lambda _c, parent, *_a, **_k: [
            attachment_record(88, page_id=2), attachment_record(89, page_id=2),
        ] if parent == "page:2" else [])
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 88, "fileName": "a.pdf", "lastModified": 1000},
        ]))

        await connector._sync_pages({13: {"id": 13}, 12: {"id": 12}})

        assert [c.args for c in processor.delete_record_by_external_id.await_args_list] == [("conn-1", "attachment:89")]

    @pytest.mark.parametrize(("lookup", "owed_after"), [
        (response(status=500), [2]),  # not confirmed: still owed
        (response(status=404), []),  # confirmed gone
    ])
    @pytest.mark.asyncio
    async def test_an_owed_page_the_listing_skipped_stays_owed_until_written_or_removed(
        self, connector: DrupalWikiConnector, processor: MagicMock, lookup: MagicMock, owed_after: list
    ) -> None:
        self.wiki(connector, processor, {12: []}, {12: [2]})
        checkpoints = self.remembered(connector)
        checkpoints.update({"WEBPAGE/space_pages/12": {"last_modified": 5000}, "owed/space/all": {"pages": [2]}})
        connector.data_source.get_page = AsyncMock(return_value=lookup)

        await connector._sync_pages({12: {"id": 12}})

        assert checkpoints["owed/space/all"] == {"pages": owed_after}

    @pytest.mark.asyncio
    async def test_a_rejected_token_is_not_replaced_by_a_failed_owed_save(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {}, {12: [1]})
        connector.data_source.list_pages = AsyncMock(return_value=response(status=401))
        connector.record_sync_point.update_sync_point = AsyncMock(
            side_effect=lambda key, _data: (_ for _ in ()).throw(RuntimeError("store down")) if key.startswith("owed/") else None
        )

        with pytest.raises(DrupalWikiAuthError):
            await connector._sync_pages({12: {"id": 12}})

        assert any("store down" in failure for failure in connector._failures)

    @pytest.mark.asyncio
    async def test_a_looked_up_page_without_an_id_is_written_under_the_id_asked_for(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # PageDTO.id may be null (spec). Without the asked id the attachment query would drop
        # its page filter and the record would be "page:None".
        self.wiki(connector, processor, {12: []}, {12: [2]})
        connector.data_source.get_page = AsyncMock(return_value=response(
            {k: v for k, v in page_payload(2, space_id=12).items() if k != "id"}
        ))

        await connector._sync_pages({12: {"id": 12}})

        assert [r.external_record_id for r, _ in processor.on_new_records.await_args.args[0]] == ["page:2"]
        assert connector.data_source.list_attachments.await_args.kwargs["page_id"] == 2

    @pytest.mark.asyncio
    async def test_a_looked_up_page_answering_as_another_page_is_not_written(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: []}, {12: [2]})
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(3, space_id=12)))

        await connector._sync_pages({12: {"id": 12}})

        processor.on_new_records.assert_not_awaited()
        processor.delete_record_by_external_id.assert_not_awaited()
        assert any("page 2 still exists" in failure for failure in connector._failures)

    @pytest.mark.asyncio
    async def test_lookups_are_bounded_per_run_and_the_rest_wait(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: []}, {12: list(range(1, 8))})
        connector.data_source.get_page = AsyncMock(return_value=response(status=404))

        with patch("app.connectors.sources.drupal_wiki.connector.GONE_PAGE_CHECKS_PER_RUN", 3):
            await connector._sync_pages({12: {"id": 12}})

        assert [c.args[1] for c in processor.delete_record_by_external_id.await_args_list] == [
            "page:1", "page:2", "page:3",
        ]

    @pytest.mark.asyncio
    async def test_a_run_that_leaves_lookups_for_later_does_not_count_as_a_complete_full_sync(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Pages past the cap are unconfirmed: the unlink after a full sync must not take
        # their links away.
        self.wiki(connector, processor, {12: []}, {12: list(range(1, 8))})
        connector.data_source.get_page = AsyncMock(return_value=response(status=404))

        with patch("app.connectors.sources.drupal_wiki.connector.GONE_PAGE_CHECKS_PER_RUN", 3):
            await connector._sync_pages({12: {"id": 12}})
        assert connector._failures == []
        assert connector.full_sync_coverage().complete is False

        with patch("app.connectors.sources.drupal_wiki.connector.GONE_PAGE_CHECKS_PER_RUN", 10):
            connector._removals_unchecked = False
            await connector._sync_pages({12: {"id": 12}})
        assert connector.full_sync_coverage().complete is True

    @staticmethod
    def remembered(connector: DrupalWikiConnector) -> dict[str, dict]:
        checkpoints: dict[str, dict] = {}
        connector.record_sync_point.read_sync_point = AsyncMock(side_effect=lambda key: dict(checkpoints.get(key, {})))
        connector.record_sync_point.update_sync_point = AsyncMock(
            side_effect=lambda key, payload: checkpoints.__setitem__(key, dict(payload))
        )
        return checkpoints

    @pytest.mark.asyncio
    async def test_a_page_missing_from_two_listings_goes_whatever_the_wiki_answers(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # What a trashed page answers is not documented; one still answering 200 from its
        # old space must not be written again for ever. The first miss looks it up; the
        # second removes it without asking.
        self.wiki(connector, processor, {12: []}, {12: [2]})
        self.remembered(connector)
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=12)))

        await connector._sync_pages({12: {"id": 12}})
        processor.delete_record_by_external_id.assert_not_awaited()
        connector.data_source.get_page.reset_mock()

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_awaited_once_with("conn-1", "page:2")
        connector.data_source.get_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_listed_again_is_not_counted_as_missing(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        listed: dict[int, list[dict]] = {12: []}
        self.wiki(connector, processor, listed, {12: [2]})
        self.remembered(connector)
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=12)))
        await connector._sync_pages({12: {"id": 12}})  # skipped by the listing once

        listed[12] = [page_payload(2)]
        await connector._sync_pages({12: {"id": 12}})
        listed[12] = []
        await connector._sync_pages({12: {"id": 12}})  # missing once again: looked up, kept

        processor.delete_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lookups_that_keep_failing_do_not_hold_back_the_ones_after_them(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: []}, {12: list(range(1, 8))})
        checkpoints = self.remembered(connector)
        checkpoints["removals/space/all"] = {"after": 3, "missing": []}
        connector.data_source.get_page = AsyncMock(return_value=response(status=500))

        with patch("app.connectors.sources.drupal_wiki.connector.GONE_PAGE_CHECKS_PER_RUN", 3):
            await connector._sync_pages({12: {"id": 12}})

        assert [c.args[0] for c in connector.data_source.get_page.await_args_list] == [4, 5, 6]

    @pytest.mark.asyncio
    async def test_a_page_the_token_may_no_longer_read_goes_and_the_run_goes_on(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Trashed, or moved into a space the token's user cannot read: the lookup answers 403
        # while the token still lists spaces. Only that page is affected, and its stored copy,
        # written from where the user could read it, goes rather than keep its grants.
        self.wiki(connector, processor, {12: []}, {12: [2, 3]})
        checkpoints = self.remembered(connector)
        connector.data_source.get_page = AsyncMock(side_effect=lambda page_id: response(status=403) if page_id == 2
                                                   else response(status=404))
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([{"id": 12}])))

        await connector._sync_pages({12: {"id": 12}})

        assert [c.args for c in processor.delete_record_by_external_id.await_args_list] == [
            ("conn-1", "page:2"), ("conn-1", "page:3")]
        assert checkpoints["removals/space/all"] == {"after": 0, "missing": [2, 3]}

    @pytest.mark.asyncio
    async def test_a_lookup_refused_with_the_token_unchecked_keeps_the_page(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: []}, {12: [2]})
        self.remembered(connector)
        connector.data_source.get_page = AsyncMock(return_value=response(status=403))
        connector.data_source.list_spaces = AsyncMock(side_effect=TimeoutError())

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_not_awaited()
        assert any("token could not be checked" in failure for failure in connector._failures)

    @pytest.mark.asyncio
    async def test_the_missing_pages_are_saved_when_a_rejected_token_stops_the_lookups(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The token dies during the lookups: the run stops, but the pages missing now are
        # known as missing before, so the run after removes them without asking.
        self.wiki(connector, processor, {12: []}, {12: [2, 3]})
        checkpoints = self.remembered(connector)
        connector.data_source.get_page = AsyncMock(return_value=response(status=403))
        connector.data_source.list_spaces = AsyncMock(return_value=response(status=403))

        with pytest.raises(DrupalWikiAuthError):
            await connector._sync_pages({12: {"id": 12}})
        assert checkpoints["removals/space/all"]["missing"] == [2, 3]
        processor.delete_record_by_external_id.assert_not_awaited()

        connector.data_source.get_page.reset_mock()
        await connector._sync_pages({12: {"id": 12}})

        connector.data_source.get_page.assert_not_awaited()
        assert [c.args for c in processor.delete_record_by_external_id.await_args_list] == [
            ("conn-1", "page:2"), ("conn-1", "page:3")]

    @pytest.mark.asyncio
    async def test_an_unchanged_stored_page_is_kept_through_a_full_sync(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # A full sync whose checkpoint deletion failed still skips unchanged pages; they
        # and their attachments are noted as kept so the unlink after it leaves them.
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: {"last_modified": 5000} if key.startswith("WEBPAGE/") else {}
        )
        self.wiki(connector, processor, {12: [page_payload(1, last_modified=1000)]}, {12: [1]})
        processor.get_records_in_record_group = AsyncMock(return_value=[stored_record(1), attachment_record(88, page_id=1)])

        await connector._sync_pages({12: {"id": 12}})

        processor.note_kept_records.assert_called_once_with("conn-1", ["internal-1", "internal-att-88"])
        processor.on_new_records.assert_not_awaited()

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
        connector.graphql.get_space_tree = AsyncMock(side_effect=DrupalWikiGraphQLError("no tree"))
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
        # Only a space the sync can sync is offered: chosen alone, the trash would sync nothing.
        assert [option.id for option in result.options] == ["3"]
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_a_login_page_is_a_failed_lookup_not_an_error(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.data_source.list_spaces = AsyncMock(return_value=non_json())

        result = await connector.get_filter_options("space_ids")

        assert result.success is False
        assert "login or proxy page" in (result.message or "")

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

        pages = [page async for page in connector._fetch_pages(12, PageListing())]

        assert [page["id"] for page in pages] == [1, 2]
        # The space is filtered by the server, not by us.
        assert connector.data_source.list_pages.await_args_list[0].kwargs["space_id"] == 12
        assert connector.data_source.list_pages.await_args_list[1].kwargs["page"] == 1

    @pytest.mark.asyncio
    async def test_page_listing_fails_on_an_empty_window_that_is_not_the_last(
        self, connector: DrupalWikiConnector
    ) -> None:
        # The envelope says more follow: the listing is unfinished, not empty.
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([], last=False))
        )

        with pytest.raises(DrupalWikiSyncError, match="pages of space 12.*not the last"):
            [page async for page in connector._fetch_pages(12, PageListing())]
        assert connector.data_source.list_pages.await_count == 1

    @pytest.mark.asyncio
    async def test_an_empty_last_window_ends_the_listing(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([], last=True)))

        assert [page async for page in connector._fetch_pages(12, PageListing())] == []

    @pytest.mark.asyncio
    async def test_page_listing_raises_on_error_status(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(
            {"status": 500, "title": "Internal Server Error", "detail": "Search index unavailable"}, status=500,
        ))

        # The wiki's own reason, not just the status.
        with pytest.raises(
            DrupalWikiSyncError,
            match=r"pages of space 12: Internal Server Error: Search index unavailable \(HTTP 500\)",
        ):
            [page async for page in connector._fetch_pages(12, PageListing())]

    @pytest.mark.asyncio
    async def test_page_listing_keeps_shared_pages_and_drops_homeless_ones(
        self, connector: DrupalWikiConnector
    ) -> None:
        # ``space`` returns pages shared into it as well, which the share pass needs. A
        # page with no home space has no record group to belong to.
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([
            page_payload(1, space_id=99),
            page_payload(2, space_id=12),
            {"id": 3, "title": "No home", "lastModified": 1000},
        ])))
        listing = PageListing()

        pages = [page async for page in connector._fetch_pages(12, listing)]

        assert [page["id"] for page in pages] == [1, 2]
        # Not skipped silently: the row may be a page of this space.
        assert listing.unidentified == {12}

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

        records = await connector._build_page_records(page_payload(733), {}, PageListing())

        assert len(records) == 102  # the page itself plus 101 attachments
        assert connector.data_source.list_attachments.await_args_list[1].kwargs["page"] == 1

    def test_space_id_filters_default_to_everything(self, connector: DrupalWikiConnector) -> None:
        assert connector._get_space_id_filters() == (set(), set())

    @pytest.mark.asyncio
    async def test_space_tree_is_unknown_without_graphql(self, connector: DrupalWikiConnector) -> None:
        # None, not {}: an empty dict would mean "this space has no parents" and
        # would delete the stored PARENT_CHILD edges.
        connector.graphql.get_space_tree = AsyncMock(side_effect=DrupalWikiGraphQLError("no tree"))
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

        page_checkpoints = page_checkpoint_writes(connector)
        assert len(page_checkpoints) == 1
        key, payload = page_checkpoints[0]
        assert key == "WEBPAGE/space_pages/12"
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
        assert page_checkpoint_writes(connector) == []

        batch = processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in batch] == ["page:2"]

    @pytest.mark.asyncio
    async def test_unreadable_checkpoint_falls_back_to_a_full_window(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: (_ for _ in ()).throw(RuntimeError("db down")) if key.startswith("WEBPAGE/") else {}
        )
        processor.get_records_in_record_group = AsyncMock(return_value=[stored_record(1)])
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([page_payload(1)])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}})
        written = [r.external_record_id for r, _ in processor.on_new_records.await_args.args[0]]
        assert written == ["page:1"]


class TestPerSpaceCheckpoints:
    @pytest.mark.asyncio
    async def test_each_space_is_written_from_its_own_cursor(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: {"WEBPAGE/space_pages/12": {"last_modified": 500}}.get(key, {})
        )
        processor.get_records_in_record_group = AsyncMock(
            side_effect=lambda _c, space, *_a, **_k: [stored_record(1, space_id=12)] if space == "12"
            else [stored_record(2, space_id=13)]
        )
        connector.data_source.list_pages = AsyncMock(side_effect=lambda space_id, **_: response(spring_page([
            page_payload(1 if space_id == 12 else 2, space_id=space_id, last_modified=400),
        ])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}})

        written = [r.external_record_id for c in processor.on_new_records.await_args_list for r, _ in c.args[0]]
        # Space 13 has never synced, so it is written in full rather than from 12's cursor.
        assert written == ["page:2"]

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

        def listing(space_id: int | None = None, **_: object) -> MagicMock:
            return response(spring_page({
                12: [page_payload(1, space_id=12, last_modified=2000)],
                13: [page_payload(2, space_id=13, last_modified=1000)],
            }[space_id]))

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

        preview = connector._failure_preview(connector._problem_lines())

        # A bulleted list, because a run can collect a dozen of these.
        assert preview.startswith("- space 0\n- space 1")
        assert preview.endswith("- ...and 2 more")
        assert preview.count("\n") == 10


def page_checkpoint_writes(connector: DrupalWikiConnector) -> list[tuple]:
    """Writes of a space's page checkpoint, not of the other sync points a run keeps."""
    return [
        call.args for call in connector.record_sync_point.update_sync_point.await_args_list
        if call.args[0].startswith("WEBPAGE/")
    ]


def non_json(status: int = 200) -> MagicMock:
    """A 200 from a proxy or SSO page in front of the wiki."""
    resp = MagicMock()
    resp.status = status
    resp.json.side_effect = ValueError("Expecting value")
    return resp


async def run_with_no_filters(connector: DrupalWikiConnector) -> None:
    with patch(
        "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
        AsyncMock(return_value=(FilterCollection(), FilterCollection())),
    ):
        await connector.run_sync()


class TestFullSyncCoverage:
    def test_a_clean_run_read_everything(self, connector: DrupalWikiConnector) -> None:
        assert connector.full_sync_coverage().complete is True

    def test_a_run_that_skipped_anything_did_not(self, connector: DrupalWikiConnector) -> None:
        # Otherwise the platform unlinks everything the run skipped.
        connector._fail("Could not read who has access to the Drupal Wiki space 'HR'")
        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_dead_phase_makes_the_run_incomplete(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.notify = AsyncMock()
        connector._sync_users = AsyncMock(return_value={})
        connector._sync_groups = AsyncMock()
        connector._sync_spaces = AsyncMock(side_effect=RuntimeError("spaces down"))

        await run_with_no_filters(connector)

        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_coverage_is_reset_by_the_next_run(self, connector: DrupalWikiConnector) -> None:
        connector._fail("left over from the last run")
        connector._sync_users = AsyncMock(return_value={})
        connector._sync_groups = AsyncMock()
        connector._sync_spaces = AsyncMock(return_value={})

        await run_with_no_filters(connector)

        assert connector.full_sync_coverage().complete is True


class TestOneFailureStaysLocal:
    @pytest.mark.asyncio
    async def test_a_timeout_listing_one_space_does_not_stop_the_next(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        async def list_pages(space_id: int, **_: object) -> MagicMock:
            if space_id == 12:
                raise httpx.ReadTimeout("")
            return response(spring_page([page_payload(2, space_id=13)]))

        connector.data_source.list_pages = AsyncMock(side_effect=list_pages)
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        await connector._sync_pages({12: {"name": "Slow"}, 13: {"name": "Fine"}})

        written = [r.external_record_id for r, _ in processor.on_new_records.await_args.args[0]]
        assert written == ["page:2"]
        assert connector._failures == [
            "Could not sync the pages of the Drupal Wiki space 'Slow': ReadTimeout"
        ]
        # Only the failed space's checkpoint stays put.
        assert [key for key, _ in page_checkpoint_writes(connector)] == ["WEBPAGE/space_pages/13"]

    @pytest.mark.asyncio
    async def test_a_timeout_reading_one_group_does_not_stop_the_next(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([
            {"id": 5, "name": "Slow"}, {"id": 6, "name": "Fine"},
        ])))
        connector.data_source.get_group = AsyncMock(side_effect=[
            httpx.ReadTimeout(""), response({"id": 6, "members": []}),
        ])

        await connector._sync_groups({})

        groups = processor.on_new_user_groups.await_args.args[0]
        assert [group.source_user_group_id for group, _ in groups] == ["6"]
        assert connector._failures == ["Could not read the members of group 5: ReadTimeout"]

    @pytest.mark.asyncio
    async def test_a_phase_error_without_a_message_names_its_type(
        self, connector: DrupalWikiConnector
    ) -> None:
        async def times_out() -> None:
            raise TimeoutError

        await connector._run_phase("spaces", times_out())

        assert connector._failures == ["Could not sync spaces from Drupal Wiki: TimeoutError"]


class TestRevokedToken:
    @pytest.mark.asyncio
    async def test_a_401_fails_the_run_with_an_auth_notification(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.notify = AsyncMock()
        connector.data_source.list_users = AsyncMock(return_value=response(status=401))
        connector.data_source.list_spaces = AsyncMock()

        with pytest.raises(DrupalWikiAuthError, match="rejected the personal access token"):
            await run_with_no_filters(connector)

        connector.notify.assert_awaited_once()
        assert connector.notify.await_args.kwargs["type"] == NotificationType.CONNECTOR_AUTH_ERROR
        # Nothing else is tried with a token the wiki has refused.
        connector.data_source.list_spaces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_401_on_one_group_is_not_skipped_like_a_failed_group(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([{"id": 5}])))
        connector.data_source.get_group = AsyncMock(return_value=response(status=401))

        with pytest.raises(DrupalWikiAuthError):
            await connector._sync_groups({})

    @pytest.mark.asyncio
    async def test_a_401_listing_attachments_stops_the_run(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(1)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response(status=401))

        with pytest.raises(DrupalWikiAuthError):
            await connector._sync_pages({12: {"name": "Team"}})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [
        response(status=403),
        non_json(status=400),
    ], ids=["403", "400-html-page"])
    async def test_a_live_wiki_rejecting_the_token_is_an_auth_failure(
        self, connector: DrupalWikiConnector, answer: MagicMock
    ) -> None:
        # A live wiki answers a token it does not accept with a 403 page, or a 400 page for
        # an unknown one, never a 401; a blocked user's token gives no access at all (node
        # 605), so listing spaces is refused too. The run stops with the auth notification,
        # before anything is removed.
        connector.notify = AsyncMock()
        connector.data_source.list_users = AsyncMock(return_value=answer)
        connector.data_source.list_spaces = AsyncMock(return_value=answer)
        connector._sync_spaces = AsyncMock(return_value={12: {"id": 12}})
        connector._sync_pages = AsyncMock()

        with pytest.raises(DrupalWikiAuthError):
            await run_with_no_filters(connector)

        types = [call.kwargs["type"] for call in connector.notify.await_args_list]
        assert types == [NotificationType.CONNECTOR_AUTH_ERROR]
        connector._sync_pages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_403_with_a_token_that_still_lists_spaces_is_one_failed_phase(
        self, connector: DrupalWikiConnector
    ) -> None:
        # A token whose user may not list people is not a revoked token (node 605: it reads
        # what its user may read): the people phase fails alone, and no auth alert is raised.
        connector.notify = AsyncMock()
        connector.data_source.list_users = AsyncMock(return_value=response(status=403))
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([])))
        connector._sync_spaces = AsyncMock(return_value={})

        await run_with_no_filters(connector)

        types = [call.kwargs["type"] for call in connector.notify.await_args_list]
        assert NotificationType.CONNECTOR_AUTH_ERROR not in types
        assert any("reading the list of people" in failure for failure in connector._failures)
        connector.data_source.list_spaces.assert_awaited_once_with(page=0, size=1)

    @pytest.mark.asyncio
    async def test_a_bad_request_the_api_words_as_json_is_not_an_auth_failure(
        self, connector: DrupalWikiConnector
    ) -> None:
        with pytest.raises(DrupalWikiSyncError):
            await connector._require_ok(response({"status": 400, "title": "Bad Request"}, status=400), "reading x")


class TestErrorsCarryTheWikisReason:
    """Every failure names the operation and says what the wiki answered."""

    PROBLEM_401 = {"status": 401, "title": "Unauthorized",
                   "detail": "Invalid or missing personal access token"}

    @pytest.mark.asyncio
    async def test_connection_test_says_why_the_token_was_rejected(
        self, connector: DrupalWikiConnector, caplog: pytest.LogCaptureFixture
    ) -> None:
        connector.client = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=response(self.PROBLEM_401, status=401))

        with pytest.raises(ConnectorInitError, match="Invalid or missing personal access token"):
            await connector.test_connection_and_access()

        assert "Invalid or missing personal access token (HTTP 401)" in caplog.text

    @pytest.mark.asyncio
    async def test_connection_test_says_why_spaces_failed(self, connector: DrupalWikiConnector) -> None:
        connector.client = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([])))
        connector.data_source.list_spaces = AsyncMock(return_value=response(
            {"status": 503, "title": "Service Unavailable", "detail": "Maintenance until 22:00"},
            status=503,
        ))

        with pytest.raises(ConnectorInitError, match="Maintenance until 22:00 \\(HTTP 503\\)"):
            await connector.test_connection_and_access()

    @pytest.mark.asyncio
    async def test_a_revoked_token_mid_sync_says_what_the_wiki_said(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.notify = AsyncMock()
        connector.data_source.list_users = AsyncMock(return_value=response(self.PROBLEM_401, status=401))

        with pytest.raises(DrupalWikiAuthError, match="reading the list of people"):
            await run_with_no_filters(connector)

        assert "Invalid or missing personal access token" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_a_failed_group_fetch_names_the_group_and_the_reason(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.data_source.list_groups = AsyncMock(return_value=response(spring_page([{"id": 5}])))
        connector.data_source.get_group = AsyncMock(return_value=response(
            {"status": 404, "title": "Not Found", "detail": "Group 5 does not exist"}, status=404,
        ))

        await connector._sync_groups({})

        assert connector._failures == [
            "Drupal Wiki refused reading the members of group 5: "
            "Not Found: Group 5 does not exist (HTTP 404)"
        ]

    @pytest.mark.asyncio
    async def test_a_page_that_cannot_be_built_says_why(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.data_source.list_pages = AsyncMock(return_value=response(spring_page([page_payload(1)])))
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))
        connector._build_page_records = AsyncMock(side_effect=KeyError("homeSpace"))

        await connector._sync_pages({12: {"name": "Team"}})

        assert connector._failures == [
            "Could not read the Drupal Wiki page 'Page 1' (KeyError: 'homeSpace'). "
            "It will be tried again on the next sync."
        ]

    @pytest.mark.asyncio
    async def test_opening_a_page_logs_the_wikis_reason(
        self, connector: DrupalWikiConnector, caplog: pytest.LogCaptureFixture
    ) -> None:
        connector.data_source.get_page = AsyncMock(return_value=response(
            {"status": 403, "title": "Forbidden", "detail": "No right to read node 733"}, status=403,
        ))

        with pytest.raises(HTTPException):
            await connector.stream_record(stored_record(733))

        assert "page 733 failed: Forbidden: No right to read node 733 (HTTP 403)" in caplog.text

    @pytest.mark.asyncio
    async def test_reindex_logs_the_wikis_reason(
        self, connector: DrupalWikiConnector, caplog: pytest.LogCaptureFixture
    ) -> None:
        connector.data_source.get_page = AsyncMock(return_value=response(
            {"status": 502, "title": "Bad Gateway", "detail": "Upstream wiki node down"}, status=502,
        ))

        await connector.reindex_records([stored_record(733)])

        assert "page:733" in caplog.text
        assert "Bad Gateway: Upstream wiki node down (HTTP 502)" in caplog.text

    @pytest.mark.asyncio
    async def test_filter_options_say_why_spaces_could_not_be_listed(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.data_source.list_spaces = AsyncMock(return_value=response(self.PROBLEM_401, status=401))

        result = await connector.get_filter_options("space_ids")

        assert result.success is False
        assert "Invalid or missing personal access token (HTTP 401)" in (result.message or "")

    @pytest.mark.asyncio
    async def test_no_log_line_carries_the_token(
        self, connector: DrupalWikiConnector, caplog: pytest.LogCaptureFixture
    ) -> None:
        connector.client = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=response(self.PROBLEM_401, status=401))

        with pytest.raises(ConnectorInitError):
            await connector.test_connection_and_access()

        assert "Bearer" not in caplog.text
        assert "pat:" not in caplog.text


def _extension_filter(operator: MultiselectOperator, values: list[str]) -> FilterCollection:
    return FilterCollection(
        filters=[
            Filter(
                key="file_extensions",
                value=values,
                type=FilterType.MULTISELECT,
                operator=operator,
            )
        ]
    )


ATTACHMENTS = [
    {"id": 1, "fileName": "a.zip", "lastModified": 1001},
    {"id": 2, "fileName": "b.pdf", "lastModified": 1001},
    {"id": 3, "fileName": "README", "lastModified": 1001},
    {"id": 4, "fileName": "C.PDF", "lastModified": 1001},
]


def _written(processor: MagicMock) -> list:
    return [r for call in processor.on_new_records.await_args_list for r, _ in call.args[0]]


class TestAttachmentExtensionFilter:
    def _stub_source(self, connector: DrupalWikiConnector) -> None:
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(733)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response(ATTACHMENTS))
        connector.data_source.get_attachment = AsyncMock()
        connector.data_source.download_attachment = MagicMock()

    def test_the_schema_has_the_filter_and_explains_it_is_for_attachments(self) -> None:
        fields = DrupalWikiConnector._connector_metadata["config"]["filters"]["sync"]["schema"]["fields"]
        field = next(f for f in fields if f["name"] == "file_extensions")
        assert "attachments only" in field["description"]
        ids = [o.id for o in field["options"]]
        assert {"pdf", "zip", "db"} <= set(ids)
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_not_in_skips_a_zip_without_a_record_or_a_download(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self._stub_source(connector)
        connector.sync_filters = _extension_filter(MultiselectOperator.NOT_IN, ["zip"])

        await connector._sync_pages({12: {"id": 12}})

        written = _written(processor)
        assert [r.external_record_id for r in written] == [
            "page:733", "attachment:2", "attachment:3", "attachment:4",
        ]
        connector.data_source.download_attachment.assert_not_called()
        connector.data_source.get_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_in_keeps_only_the_listed_types_and_never_filters_pages(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self._stub_source(connector)
        connector.sync_filters = _extension_filter(MultiselectOperator.IN, ["pdf"])

        await connector._sync_pages({12: {"id": 12}})

        written = _written(processor)
        # The page has no extension of its own and is still written; a file with
        # no extension is dropped by In; matching ignores case.
        assert [r.external_record_id for r in written] == [
            "page:733", "attachment:2", "attachment:4",
        ]
        connector.data_source.download_attachment.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_file_without_an_extension_is_kept_by_not_in(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self._stub_source(connector)
        connector.sync_filters = _extension_filter(MultiselectOperator.NOT_IN, ["pdf", "zip"])

        await connector._sync_pages({12: {"id": 12}})

        assert [r.external_record_id for r in _written(processor)] == ["page:733", "attachment:3"]

    @pytest.mark.asyncio
    async def test_with_no_filter_every_attachment_is_synced(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self._stub_source(connector)

        await connector._sync_pages({12: {"id": 12}})

        assert [r.external_record_id for r in _written(processor)] == [
            "page:733", "attachment:1", "attachment:2", "attachment:3", "attachment:4",
        ]

    @pytest.mark.asyncio
    async def test_matching_ignores_case_and_a_leading_dot(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(733)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 1, "fileName": "A.ZIP"},
            {"id": 2, "fileName": "b.Zip"},
            {"id": 3, "fileName": "c.pdf"},
        ]))
        connector.sync_filters = _extension_filter(MultiselectOperator.NOT_IN, [".ZIP"])

        await connector._sync_pages({12: {"id": 12}})

        assert [r.external_record_id for r in _written(processor)] == ["page:733", "attachment:3"]

    @pytest.mark.asyncio
    async def test_the_file_name_wins_over_name_and_name_is_the_fallback(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(733)]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 1, "name": "only-name.zip"},
            {"id": 2, "fileName": "report", "name": "report.zip"},
            {"id": 3},
        ]))
        connector.sync_filters = _extension_filter(MultiselectOperator.NOT_IN, ["zip"])

        await connector._sync_pages({12: {"id": 12}})

        # 1 is dropped via its name; 2 has no extension by fileName, as its own record does.
        assert [r.external_record_id for r in _written(processor)] == [
            "page:733", "attachment:2", "attachment:3",
        ]

    @pytest.mark.asyncio
    async def test_every_listing_page_of_attachments_is_filtered(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        total = 2 * PAGE_SIZE + 7
        everything = [
            {"id": i, "fileName": f"f{i}.{'zip' if i % 3 == 0 else 'pdf'}"}
            for i in range(1, total + 1)
        ]
        connector.data_source.list_pages = AsyncMock(
            return_value=response(spring_page([page_payload(733)]))
        )

        async def listing(page_id: int, page: int, size: int) -> MagicMock:
            return response(everything[page * size:(page + 1) * size])

        connector.data_source.list_attachments = AsyncMock(side_effect=listing)
        connector.data_source.download_attachment = MagicMock()
        connector.sync_filters = _extension_filter(MultiselectOperator.NOT_IN, ["zip"])

        await connector._sync_pages({12: {"id": 12}})

        kept = {r.external_record_id for r in _written(processor) if r.external_record_id.startswith("attachment:")}
        assert kept == {f"attachment:{i}" for i in range(1, total + 1) if i % 3 != 0}
        connector.data_source.download_attachment.assert_not_called()


# ---------------------------------------------------------------------------
# Drupal Wiki 8.44 refuses the GraphQL queries for a personal access token
# ---------------------------------------------------------------------------

def refused(operation: str = "RoleMemberManagerData") -> DrupalWikiGraphQLRefusedError:
    return DrupalWikiGraphQLRefusedError(
        f"{operation} failed: QUERY_NOT_ALLOWED: QueryNotAllowed (HTTP 400)"
    )


PRIVATE_HR = {"id": 12, "name": "HR", "type": "BASIC", "accessStatus": "PRIVATE"}
OPEN_TEAM = {"id": 3, "name": "Team", "type": "BASIC", "accessStatus": "AUTHENTICATED"}


def wiki_with_spaces(connector: DrupalWikiConnector, *spaces: dict) -> None:
    """A wiki with these spaces, one page each (page id = 100 + space id), no attachments."""
    async def users() -> dict:
        connector._enabled_emails = {"alice@example.com"}
        return {}

    async def groups(_users: dict) -> None:
        return None

    # The sync points, kept across runs.
    points: dict[str, dict] = {}

    async def read_sync_point(key: str) -> dict:
        return dict(points.get(key, {}))

    async def update_sync_point(key: str, data: dict) -> None:
        points[key] = dict(data)

    connector.record_sync_point.read_sync_point = AsyncMock(side_effect=read_sync_point)
    connector.record_sync_point.update_sync_point = AsyncMock(side_effect=update_sync_point)
    connector._sync_users = users
    connector._sync_groups = groups
    connector.notify = AsyncMock()
    connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page(list(spaces))))
    connector.data_source.list_pages = AsyncMock(side_effect=lambda space_id, **_: response(
        spring_page([page_payload(100 + space_id, space_id=space_id)])
    ))
    connector.data_source.list_attachments = AsyncMock(return_value=response([]))


def written_pages(processor: MagicMock) -> dict[str, tuple[Record, list[Permission]]]:
    return {
        record.external_record_id: (record, permissions)
        for call in processor.on_new_records.await_args_list
        for record, permissions in call.args[0]
    }


def written_groups(processor: MagicMock) -> dict[str, list[Permission]]:
    return {
        group.external_group_id: permissions
        for call in processor.on_new_record_groups.await_args_list
        for group, permissions in call.args[0]
    }


class TestConnectionTestProbesGraphQL:
    """T2: the connection test asks the page structure query about one space it can see."""

    @staticmethod
    def rest_ok(connector: DrupalWikiConnector) -> None:
        connector.client = MagicMock()
        connector._graphql_probe = lambda: (connector.graphql, AsyncMock())
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([{"id": 1}])))
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([OPEN_TEAM])))

    @pytest.mark.asyncio
    async def test_a_refusal_passes_the_test_with_a_warning(self, connector: DrupalWikiConnector) -> None:
        self.rest_ok(connector)
        connector.graphql.get_space_tree = AsyncMock(side_effect=refused("SpaceContentStructure"))

        assert await connector.test_connection_and_access() is True

        assert [item.code for item in connector.last_action_items] == [PAGE_STRUCTURE_REFUSED_ACTION_CODE]
        connector.graphql.get_space_tree.assert_awaited_once_with(3)
        connector.graphql.get_space_members.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_answered_queries_clear_the_warning(self, connector: DrupalWikiConnector) -> None:
        self.rest_ok(connector)
        connector.graphql.get_space_members = AsyncMock(return_value={"users": [], "groups": []})
        connector.graphql.get_space_tree = AsyncMock(return_value=[])

        assert await connector.test_connection_and_access() is True

        assert connector.last_action_items == []

    @pytest.mark.asyncio
    async def test_a_failure_that_may_pass_leaves_the_card_as_it_is(
        self, connector: DrupalWikiConnector
    ) -> None:
        self.rest_ok(connector)
        connector.graphql.get_space_members = AsyncMock(side_effect=DrupalWikiGraphQLTransientError("502"))
        connector.graphql.get_space_tree = AsyncMock(side_effect=DrupalWikiGraphQLTransientError("502"))

        assert await connector.test_connection_and_access() is True

        assert connector.last_action_items is None

    @pytest.mark.asyncio
    async def test_a_query_that_does_not_answer_in_time_leaves_the_card_as_it_is(
        self, connector: DrupalWikiConnector
    ) -> None:
        self.rest_ok(connector)

        async def hang(_space_id: int) -> dict:
            await asyncio.sleep(3600)
            return {}

        connector.graphql.get_space_tree = AsyncMock(side_effect=hang)

        with patch("app.connectors.sources.drupal_wiki.connector.GRAPHQL_PROBE_TIMEOUT_S", 0.2):
            assert await connector.test_connection_and_access() is True

        assert connector.last_action_items is None

    @pytest.mark.asyncio
    async def test_a_rest_failure_still_fails_without_asking_graphql(
        self, connector: DrupalWikiConnector
    ) -> None:
        connector.client = MagicMock()
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([])))
        connector.data_source.list_spaces = AsyncMock(return_value=response(status=500))

        with pytest.raises(ConnectorInitError, match="could not list spaces"):
            await connector.test_connection_and_access()

        connector.graphql.get_space_tree.assert_not_awaited()


class TestTreeRefused:
    """T5: a refused page tree keeps stored parents and is reported once for every space."""

    @pytest.mark.asyncio
    async def test_pages_keep_their_parents_and_one_line_names_every_space(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        spaces = [{"id": i, "name": f"S{i}", "type": "BASIC", "accessStatus": "AUTHENTICATED"} for i in (3, 4, 5)]
        wiki_with_spaces(connector, *spaces)
        connector.graphql.get_space_tree = AsyncMock(side_effect=refused("SpaceContentStructure"))
        processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda _, external_id: stored_record(int(external_id.split(":")[1]), parent="page:700")
        )

        await run_with_no_filters(connector)

        pages = written_pages(processor)
        assert {pages[f"page:{100 + i}"][0].parent_external_record_id for i in (3, 4, 5)} == {"page:700"}
        connector.notify.assert_awaited_once()
        message = connector.notify.await_args.kwargs["message"]
        assert message == "- " + TREE_UNREADABLE.message.format(spaces="'S3', 'S4', 'S5'")
        assert "1 problem" in connector.notify.await_args.kwargs["title"]
        # The structure says nothing about which records exist, so nothing is held back.
        assert connector.full_sync_coverage().complete is True
        assert len(page_checkpoint_writes(connector)) == 3


class TestTreeFailuresThatMayPass:
    """A tree that fails without a refusal is an error every run; a refusal is a warning
    sent once. Either way the stored parents stay."""

    @staticmethod
    def wiki_with_parents(connector: DrupalWikiConnector, processor: MagicMock) -> None:
        wiki_with_spaces(connector, OPEN_TEAM)
        processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda _, external_id: stored_record(int(external_id.split(":")[1]), parent="page:700")
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [
        DrupalWikiGraphQLAuthError("SpaceContentStructure failed (HTTP 401)"),
        DrupalWikiGraphQLTransientError("SpaceContentStructure failed (HTTP 502)"),
    ])
    async def test_an_auth_or_transient_failure_is_an_error_every_run(
        self, connector: DrupalWikiConnector, processor: MagicMock, error: Exception
    ) -> None:
        self.wiki_with_parents(connector, processor)
        connector.graphql.get_space_tree = AsyncMock(side_effect=error)

        for _ in range(2):
            await run_with_no_filters(connector)
            assert written_pages(processor)["page:103"][0].parent_external_record_id == "page:700"
            processor.on_new_records.reset_mock()

        assert [
            (call.kwargs["type"], call.kwargs["severity"]) for call in connector.notify.await_args_list
        ] == [(NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR)] * 2
        assert "page structure of the Drupal Wiki space 'Team'" in connector.notify.await_args.kwargs["message"]
        # The pages are read again next run, when the tree may answer.
        assert page_checkpoint_writes(connector) == []

    @pytest.mark.asyncio
    async def test_a_refusal_is_one_warning_over_runs(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki_with_parents(connector, processor)
        connector.graphql.get_space_tree = AsyncMock(side_effect=refused("SpaceContentStructure"))

        for _ in range(2):
            await run_with_no_filters(connector)
            assert written_pages(processor)["page:103"][0].parent_external_record_id == "page:700"
            processor.on_new_records.reset_mock()

        connector.notify.assert_awaited_once()
        assert connector.notify.await_args.kwargs["severity"] == NotificationSeverity.WARNING
        assert connector.notify.await_args.kwargs["message"] == "- " + TREE_UNREADABLE.message.format(spaces="'Team'")


class TestTheConnectionTestProbeIsBounded:
    """QA-P1-2: the GraphQL check only adds a warning, so a wiki that hangs on it must not
    hold the connection test for the sync's retries and timeouts."""

    @pytest.mark.asyncio
    async def test_a_hanging_wiki_still_passes_the_test_quickly(self, connector: DrupalWikiConnector) -> None:
        TestConnectionTestProbesGraphQL.rest_ok(connector)
        closer = AsyncMock()
        connector._graphql_probe = lambda: (connector.graphql, closer)

        async def hang(_space_id: int) -> dict:
            await asyncio.sleep(3600)
            return {}

        connector.graphql.get_space_members = AsyncMock(side_effect=hang)
        connector.graphql.get_space_tree = AsyncMock(side_effect=hang)

        with patch("app.connectors.sources.drupal_wiki.connector.GRAPHQL_PROBE_TIMEOUT_S", 0.2):
            assert await asyncio.wait_for(connector.test_connection_and_access(), timeout=5) is True

        assert connector.last_action_items is None
        closer.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_test_asks_the_probe_client_not_the_syncs_own(
        self, connector: DrupalWikiConnector
    ) -> None:
        TestConnectionTestProbesGraphQL.rest_ok(connector)
        probe = MagicMock()
        probe.get_space_tree = AsyncMock(side_effect=refused("SpaceContentStructure"))
        probe_rest = AsyncMock()
        connector._graphql_probe = lambda: (probe, probe_rest)
        # The sync's client retries for minutes; the test must never reach it.
        connector.graphql.get_space_tree = AsyncMock(side_effect=AssertionError("sync client used"))

        assert await connector.test_connection_and_access() is True

        probe.get_space_tree.assert_awaited_once_with(3)
        connector.graphql.get_space_tree.assert_not_awaited()
        probe_rest.close.assert_awaited_once()
        assert [item.code for item in connector.last_action_items] == [PAGE_STRUCTURE_REFUSED_ACTION_CODE]

    def test_the_probe_tries_once_with_a_short_timeout(self, connector: DrupalWikiConnector) -> None:
        connector.client = MagicMock()
        connector.client.get_client.return_value = DrupalWikiRESTClientViaToken(
            BASE_URL, "pat:secret", resilience=MagicMock()
        )

        _, probe_rest = connector._graphql_probe()

        assert probe_rest.resilience is None
        assert probe_rest.timeout == GRAPHQL_PROBE_TIMEOUT_S <= 10
        assert (probe_rest.get_base_url(), probe_rest.get_token()) == (BASE_URL, "pat:secret")


class TestProtectedSpacesAreAnnouncedOnce:
    """A protected space whose members the wiki refuses to list is a steady state: it is
    announced when it starts or changes, as a warning, and real failures still notify every run."""

    @pytest.fixture(autouse=True)
    def members_refused(self, connector: DrupalWikiConnector) -> None:
        connector.graphql.get_space_members = AsyncMock(side_effect=DrupalWikiGraphQLRefusedError("refused"))

    @pytest.mark.asyncio
    async def test_three_runs_send_one_warning(self, connector: DrupalWikiConnector) -> None:
        wiki_with_spaces(connector, PRIVATE_HR)

        for _ in range(3):
            await run_with_no_filters(connector)

        connector.notify.assert_awaited_once()
        assert connector.notify.await_args.kwargs["severity"] == NotificationSeverity.WARNING
        assert connector.notify.await_args.kwargs["type"] == NotificationType.CONNECTOR_WARNING
        assert "'HR'" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_a_newly_affected_space_is_announced(self, connector: DrupalWikiConnector) -> None:
        wiki_with_spaces(connector, PRIVATE_HR)
        await run_with_no_filters(connector)

        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([
            PRIVATE_HR, {**PRIVATE_HR, "id": 13, "name": "Legal"},
        ])))
        await run_with_no_filters(connector)

        assert connector.notify.await_count == 2
        assert "'Legal'" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_a_failure_still_notifies_every_run_as_an_error(self, connector: DrupalWikiConnector) -> None:
        wiki_with_spaces(connector, PRIVATE_HR, OPEN_TEAM)
        connector.data_source.list_attachments = AsyncMock(return_value=response(status=500))

        for _ in range(2):
            await run_with_no_filters(connector)

        assert [
            (call.kwargs["type"], call.kwargs["severity"]) for call in connector.notify.await_args_list
        ] == [(NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR)] * 2


ORG_GRANT = Permission(external_id=None, email=None, type=PermissionType.READ, entity_type=EntityType.ORG)


def keys(permissions: list[Permission]) -> list[tuple[str, str | None]]:
    """What a grant names, without the timestamps a Permission carries."""
    return [(p.entity_type.value, p.external_id) for p in permissions]


class TestSharedPages:
    """A page shared into a space is readable there (handbook node 461), its sub-pages are not.

    Space 12 (protected) is the home of page 1; 13 is open; 14 and 15 are protected. Only an
    open space has readers PipesHub can name.
    """

    @staticmethod
    def wiki(connector: DrupalWikiConnector, processor: MagicMock, listed: dict[int, object],
             shares: dict | None = None, settled: tuple[int, ...] = (12,), stored_open: bool = False) -> dict:
        checkpoints: dict[str, dict] = {"shares/space/all": {"pages": json.dumps(shares or {})}}
        for space in settled:
            checkpoints[f"WEBPAGE/space_pages/{space}"] = {"last_modified": 5000}
        connector.record_sync_point.read_sync_point = AsyncMock(side_effect=lambda key: dict(checkpoints.get(key, {})))
        connector.record_sync_point.update_sync_point = AsyncMock(
            side_effect=lambda key, payload: checkpoints.__setitem__(key, dict(payload))
        )
        connector.data_source.list_pages = AsyncMock(
            side_effect=lambda space_id, **_: listed[space_id] if isinstance(listed[space_id], MagicMock)
            else response(spring_page(listed[space_id]))
        )
        connector.data_source.list_attachments = AsyncMock(return_value=response([]))

        def page_one() -> Record:
            record = stored_record(1)
            if stored_open:
                record.access_rule = AccessRule.OPEN
            return record

        processor.get_records_in_record_group = AsyncMock(
            side_effect=lambda _c, space, *_a, **_k: [page_one()] if space == "12" else []
        )
        processor.get_record_by_external_id = AsyncMock(side_effect=lambda _c, ext: page_one() if ext == "page:1" else None)
        connector._space_status = {12: "PRIVATE", 13: "AUTHENTICATED", 14: "PRIVATE", 15: "PRIVATE"}
        return checkpoints

    @staticmethod
    def rewrites(processor: MagicMock) -> list[tuple[str, str, bool, list]]:
        return [
            (c.args[0].external_record_id, c.args[0].access_rule.value, c.args[0].inherit_permissions, keys(c.args[1]))
            for c in processor.on_updated_record_permissions.await_args_list
        ]

    @staticmethod
    def page_writes(processor: MagicMock, external_id: str) -> list[tuple[str, bool, list, bool]]:
        return [
            (r.access_rule.value, r.inherit_permissions, keys(p), r.rewrite_permissions)
            for c in processor.on_new_records.await_args_list for r, p in c.args[0] if r.external_record_id == external_id
        ]

    @staticmethod
    def memory(checkpoints: dict) -> dict:
        return json.loads(checkpoints["shares/space/all"]["pages"])

    PAGE = page_payload(1, space_id=12, last_modified=1000)
    SPACES = {12: {"id": 12}, 13: {"id": 13}, 14: {"id": 14}, 15: {"id": 15}}

    def test_pages_are_strict_so_a_grant_on_one_does_not_open_its_sub_pages(
        self, connector: DrupalWikiConnector
    ) -> None:
        record = connector._build_page_record(page_payload(733), parent_page_id=700)
        assert record.parent_external_record_id == "page:700"
        assert record.inherit_permissions is True
        assert record.access_rule.value == "STRICT"

    @pytest.mark.asyncio
    async def test_a_page_shared_into_an_open_space_is_granted_to_the_org(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        checkpoints = self.wiki(connector, processor, {12: [self.PAGE], 13: [self.PAGE], 14: [], 15: []})

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == [("page:1", "OPEN", False, keys([ORG_GRANT]))]
        processor.on_new_records.assert_not_awaited()  # its home is unchanged
        assert self.memory(checkpoints)["1"]["spaces"] == [13]

    @pytest.mark.asyncio
    async def test_a_page_shared_only_into_protected_spaces_stays_plain(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        checkpoints = self.wiki(connector, processor, {12: [self.PAGE], 13: [], 14: [self.PAGE], 15: []})

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert self.memory(checkpoints)["1"] == {"spaces": [14], "grants": "", "open": []}

    @pytest.mark.asyncio
    async def test_a_page_shared_into_a_protected_space_reaches_its_members_until_unshared(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Members of a space a page is shared into see it (node 461). Not inheriting, the page
        # carries its home space's members as well; a change of the target's members rewrites it.
        alice = Permission(external_id=None, email="alice@example.com", type=PermissionType.READ, entity_type=EntityType.USER)
        group = lambda gid: Permission(external_id=gid, email=None, type=PermissionType.READ, entity_type=EntityType.GROUP)  # noqa: E731
        listed = {12: [self.PAGE], 13: [], 14: [self.PAGE], 15: []}
        checkpoints = self.wiki(connector, processor, listed)
        connector._space_grants = {12: [alice], 13: [ORG_GRANT], 14: [group("7")], 15: []}

        await connector._sync_pages(self.SPACES)
        assert self.rewrites(processor) == [("page:1", "OPEN", False, keys([alice, group("7")]))]

        processor.on_updated_record_permissions.reset_mock()
        shares = self.memory(checkpoints)
        checkpoints = self.wiki(connector, processor, listed, shares=shares, stored_open=True)
        connector._space_grants[14] = [group("8")]
        await connector._sync_pages(self.SPACES)
        assert self.rewrites(processor) == [("page:1", "OPEN", False, keys([alice, group("8")]))]

        processor.on_updated_record_permissions.reset_mock()
        shares = self.memory(checkpoints)
        listed[14] = []
        self.wiki(connector, processor, listed, shares=shares, stored_open=True)
        await connector._sync_pages(self.SPACES)
        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]

    @pytest.mark.asyncio
    async def test_an_unchanged_share_is_not_written_again_whoever_its_spaces_members_are(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Page 2 is the newest, which each run writes again (the checkpoint stands a
        # second back); page 1 is not.
        newest = page_payload(2, space_id=12, last_modified=6000)
        checkpoints = self.wiki(connector, processor, {12: [self.PAGE, newest], 13: [self.PAGE], 14: [], 15: []})
        await connector._sync_pages(self.SPACES)
        processor.on_updated_record_permissions.reset_mock()
        processor.get_record_by_external_id.reset_mock()
        written_open = stored_record(1)
        written_open.access_rule = AccessRule.OPEN
        processor.get_records_in_record_group = AsyncMock(
            side_effect=lambda _c, space, *_a, **_k: [written_open] if space == "12" else []
        )

        await connector._sync_pages(self.SPACES)

        processor.on_updated_record_permissions.assert_not_awaited()
        assert "1" in self.memory(checkpoints)
        # Nor read back from the graph: its home space listed it.
        assert "page:1" not in [c.args[1] for c in processor.get_record_by_external_id.await_args_list]

    @pytest.mark.asyncio
    async def test_an_unshared_page_goes_back_to_its_space(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Unsharing need not change lastModified; the wiki says it only by no longer
        # listing the page in that space.
        had = {"spaces": [13], "grants": _grants_digest([ORG_GRANT]), "open": [13]}
        checkpoints = self.wiki(
            connector, processor, {12: [self.PAGE], 13: [], 14: [], 15: []}, shares={"1": had}, stored_open=True,
        )

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]
        assert self.memory(checkpoints) == {}

    @pytest.mark.asyncio
    async def test_a_shared_page_is_unshared_even_when_the_share_memory_was_lost(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The memory was not saved after the page was written shared. The graph still
        # records it (the page is stored OPEN), so an unshare the listing shows is applied.
        self.wiki(connector, processor, {12: [self.PAGE], 13: [], 14: [], 15: []}, stored_open=True)

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]

    @pytest.mark.asyncio
    async def test_a_shared_page_is_unshared_even_when_the_share_memory_is_stale(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The memory still says plain (shared into protected 14 only): it was not saved after
        # a share into open 13 wrote the page OPEN. That share is gone now, so the page is
        # written plain again, not left with the org-wide grant.
        had = {"spaces": [14], "grants": ""}
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: [], 14: [self.PAGE], 15: []},
            shares={"1": had}, stored_open=True,
        )

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]

    @pytest.mark.asyncio
    async def test_a_full_sync_that_cannot_list_an_open_space_keeps_a_shared_pages_stored_grants(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # A full sync cleared the share memory and every checkpoint; open space 13, which may
        # still share the page stored OPEN, cannot be listed. Its stored grants stay as they
        # are this run, and the page is reported until a listing decides it.
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: response(status=500), 14: [], 15: []},
            settled=(), stored_open=True,
        )
        processor.inheritance_when_unreadable = AsyncMock(return_value=False)  # written shared: no inherit edge

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert self.page_writes(processor, "page:1") == []
        [kept] = [c.args[0] for c in processor.on_record_content_update.await_args_list]
        assert (kept.external_record_id, kept.access_rule.value, kept.inherit_permissions) == ("page:1", "OPEN", False)
        assert [f for f in connector._failures if "page 1" in f]

    @pytest.mark.asyncio
    async def test_a_kept_page_keeps_the_inherit_edge_it_is_stored_with(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Home 12 is open and listed; the page is stored OPEN (the record default) and reads
        # only through its inherit edge. Open space 13 cannot be listed: the page is kept,
        # and keeping it must not drop that edge.
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: response(status=500), 14: [], 15: []},
            settled=(), stored_open=True,
        )
        connector._space_status[12] = "AUTHENTICATED"
        processor.inheritance_when_unreadable = AsyncMock(return_value=True)

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        [kept] = [c.args[0] for c in processor.on_record_content_update.await_args_list]
        assert (kept.external_record_id, kept.access_rule.value, kept.inherit_permissions) == ("page:1", "OPEN", True)
        processor.inheritance_when_unreadable.assert_awaited_once_with("records", stored_record(1).id)

    @pytest.mark.asyncio
    async def test_a_full_sync_that_lists_every_open_space_unshares_a_page_no_longer_shared(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The same full sync with open space 13 listed: the share is gone, so the page is plain.
        self.wiki(connector, processor, {12: [self.PAGE], 13: [], 14: [], 15: []}, settled=(), stored_open=True)

        await connector._sync_pages(self.SPACES)

        writes = [
            (r.access_rule.value, r.inherit_permissions, keys(p))
            for name, args, _ in processor.mock_calls
            for r, p in (args[0] if name == "on_new_records" else [args] if name == "on_updated_record_permissions" else [])
            if r.external_record_id == "page:1"
        ]
        assert writes[-1] == ("STRICT", True, [])
        assert connector._failures == []

    @pytest.mark.asyncio
    async def test_a_share_into_a_space_that_did_not_list_stays(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        had = {"spaces": [13], "grants": _grants_digest([ORG_GRANT]), "open": [13]}
        checkpoints = self.wiki(
            connector, processor, {12: [self.PAGE], 13: response(status=500), 14: [], 15: []}, shares={"1": had},
            stored_open=True,
        )

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert self.memory(checkpoints) == {"1": had}

    @pytest.mark.asyncio
    async def test_an_unshare_from_the_open_space_leaves_the_page_plain(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Still shared into protected 14, whose readers nobody can name: the org-wide grant
        # the open space gave goes.
        had = {"spaces": [13, 14], "grants": _grants_digest([ORG_GRANT])}
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: [], 14: [self.PAGE], 15: []}, shares={"1": had}, stored_open=True,
        )

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]

    @pytest.mark.asyncio
    async def test_a_first_pass_that_misses_a_stored_page_does_not_settle_its_space(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The first pass after upgrade: page 2 (stored, maybe still inheriting from the
        # page above it) slips out of the offset listing. The space gets no checkpoint and
        # page 1 no shared grants, which page 2 could otherwise reach.
        self.wiki(connector, processor, {12: [self.PAGE], 13: [self.PAGE], 14: [], 15: []}, settled=())
        processor.get_records_in_record_group = AsyncMock(
            side_effect=lambda _c, space, *_a, **_k: [stored_record(1), stored_record(2)] if space == "12" else []
        )
        connector.data_source.get_page = AsyncMock(return_value=response(page_payload(2, space_id=12)))

        await connector._sync_pages(self.SPACES)

        assert "WEBPAGE/space_pages/12" not in [key for key, _ in page_checkpoint_writes(connector)]
        assert [w for w in self.page_writes(processor, "page:1") if w[0] == "OPEN"] == []
        assert self.rewrites(processor) == []

    @pytest.mark.asyncio
    async def test_a_share_waits_until_every_home_space_page_has_access_rule_strict(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # A space with no checkpoint has not had all its pages written STRICT yet; a
        # sub-page still OPEN under a page with its own grants would be read through it.
        self.wiki(connector, processor, {12: [self.PAGE, page_payload(2)], 13: [self.PAGE], 14: [], 15: []}, settled=())
        connector.data_source.list_attachments = AsyncMock(side_effect=[RuntimeError("boom"), response([])])

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert [w for w in self.page_writes(processor, "page:1") if w[0] == "OPEN"] == []

    @pytest.mark.asyncio
    async def test_a_shared_page_its_home_pass_writes_again_stays_shared(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Edited after it was shared: the home pass writes it before the share pass runs,
        # and must not write it plain, or nothing would put the other space's readers back.
        changed = page_payload(1, space_id=12, last_modified=9000)
        self.wiki(connector, processor, {12: [changed], 13: [changed], 14: [], 15: []},
                  shares={"1": {"spaces": [13], "grants": _grants_digest([ORG_GRANT]), "open": [13]}}, stored_open=True)

        await connector._sync_pages(self.SPACES)

        assert self.page_writes(processor, "page:1") == [("OPEN", False, keys([ORG_GRANT]), True)]
        processor.on_updated_record_permissions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_shared_page_the_removal_pass_writes_again_stays_shared(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The listing of its home space skipped it; the page written again after the
        # share pass keeps the other space's readers.
        self.wiki(connector, processor, {12: [], 13: [self.PAGE], 14: [], 15: []})
        connector.data_source.get_page = AsyncMock(return_value=response(self.PAGE))

        await connector._sync_pages(self.SPACES)

        assert self.page_writes(processor, "page:1") == [("OPEN", False, keys([ORG_GRANT]), True)]
        processor.delete_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_plain_page_is_written_strict_with_its_grants_replaced(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: [page_payload(2, last_modified=9000)], 13: [], 14: [], 15: []})

        await connector._sync_pages(self.SPACES)

        assert self.page_writes(processor, "page:2") == [("STRICT", True, [], True)]

    @pytest.mark.asyncio
    async def test_a_reindexed_page_keeps_the_inherit_edge_it_is_stored_with(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Page 1 is shared (OPEN, no edge); page 3 is OPEN only as the record default and reads
        # through its edge; page 2 is plain. A loaded record's inherit_permissions is the model
        # default, so the edge decides.
        shared, plain, legacy = stored_record(1), stored_record(2), stored_record(3)
        shared.access_rule = legacy.access_rule = AccessRule.OPEN
        shared.inherit_permissions = plain.inherit_permissions = legacy.inherit_permissions = True
        connector.data_source.get_page = AsyncMock(side_effect=lambda page_id: response(page_payload(page_id, last_modified=2000)))
        processor.inheritance_when_unreadable = AsyncMock(side_effect=lambda _c, stored_id: stored_id != shared.id)

        await connector.reindex_records([shared, plain, legacy])

        written = {r.external_record_id: r.inherit_permissions for r, _ in processor.on_new_records.await_args.args[0]}
        assert written == {"page:1": False, "page:2": True, "page:3": True}

    @pytest.mark.asyncio
    async def test_a_space_with_a_kept_page_is_not_settled(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # First sync after upgrade. Page 2, a sub-page of page 1, is stored OPEN as the record
        # default and kept with its inherit edge to page 1, because open 15 cannot be listed.
        # Page 1 is shared into open 13: granting it now would hand page 2 the org through
        # that edge, so space 12 is not settled and gets no checkpoint.
        self.wiki(connector, processor, {12: [self.PAGE, page_payload(2)], 13: [self.PAGE], 14: [],
                                         15: response(status=500)}, settled=())
        connector._space_status[15] = "AUTHENTICATED"
        legacy = stored_record(2, parent="page:1")
        legacy.access_rule = AccessRule.OPEN
        processor.get_records_in_record_group = AsyncMock(
            side_effect=lambda _c, space, *_a, **_k: [stored_record(1), legacy] if space == "12" else []
        )
        processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda _c, ext: {"page:1": stored_record(1), "page:2": legacy}.get(ext)
        )
        processor.inheritance_when_unreadable = AsyncMock(return_value=True)

        await connector._sync_pages(self.SPACES)

        assert [c.args[0].external_record_id for c in processor.on_record_content_update.await_args_list] == ["page:2"]
        assert [r for r in self.rewrites(processor) if r[1] == "OPEN"] == []
        assert [w for w in self.page_writes(processor, "page:1") if w[0] == "OPEN"] == []
        assert "WEBPAGE/space_pages/12" not in [key for key, _ in page_checkpoint_writes(connector)]

    @pytest.mark.asyncio
    async def test_a_moved_page_is_decided_by_the_home_listed_now_not_the_remembered_one(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Page 1 moved from open 13 into protected 12 and stays shared into open 16; it was
        # written there with the org, but the memory was not saved and still names 13. 12
        # lists it unchanged. Read as still in open 13, its grant would be taken away.
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: [], 14: [], 15: [], 16: [self.PAGE]},
            shares={"1": {"home": 13, "spaces": [16], "grants": _grants_digest([ORG_GRANT])}}, stored_open=True,
        )
        connector._space_status[16] = "AUTHENTICATED"

        await connector._sync_pages({**self.SPACES, 16: {"id": 16}})

        assert self.rewrites(processor) == []

    @pytest.mark.asyncio
    async def test_a_share_is_put_back_on_a_page_stored_plain_whatever_the_memory_says(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # An unshare wrote the page STRICT, but the memory was not saved and still holds the
        # org grant. The wiki shares it into open 13 again without editing it.
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: [self.PAGE], 14: [], 15: []},
            shares={"1": {"spaces": [13], "grants": _grants_digest([ORG_GRANT]), "open": [13]}},
        )

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == [("page:1", "OPEN", False, keys([ORG_GRANT]))]

    @pytest.mark.asyncio
    async def test_a_share_into_a_space_the_listing_could_not_identify_keeps_its_grant(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Space 17 came back as a row without an id: it may still share the page, so the grant
        # the page holds stays. With a listing that named every space, 17 is gone and it goes.
        had = {"spaces": [17], "grants": _grants_digest([ORG_GRANT]), "open": [17]}
        checkpoints = self.wiki(connector, processor, {12: [self.PAGE], 13: [], 14: [], 15: []},
                                shares={"1": had}, stored_open=True)
        connector._space_listing_complete = False
        connector._spaces_named = {12, 13, 14, 15}

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert self.memory(checkpoints) == {"1": had}

        self.wiki(connector, processor, {12: [self.PAGE], 13: [], 14: [], 15: []}, shares={"1": had}, stored_open=True)
        connector._space_listing_complete = True
        await connector._sync_pages(self.SPACES)
        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]

    @pytest.mark.asyncio
    async def test_an_unidentified_space_keeps_only_a_grant_it_justified(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Shared into open 13 (which gave the grant) and protected 14. 13 lists it no more, and
        # 14 came back as a row without an id: 14 never gave the grant, so it does not keep it.
        had = {"spaces": [13, 14], "grants": _grants_digest([ORG_GRANT]), "open": [13]}
        self.wiki(connector, processor, {12: [self.PAGE], 13: [], 15: []}, shares={"1": had}, stored_open=True)
        del connector._space_status[14]
        connector._space_listing_complete = False
        connector._spaces_named = {12, 13, 15}

        await connector._sync_pages({12: {"id": 12}, 13: {"id": 13}, 15: {"id": 15}})

        assert self.rewrites(processor) == [("page:1", "STRICT", True, [])]

    @pytest.mark.asyncio
    async def test_a_page_whose_home_space_the_listing_could_not_identify_is_left_as_it_is(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Page 1 is stored under 18, which came back as a row without an id; its space pass
        # did not run, so nothing decides its access this run.
        had = {"spaces": [13], "grants": _grants_digest([ORG_GRANT]), "open": [13]}
        checkpoints = self.wiki(connector, processor, {12: [], 13: [], 14: [], 15: []}, shares={"1": had})
        moved = stored_record(1, space_id=18)
        moved.access_rule = AccessRule.OPEN
        processor.get_records_in_record_group = AsyncMock(return_value=[])
        processor.get_record_by_external_id = AsyncMock(side_effect=lambda _c, ext: moved if ext == "page:1" else None)
        connector._space_listing_complete = False
        connector._spaces_named = {12, 13, 14, 15}

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert self.memory(checkpoints) == {"1": had}

    @pytest.mark.asyncio
    async def test_a_remembered_share_never_adds_a_grant(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Shared only into protected 14 (plain). The wiki drops that share and opens 14, whose
        # listing then fails: the remembered share must not grant the org.
        self.wiki(
            connector, processor, {12: [self.PAGE], 13: [], 14: response(status=500), 15: []},
            shares={"1": {"spaces": [14], "grants": ""}},
        )
        connector._space_status[14] = "AUTHENTICATED"

        await connector._sync_pages(self.SPACES)

        assert self.rewrites(processor) == []
        assert [w for w in self.page_writes(processor, "page:1") if w[0] == "OPEN"] == []

    @pytest.mark.asyncio
    async def test_an_unreadable_share_memory_fails_the_pass_rather_than_forget(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        self.wiki(connector, processor, {12: [self.PAGE]})
        connector.record_sync_point.read_sync_point = AsyncMock(
            side_effect=lambda key: (_ for _ in ()).throw(RuntimeError("db down")) if key.startswith("shares/") else {}
        )

        # Read as "nothing shared", a shared page would be written plain and an unshared
        # one keep its readers: no page is written (run_sync reports the pages phase).
        with pytest.raises(RuntimeError, match="db down"):
            await connector._sync_pages({12: {"id": 12}})

        processor.on_new_records.assert_not_awaited()
        assert not any(c.args[0].startswith("shares/") for c in connector.record_sync_point.update_sync_point.await_args_list)


class TestPeopleWhoLostAccess:
    @staticmethod
    def stored(email: str, source_id: str) -> MagicMock:
        return MagicMock(id=f"key-{source_id}", email=email, source_user_id=source_id)

    @pytest.mark.asyncio
    async def test_a_disabled_or_removed_person_loses_the_connector(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"id": 1, "email": "a@example.com", "status": "ENABLED"},
            {"id": 2, "email": "b@example.com", "status": "DISABLED"},
        ])))
        kept, disabled, removed = (self.stored("A@example.com", "1"), self.stored("b@example.com", "2"),
                                   self.stored("c@example.com", "3"))
        processor.get_all_app_users = AsyncMock(return_value=[kept, disabled, removed])
        connector.data_source.get_user = AsyncMock(side_effect=lambda user_id: {
            2: response({"id": 2, "email": "b@example.com", "status": "DISABLED"}),
        }.get(user_id, response(status=404)))

        await connector._sync_users()
        await connector._withdraw_people_without_access()

        processor.withdraw_app_user_access.assert_awaited_once_with("conn-1", [disabled, removed], drop_grants=True)

    @pytest.mark.asyncio
    async def test_a_person_left_out_whom_the_token_may_not_read_is_withdrawn(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"id": 1, "email": "a@example.com", "status": "ENABLED"},
        ])))
        gone = self.stored("b@example.com", "2")
        processor.get_all_app_users = AsyncMock(return_value=[self.stored("a@example.com", "1"), gone])
        connector.data_source.get_user = AsyncMock(return_value=response(status=403))
        connector.data_source.list_spaces = AsyncMock(return_value=response(spring_page([])))

        await connector._sync_users()
        await connector._withdraw_people_without_access()

        processor.withdraw_app_user_access.assert_awaited_once_with("conn-1", [gone], drop_grants=True)

    @pytest.mark.asyncio
    async def test_an_address_an_enabled_account_also_uses_keeps_access(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"id": 1, "email": "a@example.com", "status": "ENABLED"},
            {"id": 2, "email": "a@example.com", "status": "DISABLED"},
        ])))
        processor.get_all_app_users = AsyncMock(return_value=[self.stored("a@example.com", "2")])

        await connector._sync_users()
        await connector._withdraw_people_without_access()

        processor.withdraw_app_user_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_enabled_person_listed_without_an_id_keeps_access(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # UserDTO.id may be null (spec); the person can still open the wiki.
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"id": 1, "email": "a@example.com", "status": "ENABLED"},
            {"id": None, "email": "b@example.com", "status": "ENABLED"},
        ])))
        processor.get_all_app_users = AsyncMock(return_value=[self.stored("b@example.com", "2")])

        await connector._sync_users()
        await connector._withdraw_people_without_access()

        processor.withdraw_app_user_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_directory_that_ends_early_withdraws_nobody(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # An empty window that says more follow is a misread, not the end of the directory.
        connector._enabled_emails = None  # as each run starts
        connector.data_source.list_users = AsyncMock(side_effect=[
            response(spring_page([{"id": 1, "email": "a@example.com", "status": "ENABLED"}], last=False)),
            response(spring_page([], last=False)),
        ])

        with pytest.raises(DrupalWikiSyncError, match="not the last"):
            await connector._sync_users()

        assert connector._enabled_emails is None

    @pytest.mark.asyncio
    async def test_a_person_the_directory_skipped_is_checked_before_losing_access(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The directory pages by offset, so a removal between two windows can shift Bob out
        # of both. His own record says he is enabled; Carol's lookup fails, so she stays too.
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([
            {"id": 1, "email": "a@example.com", "status": "ENABLED"},
        ])))
        processor.get_all_app_users = AsyncMock(return_value=[
            self.stored("b@example.com", "2"), self.stored("c@example.com", "3"),
        ])
        connector.data_source.get_user = AsyncMock(side_effect=lambda user_id: {
            2: response({"id": 2, "email": "B@example.com", "status": "ENABLED"}),
        }.get(user_id, response(status=500)))

        await connector._sync_users()
        await connector._withdraw_people_without_access()

        processor.withdraw_app_user_access.assert_not_awaited()
        assert any("user 3" in failure for failure in connector._failures)

    @pytest.mark.asyncio
    async def test_a_directory_with_nobody_enabled_withdraws_nobody(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.data_source.list_users = AsyncMock(return_value=response(spring_page([])))
        processor.get_all_app_users = AsyncMock(return_value=[self.stored("a@example.com", "1")])

        await connector._sync_users()
        await connector._withdraw_people_without_access()

        processor.withdraw_app_user_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_run_that_could_not_read_the_directory_withdraws_nobody(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        connector.notify = AsyncMock()
        connector.data_source.list_users = AsyncMock(return_value=response(status=500))
        connector._sync_spaces = AsyncMock(return_value={})
        processor.get_all_app_users = AsyncMock(return_value=[self.stored("a@example.com", "1")])

        with patch(
            "app.connectors.sources.drupal_wiki.connector.load_connector_filters",
            AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

        processor.get_all_app_users.assert_not_awaited()
        processor.withdraw_app_user_access.assert_not_awaited()


# ---------------------------------------------------------------------------
# Attachments of pages a run does not write
# ---------------------------------------------------------------------------

class QuietWiki:
    """A wiki whose pages never change date, and a store the connector writes into and reads back.

    Every page sits in space 12 below its checkpoint, so a run writes none of them; only the
    attachment recheck looks at their attachments.
    """

    def __init__(self, connector: DrupalWikiConnector, processor: MagicMock, pages: dict[int, list[int]],
                 spaces: dict[int, int] | None = None) -> None:
        self.pages = {p: list(a) for p, a in pages.items()}  # what the wiki has: page -> attachment ids
        self.space_of = spaces or dict.fromkeys(pages, 12)
        self.gone: set[int] = set()  # attachment lookups that answer 404 although listed
        self.store: dict[str, MagicMock] = {}
        for page_id, attachments in pages.items():
            self.store[f"page:{page_id}"] = stored_record(page_id, space_id=self.space_of[page_id])
            for a in attachments:
                held = attachment_record(a, page_id=page_id)
                held.external_record_group_id = str(self.space_of[page_id])
                self.store[f"attachment:{a}"] = held
        self.checkpoints = TestMovesAndDeletions.remembered(connector)
        for space in set(self.space_of.values()):
            self.checkpoints[f"WEBPAGE/space_pages/{space}"] = {"last_modified": 5000}
        # A clean pass stands one second behind its newest page, which would write that page
        # on every run; these runs keep the cursor ahead of every page.
        connector.record_sync_point.update_sync_point = AsyncMock(side_effect=lambda key, payload: None
                                                                  if key.startswith("WEBPAGE/")
                                                                  else self.checkpoints.__setitem__(key, dict(payload)))
        connector.data_source.list_pages = AsyncMock(side_effect=lambda space_id, **_: response(spring_page([
            page_payload(p, space_id=space_id) for p in sorted(self.pages) if self.space_of[p] == space_id
        ])))
        connector.data_source.list_attachments = AsyncMock(side_effect=lambda page_id, page=0, size=PAGE_SIZE, **_: response([
            {"id": a, "fileName": f"{a}.pdf", "lastModified": 1000} for a in self.pages.get(page_id, [])
        ][page * size:(page + 1) * size]))
        connector.data_source.get_attachment = AsyncMock(side_effect=lambda aid: response(
            {"id": aid}, status=200 if any(aid in a for a in self.pages.values()) and aid not in self.gone else 404
        ))
        connector.data_source.get_page = AsyncMock(side_effect=lambda pid: response(
            page_payload(pid, space_id=self.space_of[pid]) if pid in self.pages else None,
            status=200 if pid in self.pages else 404,
        ))
        processor.get_records_in_record_group = AsyncMock(side_effect=lambda _c, space, *_a, **_k: [
            r for r in sorted(self.store.values(), key=lambda r: r.id) if r.external_record_group_id == space
        ])
        processor.get_records_by_parent = AsyncMock(side_effect=lambda _c, parent, *_a, **_k: [
            r for r in self.store.values() if r.parent_external_record_id == parent
            and r.external_record_id.startswith("attachment:")
        ])
        processor.get_record_by_external_id = AsyncMock(side_effect=lambda _c, ext: self.store.get(ext))
        processor.on_new_records = AsyncMock(side_effect=self.write)
        processor.delete_record_by_external_id = AsyncMock(side_effect=lambda _c, ext: self.store.pop(ext))

    async def write(self, batch: list) -> None:
        for record, _ in batch:
            if record.external_record_id.startswith("attachment:"):
                held = attachment_record(int(record.external_record_id.split(":")[1]),
                                         page_id=int(record.parent_external_record_id.split(":")[1]))
                held.external_record_group_id = record.external_record_group_id
                self.store[record.external_record_id] = held

    def attachments(self) -> set[str]:
        return {e for e in self.store if e.startswith("attachment:")}


class TestAttachmentsOfUnwrittenPages:
    @pytest.mark.asyncio
    async def test_an_attachment_deleted_without_its_page_changing_is_removed(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        wiki = QuietWiki(connector, processor, {1: [88, 89]})
        wiki.pages[1] = [88]

        await connector._sync_pages({12: {"id": 12}})

        assert wiki.attachments() == {"attachment:88"}
        processor.on_new_records.assert_not_awaited()  # the page itself was not written
        assert not connector._failures
        assert connector.full_sync_coverage().complete

    @pytest.mark.parametrize("status", [200, 500])
    @pytest.mark.asyncio
    async def test_an_unlisted_attachment_is_kept_unless_its_lookup_answers_404(
        self, connector: DrupalWikiConnector, processor: MagicMock, status: int
    ) -> None:
        # The listing is a bare array that ends on a short page; the spec does not say that page
        # is the last, so the listing alone removes nothing.
        wiki = QuietWiki(connector, processor, {1: [88, 89]})
        wiki.pages[1] = [88]
        connector.data_source.get_attachment = AsyncMock(return_value=response({"id": 89}, status=status))

        await connector._sync_pages({12: {"id": 12}})

        assert wiki.attachments() == {"attachment:88", "attachment:89"}

    @pytest.mark.asyncio
    async def test_a_rejected_token_on_the_lookup_stops_the_run(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        wiki = QuietWiki(connector, processor, {1: [88, 89]})
        wiki.pages[1] = [88]
        connector.data_source.get_attachment = AsyncMock(return_value=response(status=401))

        with pytest.raises(DrupalWikiAuthError):
            await connector._sync_pages({12: {"id": 12}})

        assert wiki.attachments() == {"attachment:88", "attachment:89"}

    @pytest.mark.parametrize("token_lists_spaces", [True, False], ids=["token-valid", "token-dead"])
    @pytest.mark.asyncio
    async def test_a_403_on_the_lookup_removes_it_unless_the_token_is_dead(
        self, connector: DrupalWikiConnector, processor: MagicMock, token_lists_spaces: bool
    ) -> None:
        # Unlisted and refused while the token still lists spaces: its user may not read it, so
        # the stored copy goes. Refused with the token refused too: a dead token stops the run.
        wiki = QuietWiki(connector, processor, {1: [88, 89]})
        wiki.pages[1] = [88]
        connector.data_source.get_attachment = AsyncMock(return_value=response(status=403))
        connector.data_source.list_spaces = AsyncMock(
            return_value=response(spring_page([])) if token_lists_spaces else response(status=403)
        )

        if token_lists_spaces:
            await connector._sync_pages({12: {"id": 12}})
            assert wiki.attachments() == {"attachment:88"}
        else:
            with pytest.raises(DrupalWikiAuthError):
                await connector._sync_pages({12: {"id": 12}})
            assert wiki.attachments() == {"attachment:88", "attachment:89"}

    @pytest.mark.parametrize("error", [
        httpx.ReadTimeout("timed out"),
        HostCheckError('"10.0.0.1" is a private address'),
        RuntimeError("boom"),
    ], ids=["timeout", "address refused", "anything else"])
    @pytest.mark.asyncio
    async def test_a_recheck_that_fails_keeps_everything_and_fails_nothing(
        self, connector: DrupalWikiConnector, processor: MagicMock, error: Exception
    ) -> None:
        wiki = QuietWiki(connector, processor, {1: [88], 2: [90]})
        wiki.pages[2] = []
        connector.data_source.list_attachments = AsyncMock(side_effect=[error, response([])])

        await connector._sync_pages({12: {"id": 12}})

        # Page 1's listing failed and keeps 88; page 2 after it was still rechecked.
        assert wiki.attachments() == {"attachment:88"}
        assert not connector._failures
        assert connector.full_sync_coverage().complete
        assert wiki.checkpoints["attachment_recheck/space/all"] == {"after": 0}

    @pytest.mark.asyncio
    async def test_one_budget_per_run_shared_by_every_space_in_turn(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        pages = range(1, ATTACHMENT_RECHECKS_PER_RUN + 51)
        QuietWiki(connector, processor, {p: [10_000 + p] for p in pages},
                  spaces={p: 12 if p % 2 else 13 for p in pages})
        spaces = {12: {"id": 12}, 13: {"id": 13}}

        await connector._sync_pages(spaces)
        first = [c.kwargs["page_id"] for c in connector.data_source.list_attachments.await_args_list]
        connector.data_source.list_attachments.reset_mock()
        await connector._sync_pages(spaces)
        second = [c.kwargs["page_id"] for c in connector.data_source.list_attachments.await_args_list]

        # Across both spaces, in page id order; past the last page the turn goes on from the first.
        assert first == list(pages)[:ATTACHMENT_RECHECKS_PER_RUN]
        assert second == list(pages)[ATTACHMENT_RECHECKS_PER_RUN:] + list(pages)[:50]

    @pytest.mark.asyncio
    async def test_a_page_whose_attachment_came_back_is_written_again(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # Trashed between its space's listing and the recheck: its attachments are no longer listed
        # and their lookup is a 404. Restored without a date change, it is never in a delta again;
        # the recheck sees an attachment it does not store and writes the page.
        wiki = QuietWiki(connector, processor, {1: [88]})
        wiki.pages[1], wiki.gone = [], {88}

        await connector._sync_pages({12: {"id": 12}})
        assert wiki.attachments() == set()

        wiki.pages[1], wiki.gone = [88], set()
        await connector._sync_pages({12: {"id": 12}})

        assert wiki.attachments() == {"attachment:88"}
        connector.data_source.get_page.assert_awaited_with(1)

    @pytest.mark.asyncio
    async def test_an_attachment_the_filters_exclude_does_not_write_its_page(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        wiki = QuietWiki(connector, processor, {1: [88]})
        wiki.pages[1] = [88, 89]
        connector.data_source.list_attachments = AsyncMock(return_value=response([
            {"id": 88, "fileName": "a.pdf"}, {"id": 89, "fileName": "b.zip"},
        ]))
        connector.sync_filters = _extension_filter(MultiselectOperator.NOT_IN, ["zip"])

        await connector._sync_pages({12: {"id": 12}})

        connector.data_source.get_page.assert_not_awaited()
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stored_attachments_are_read_past_the_first_thousand_records(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        wiki = QuietWiki(connector, processor, {1: [88]})
        filler = [attachment_record(50_000 + i, page_id=2) for i in range(1000)]
        for r in filler:
            r.id = f"a-{r.id}"  # sorted ahead of page 1's records
        reads: list = []

        async def read(_c: str, space: str, limit: int, after: str | None = None, **_: object) -> list:
            reads.append(after)
            records = filler + sorted(wiki.store.values(), key=lambda r: r.id)
            start = next((i + 1 for i, r in enumerate(records) if r.id == after), 0) if after else 0
            return records[start:start + limit]

        processor.get_records_in_record_group = AsyncMock(side_effect=read)
        wiki.pages[1] = []

        await connector._sync_pages({12: {"id": 12}})

        assert reads == [None, filler[-1].id]
        assert "attachment:88" not in wiki.attachments()

    @pytest.mark.asyncio
    async def test_a_stored_attachment_without_an_id_is_never_looked_up(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        wiki = QuietWiki(connector, processor, {1: [88]})
        broken = attachment_record(0, page_id=1)
        broken.external_record_id = "attachment:None"
        wiki.store["attachment:None"] = broken

        await connector._sync_pages({12: {"id": 12}})

        assert [c.args for c in connector.data_source.get_attachment.await_args_list] == []
        assert "attachment:None" in wiki.store


    @pytest.mark.asyncio
    async def test_a_written_page_keeps_an_unlisted_attachment_the_wiki_still_serves(
        self, connector: DrupalWikiConnector, processor: MagicMock
    ) -> None:
        # The same rule as for pages left unwritten: only the lookup's 404 removes.
        TestMovesAndDeletions.page_with_attachments(connector, processor, {}, 1000, response([
            {"id": 88, "fileName": "a.pdf", "lastModified": 1000},
        ]))
        connector.data_source.get_attachment = AsyncMock(return_value=response({"id": 89}))

        await connector._sync_pages({12: {"id": 12}})

        processor.delete_record_by_external_id.assert_not_awaited()
