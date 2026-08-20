"""Unit tests for github_teams ProjectsSync.

Covers:
- Collaborator role -> PermissionType mapping (admin/maintain/push/triage/pull).
- Creator-only fallback when member listing raises.
- Record-group hierarchy external ids anchored on the stable numeric repo.id
  (not owner/repo) — the core rename-survivability property.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.connectors.sources.github_teams.projects import (
    CollaboratorsUnavailable,
    ProjectsSync,
    _dedupe_highest_permissions,
    _highest_role_from_collaborator_permissions,
    _permission_type_from_role,
)
from app.models.permission import EntityType, Permission, PermissionType

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    make_named_user,
    make_repo,
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
        })
        c.tx_store.get_user_by_source_id = AsyncMock(
            return_value=SimpleNamespace(email="alice@example.com")
        )

        sync = ProjectsSync(c)
        perms = await sync._sync_repo_members("acme", "widgets")

        assert len(perms) == 1
        assert perms[0].email == "alice@example.com"
        assert perms[0].type == PermissionType.WRITE  # push implies WRITE
        c.data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_individual_owned_repo_binds_collaborators_before_granting(self) -> None:
        """A user-account owner has no org to enumerate, so the collaborator
        list is the only identity source — it must be bound to AppUsers before
        the (unchanged) grant loop runs, or a private individual repo is
        visible to nobody."""
        c = make_mock_connector()
        collaborator = make_named_user(
            user_id=42, login="alice",
            permissions=SimpleNamespace(admin=True, maintain=False, push=True, triage=False, pull=True),
        )
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": ok_response([collaborator]),
        })
        c.users.resolve_collaborator_principals = AsyncMock(return_value={42})
        c.tx_store.get_user_by_source_id = AsyncMock(
            return_value=SimpleNamespace(email="alice@example.com")
        )

        repo = make_repo(owner_login="darshan", owner_type="User", name="private-repo")
        perms = await ProjectsSync(c)._sync_repo_members("darshan", "private-repo", repo)

        c.users.resolve_collaborator_principals.assert_awaited_once()
        assert set(c.users.resolve_collaborator_principals.await_args.args[0]) == {42}
        assert [p.email for p in perms] == ["alice@example.com"]

    async def test_org_owned_repo_never_enters_the_individual_path(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": ok_response([make_named_user(
                user_id=42, login="alice",
                permissions=SimpleNamespace(admin=False, maintain=False, push=True, triage=False, pull=True),
            )]),
        })
        c.users.resolve_collaborator_principals = AsyncMock()

        repo = make_repo(owner_type="Organization")
        await ProjectsSync(c)._sync_repo_members("acme", "widgets", repo)

        c.users.resolve_collaborator_principals.assert_not_awaited()

    async def test_missing_repo_object_keeps_the_original_flow(self) -> None:
        """Callers that pass no repo object (existing signature) get the exact
        pre-change behaviour: no resolution attempt."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": ok_response([]),
        })
        c.users.resolve_collaborator_principals = AsyncMock()

        await ProjectsSync(c)._sync_repo_members("acme", "widgets")

        c.users.resolve_collaborator_principals.assert_not_awaited()

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


