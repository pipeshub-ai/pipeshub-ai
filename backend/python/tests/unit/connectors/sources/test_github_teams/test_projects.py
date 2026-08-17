"""Unit tests for github_teams ProjectsSync.

Covers:
- Collaborator role -> PermissionType mapping (admin/maintain/push/triage/pull).
- Team role -> GROUP permission + membership edge sync.
- Creator-only fallback when member/team listing raises.
- Record-group hierarchy external ids anchored on the stable numeric repo.id
  (not owner/repo) — the core rename-survivability property.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.models import team_group_external_id
from app.connectors.sources.github_teams.projects import (
    CollaboratorsUnavailable,
    ProjectsSync,
    _dedupe_highest_permissions,
    _permission_type_from_role,
)
from app.models.permission import EntityType, Permission, PermissionType

from .conftest import (
    failed_response,
    make_mock_connector,
    make_named_user,
    make_repo,
    make_team,
    ok_response,
)

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
        assert perms[0].external_id == team_group_external_id(7)
        assert perms[0].type == PermissionType.READ
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()
        # The Permission and the group node must agree; they are built separately.
        group, _members = c.data_entities_processor.on_new_user_groups.call_args.args[0][0]
        assert group.source_user_group_id == perms[0].external_id

    async def test_member_listing_failure_skips_the_repo_without_touching_it(self) -> None:
        """on_new_record_groups DELETES a record group's permission edges before
        recreating them from the list passed in, so writing an empty list would
        revoke everyone's access. The repo must be left entirely alone instead."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = RuntimeError("API down")

        sync = ProjectsSync(c)
        repo = make_repo()
        sync._create_record_group_hierarchy = AsyncMock()
        sync._flush_org_record_groups = AsyncMock()
        sync._resolve_repos_with_filters = AsyncMock(return_value=[repo])
        c.issues.fetch_issues_batched = AsyncMock()
        c.repos.run = AsyncMock()

        await sync.sync_all_repos()

        sync._create_record_group_hierarchy.assert_not_awaited()
        c.issues.fetch_issues_batched.assert_not_awaited()
        c.repos.run.assert_not_awaited()

    async def test_collaborator_403_carries_the_status_for_the_caller(self) -> None:
        """/collaborators needs *push* access, so read-only access to any repo
        403s permanently. The status has to survive so the caller can tell that
        apart from an outage."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": failed_response("Must have push access", status_code=403),
        })

        with pytest.raises(CollaboratorsUnavailable) as exc:
            await ProjectsSync(c)._sync_repo_members("python", "cpython")

        assert exc.value.is_structural is True


class TestPermissionsWithoutCollaborators:
    """A 403 on /collaborators must not cost a public repo its whole sync: the
    visibility grant alone is a complete and correct ACL for it."""

    def _err(self, status: int | None) -> CollaboratorsUnavailable:
        return CollaboratorsUnavailable("boom", status_code=status)

    async def test_public_repo_syncs_on_403_using_visibility_alone(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "public"

        result = sync._permissions_without_collaborators(repo, self._err(403))

        assert result == []  # not None -> the repo proceeds; visibility is added after

    async def test_internal_repo_syncs_on_403_using_member_floor(self) -> None:
        c = make_mock_connector()
        c.users.org_member_emails = lambda: {"alice@corp.com"}
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "internal"

        assert sync._permissions_without_collaborators(repo, self._err(403)) == []

    async def test_private_repo_still_skips_on_403(self) -> None:
        """No floor exists — granting ORG would expose a private repo to the
        whole tenant, and an empty ACL would revoke everyone."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "private"

        assert sync._permissions_without_collaborators(repo, self._err(403)) is None

    async def test_transient_failure_on_a_public_repo_still_skips(self) -> None:
        """A 5xx must keep last sync's richer grants rather than downgrading
        every maintainer to READ for one cycle."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "public"

        assert sync._permissions_without_collaborators(repo, self._err(500)) is None
        assert sync._permissions_without_collaborators(repo, RuntimeError("API down")) is None


class TestVisibilityPermissions:
    """A public repo is readable by anyone with a GitHub account, so it is
    mirrored as readable by the whole PipesHub org."""

    async def test_public_repo_grants_read_to_the_whole_org(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "public"

        perms = sync._visibility_permissions(repo)

        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG
        assert perms[0].type == PermissionType.READ

    async def test_private_repo_grants_nothing(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "private"

        assert sync._visibility_permissions(repo) == []

    async def test_internal_repo_grants_read_to_org_members_only(self) -> None:
        """Internal repos are readable by every enterprise member but explicitly
        NOT by outside collaborators, so an ORG grant would over-grant twice
        over — PipesHub users outside the enterprise, and outside collaborators.
        Per-member USER grants are the faithful model."""
        c = make_mock_connector()
        c.users.org_member_emails = lambda: {"alice@corp.com", "bob@corp.com"}
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "internal"

        perms = sync._visibility_permissions(repo)

        assert {p.email for p in perms} == {"alice@corp.com", "bob@corp.com"}
        assert all(p.entity_type == EntityType.USER for p in perms)
        assert all(p.type == PermissionType.READ for p in perms)
        assert not any(p.entity_type == EntityType.ORG for p in perms)

    async def test_internal_repo_grants_nothing_when_no_member_resolved(self) -> None:
        """User sync resolved nobody (or never ran) — fall back to explicit
        collaborators rather than guessing."""
        c = make_mock_connector()
        c.users.org_member_emails = lambda: set()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = "internal"

        assert sync._visibility_permissions(repo) == []

    def test_internal_read_never_downgrades_a_collaborator_grant(self) -> None:
        """The internal-repo READ restates people who already hold WRITE/OWNER
        as collaborators; the merged ACL must keep one edge each, at the
        strongest level."""
        merged = _dedupe_highest_permissions([
            Permission(email="alice@corp.com", type=PermissionType.OWNER, entity_type=EntityType.USER),
            Permission(email="bob@corp.com", type=PermissionType.WRITE, entity_type=EntityType.USER),
            Permission(email="alice@corp.com", type=PermissionType.READ, entity_type=EntityType.USER),
            Permission(email="bob@corp.com", type=PermissionType.READ, entity_type=EntityType.USER),
            Permission(email="carol@corp.com", type=PermissionType.READ, entity_type=EntityType.USER),
        ])

        by_email = {p.email: p.type for p in merged}
        assert by_email == {
            "alice@corp.com": PermissionType.OWNER,
            "bob@corp.com": PermissionType.WRITE,
            "carol@corp.com": PermissionType.READ,
        }

    def test_dedupe_keeps_group_and_org_grants_separate_from_users(self) -> None:
        """Keying is (entity_type, id) — a GROUP and a USER grant must never
        collapse into one another."""
        merged = _dedupe_highest_permissions([
            Permission(email="alice@corp.com", type=PermissionType.READ, entity_type=EntityType.USER),
            Permission(external_id="42", type=PermissionType.WRITE, entity_type=EntityType.GROUP),
            Permission(type=PermissionType.READ, entity_type=EntityType.ORG),
        ])

        assert len(merged) == 3

    async def test_falls_back_to_the_private_boolean_when_visibility_absent(self) -> None:
        """Older payloads expose only `private`; an unknown repo must not be
        treated as public."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=1)
        repo.visibility = None
        repo.private = True

        assert sync._visibility_permissions(repo) == []

        repo.private = False
        assert sync._visibility_permissions(repo)[0].entity_type == EntityType.ORG


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

    async def test_acl_lives_only_on_the_repo_group_and_children_inherit(self) -> None:
        """One ACL per repo: the children carry no permissions of their own and
        set inherit_permissions=True, which makes the processor write the
        child->repo INHERIT_PERMISSIONS edge access resolution walks. The repo
        group must NOT inherit — its parent (the org group) holds the union of
        every repo's grants, and inheriting that union would leak each repo to
        every other repo's users."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=555)
        permission = SimpleNamespace(email="x@example.com", type=PermissionType.READ, entity_type=EntityType.USER)

        await sync._create_record_group_hierarchy(repo, [permission])

        groups = {rg.external_group_id: (rg, perms)
                  for rg, perms in c.data_entities_processor.on_new_record_groups.call_args.args[0]}
        repo_rg, repo_perms = groups["555"]
        assert repo_perms == [permission]
        assert not repo_rg.inherit_permissions
        for child_id in ("555-work-items", "555-pull-requests", "555-code-repository"):
            child_rg, child_perms = groups[child_id]
            assert child_perms == []
            assert child_rg.inherit_permissions is True
            assert child_rg.parent_external_group_id == "555"

    async def test_org_group_anchored_on_numeric_owner_id(self) -> None:
        """The org group id must survive an org rename too: keyed on the
        numeric owner id (free on every repo payload), never the login."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        repo = make_repo(repo_id=555, owner_login="acme", owner_id=777)
        permission = SimpleNamespace(email="x@example.com", type=PermissionType.READ, entity_type=EntityType.USER)

        await sync._create_record_group_hierarchy(repo, [permission])
        groups = {rg.external_group_id: rg
                  for rg, _ in c.data_entities_processor.on_new_record_groups.call_args.args[0]}
        assert groups["555"].parent_external_group_id == "org-777"

        sync._accumulate_org_permissions(repo.owner, [permission])
        await sync._flush_org_record_groups()
        org_rg, org_perms = c.data_entities_processor.on_new_record_groups.call_args.args[0][0]
        assert org_rg.external_group_id == "org-777"
        assert org_rg.name == "acme"  # login stays the human-facing name
        assert org_perms == [permission]

    async def test_org_group_is_written_before_the_repo_syncs_any_record(self) -> None:
        """Connector stats count by walking DOWN from the App node, and the org
        group is the only one the platform links to the App (a group with a
        parent never gets that edge). Flushing it after the repo loop left every
        record written during the sync unreachable — reported as zero — until
        the final write."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync._sync_repo_members = AsyncMock(return_value=[])

        order: list[str] = []
        c.data_entities_processor.on_new_record_groups.side_effect = (
            lambda pairs: order.extend(f"group:{rg.external_group_id}" for rg, _ in pairs)
        )
        c.issues.fetch_issues_batched = AsyncMock(side_effect=lambda _r: order.append("issues"))
        c.pull_requests.fetch_prs_batched = AsyncMock(side_effect=lambda _r: order.append("prs"))
        c.repos.run = AsyncMock(side_effect=lambda _r: order.append("code"))

        await sync._sync_repo(make_repo(repo_id=555, owner_id=777))

        assert order.index("group:org-777") < order.index("group:555")
        assert order.index("group:org-777") < order.index("issues")

    async def test_org_group_is_not_rewritten_when_a_repo_adds_no_grant(self) -> None:
        """The per-repo flush is gated on the union actually widening, so repos
        sharing an org cost one write, not one each."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        owner = make_repo(repo_id=1, owner_id=777).owner
        permission = SimpleNamespace(
            email="x@example.com", type=PermissionType.READ, entity_type=EntityType.USER
        )

        sync._accumulate_org_permissions(owner, [permission])
        await sync._flush_org_record_groups()
        assert c.data_entities_processor.on_new_record_groups.await_count == 1

        sync._accumulate_org_permissions(owner, [permission])  # same principal again
        await sync._flush_org_record_groups()
        assert c.data_entities_processor.on_new_record_groups.await_count == 1

    async def test_a_first_seen_org_flushes_even_with_no_permissions(self) -> None:
        """A public repo whose collaborators are unresolvable yields no grants,
        but the group still has to exist or its records stay uncounted."""
        c = make_mock_connector()
        sync = ProjectsSync(c)

        sync._accumulate_org_permissions(make_repo(repo_id=1, owner_id=777).owner, [])
        await sync._flush_org_record_groups()

        org_rg, org_perms = c.data_entities_processor.on_new_record_groups.call_args.args[0][0]
        assert org_rg.external_group_id == "org-777"
        assert org_perms == []

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


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    """Build a ``ds_call`` side_effect dispatching on data_source method identity."""
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
