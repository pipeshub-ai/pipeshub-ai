"""Unit tests for github_teams ProjectsSync.

Covers:
- Collaborator role -> PermissionType mapping (admin/maintain/push/triage/pull).
- Team role -> GROUP permission + membership edge sync.
- Creator-only fallback when member/team listing raises.
- Record-group hierarchy external ids anchored on the stable numeric repo.id
  (not owner/repo) — the core rename-survivability property.
- Deleted/transferred repo detection via the repo-inventory SyncPoint.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.models import GitHubLiterals
from app.connectors.sources.github_teams.projects import ProjectsSync, _permission_type_from_role
from app.models.permission import EntityType, PermissionType

from .conftest import make_mock_connector, make_named_user, make_repo, make_team, ok_response

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestPermissionTypeFromRole:
    @pytest.mark.parametrize(
        "role,expected",
        [
            ("admin", PermissionType.OWNER),
            ("maintain", PermissionType.WRITE),
            ("push", PermissionType.WRITE),
            ("triage", PermissionType.READ),
            ("pull", PermissionType.READ),
            (None, None),
            ("unknown", None),
        ],
    )
    def test_role_mapping(self, role: str | None, expected: PermissionType | None) -> None:
        assert _permission_type_from_role(role) == expected


class TestSyncRepoMembers:
    async def test_collaborator_role_maps_to_user_permission(self) -> None:
        c = make_mock_connector()
        collaborator = make_named_user(
            user_id=42, login="alice",
            permissions=SimpleNamespace(admin=False, maintain=False, push=True, triage=False, pull=True),
        )
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": ok_response([collaborator]),
            "list_repo_teams": ok_response([]),
        })
        c.tx_store.get_user_by_source_id = AsyncMock(
            return_value=SimpleNamespace(email="alice@example.com")
        )

        sync = ProjectsSync(c)
        perms = await sync._sync_repo_members("acme", "widgets")

        assert len(perms) == 1
        assert perms[0].email == "alice@example.com"
        assert perms[0].type == PermissionType.WRITE  # push implies WRITE

    async def test_team_role_maps_to_group_permission_and_syncs_membership(self) -> None:
        c = make_mock_connector()
        team = make_team(team_id=7, slug="core", name="Core Team", permission="pull")
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": ok_response([]),
            "list_repo_teams": ok_response([team]),
            "list_team_members": ok_response([make_named_user(user_id=1, login="bob")]),
        })
        c.tx_store.get_user_by_source_id = AsyncMock(
            return_value=SimpleNamespace(email="bob@example.com")
        )

        sync = ProjectsSync(c)
        perms = await sync._sync_repo_members("acme", "widgets")

        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP
        assert perms[0].external_id == "7"
        assert perms[0].type == PermissionType.READ
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    async def test_creator_only_fallback_on_listing_failure(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = RuntimeError("API down")
        c.creator_user_permission = lambda: SimpleNamespace(
            email="creator@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER,
        )

        sync = ProjectsSync(c)
        # _sync_repo is the caller that catches the exception and falls back;
        # calling _sync_repo_members directly re-raises, matching production wiring.
        repo = make_repo()
        sync._create_record_group_hierarchy = AsyncMock()
        sync._flush_org_record_groups = AsyncMock()
        sync._update_repo_inventory = AsyncMock()
        sync._detect_deleted_repos = AsyncMock()
        sync._resolve_repos_with_filters = AsyncMock(return_value=[repo])

        # Issues/code steps aren't under test here.
        c.issues.fetch_issues_batched = AsyncMock()
        c.repos.run = AsyncMock()

        await sync.sync_all_repos()

        args = sync._create_record_group_hierarchy.call_args.args
        permissions = args[1]
        assert len(permissions) == 1
        assert permissions[0].email == "creator@example.com"


class TestRecordGroupHierarchy:
    async def test_external_ids_anchored_on_stable_repo_id(self) -> None:
        """The core rename-survivability property: every child record group's
        external id is derived from repo.id, never repo.full_name."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=555, owner_login="acme", name="widgets")
        permission = SimpleNamespace(email="x@example.com", type=PermissionType.READ, entity_type=EntityType.USER)

        await sync._create_record_group_hierarchy(repo, [permission])

        c.data_entities_processor.on_new_record_groups.assert_awaited_once()
        groups = c.data_entities_processor.on_new_record_groups.call_args.args[0]
        external_ids = {rg.external_group_id for rg, _perms in groups}
        assert external_ids == {"555", "555-work-items", "555-pull-requests", "555-code-repository"}

    async def test_record_group_hierarchy_survives_repo_rename(self) -> None:
        """Calling the hierarchy builder again after a rename (same repo.id, new
        full_name) must reuse the exact same external ids."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        permission = SimpleNamespace(email="x@example.com", type=PermissionType.READ, entity_type=EntityType.USER)

        repo_before = make_repo(repo_id=555, owner_login="acme", name="widgets")
        repo_after = make_repo(repo_id=555, owner_login="acme", name="widgets-renamed")

        await sync._create_record_group_hierarchy(repo_before, [permission])
        ids_before = {
            rg.external_group_id
            for rg, _ in c.data_entities_processor.on_new_record_groups.call_args.args[0]
        }

        await sync._create_record_group_hierarchy(repo_after, [permission])
        ids_after = {
            rg.external_group_id
            for rg, _ in c.data_entities_processor.on_new_record_groups.call_args.args[0]
        }

        assert ids_before == ids_after


class TestDeletedRepoDetection:
    async def test_detects_and_cascade_deletes_removed_repo(self) -> None:
        c = make_mock_connector()
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={GitHubLiterals.REPO_IDS.value: [1, 2, 3]}
        )
        sync = ProjectsSync(c)
        sync._inventory_sync_point = lambda: c.record_sync_point
        sync._cascade_delete_repo = AsyncMock()

        await sync._detect_deleted_repos(current_ids={1, 2})  # repo 3 disappeared

        sync._cascade_delete_repo.assert_awaited_once_with(3)

    async def test_no_deletion_when_inventory_matches(self) -> None:
        c = make_mock_connector()
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={GitHubLiterals.REPO_IDS.value: [1, 2]}
        )
        sync = ProjectsSync(c)
        sync._inventory_sync_point = lambda: c.record_sync_point
        sync._cascade_delete_repo = AsyncMock()

        await sync._detect_deleted_repos(current_ids={1, 2})

        sync._cascade_delete_repo.assert_not_awaited()

    async def test_cascade_delete_removes_all_four_child_groups(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync._list_record_ids_for_group = AsyncMock(return_value=["rec-1", "rec-2"])

        await sync._cascade_delete_repo(555)

        assert c.data_entities_processor.on_record_group_deleted.await_count == 4
        deleted_external_ids = {
            call.args[0] for call in c.data_entities_processor.on_record_group_deleted.call_args_list
        }
        assert deleted_external_ids == {"555", "555-work-items", "555-pull-requests", "555-code-repository"}
        assert c.data_entities_processor.on_records_deleted_cascade.await_count == 4


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    """Build a ``ds_call`` side_effect dispatching on data_source method identity."""
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