class TestSyncAllReposAndResolution:
    async def test_missing_data_source_raises(self) -> None:
        c = make_mock_connector()
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await ProjectsSync(c).sync_all_repos()

    async def test_no_repos_after_filters_is_noop(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync._resolve_repos_with_filters = AsyncMock(return_value=[])
        await sync.sync_all_repos()
        c.issues.fetch_issues_batched.assert_not_awaited()

    async def test_per_repo_error_continues_to_next(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        first, second = make_repo(repo_id=1), make_repo(repo_id=2, name="other")
        sync._resolve_repos_with_filters = AsyncMock(return_value=[first, second])
        sync._sync_repo = AsyncMock(side_effect=[RuntimeError("boom"), None])
        sync._flush_org_record_groups = AsyncMock()

        await sync.sync_all_repos()

        assert sync._sync_repo.await_count == 2
        sync._flush_org_record_groups.assert_awaited()

    async def test_child_step_error_does_not_abort_repo(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync._sync_repo_members = AsyncMock(return_value=[])
        c.issues.fetch_issues_batched = AsyncMock(side_effect=RuntimeError("issues down"))
        c.pull_requests.fetch_prs_batched = AsyncMock()
        c.repos.run = AsyncMock()

        await sync._sync_repo(make_repo(repo_id=555, owner_id=777))

        c.pull_requests.fetch_prs_batched.assert_awaited_once()
        c.repos.run.assert_awaited_once()

    async def test_repo_ids_in_resolves_each_repo(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=9, owner_login="acme", name="widgets")
        c.sync_filters = {
            SyncFilterKey.REPO_IDS: SimpleNamespace(
                is_empty=lambda: False, value=["acme/widgets", "badname"], operator_value=FilterOperator.IN,
            )
        }
        c.runtime.ds_call.side_effect = _dispatch(c, {"get_repo": ok_response(repo)})

        result = await ProjectsSync(c)._resolve_repos_with_filters()
        assert [r.id for r in result] == [9]

    async def test_org_in_lists_org_repos_and_applies_exclusions(self) -> None:
        c = make_mock_connector()
        kept = make_repo(repo_id=1, owner_login="acme", name="kept")
        dropped = make_repo(repo_id=2, owner_login="acme", name="dropped")
        c.sync_filters = {
            SyncFilterKey.ORG_IDS: SimpleNamespace(
                is_empty=lambda: False, value=["acme"], operator_value=FilterOperator.IN,
            ),
            SyncFilterKey.REPO_IDS: SimpleNamespace(
                is_empty=lambda: False, value=["acme/dropped"], operator_value=FilterOperator.NOT_IN,
            ),
        }
        c.runtime.ds_call.side_effect = _dispatch(c, {"list_org_repos": ok_response([kept, dropped])})

        result = await ProjectsSync(c)._resolve_repos_with_filters()
        assert [r.full_name for r in result] == ["acme/kept"]

    async def test_org_discovery_failure_returns_empty(self) -> None:
        c = make_mock_connector()
        c.users._resolve_target_orgs = AsyncMock(return_value=([], False))
        assert await ProjectsSync(c)._resolve_repos_with_filters() == []

    async def test_org_list_failure_skips_that_org(self) -> None:
        c = make_mock_connector()
        c.users._resolve_target_orgs = AsyncMock(return_value=(["acme"], True))
        c.runtime.ds_call.side_effect = _dispatch(c, {"list_org_repos": failed_response("403")})
        assert await ProjectsSync(c)._resolve_repos_with_filters() == []


class TestCollaboratorEdgeCases:
    async def test_empty_collaborator_list_grants_nothing(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_collaborators": ok_response([]),
        })
        perms = await ProjectsSync(c)._sync_repo_members("acme", "widgets")
        assert perms == []
        c.data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_unknown_collaborator_role_grants_nothing(self) -> None:
        c = make_mock_connector()
        user = make_named_user(user_id=1, login="x", permissions=SimpleNamespace(
            admin=False, maintain=False, push=False, triage=False, pull=False,
        ))
        assert await ProjectsSync(c)._transform_collaborator_to_permission(user) is None

    async def test_create_user_permission_exception_returns_none(self) -> None:
        c = make_mock_connector()
        c.tx_store.get_user_by_source_id = AsyncMock(side_effect=RuntimeError("db"))
        assert await ProjectsSync(c)._create_user_permission("1", PermissionType.READ) is None

    def test_highest_role_none_when_no_permissions(self) -> None:
        assert _highest_role_from_collaborator_permissions(None) is None
        assert _highest_role_from_collaborator_permissions(SimpleNamespace()) is None


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    """Build a ``ds_call`` side_effect dispatching on data_source method identity."""
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
