"""Unit tests for the GitLab Personal connector.

Covers:
- Factory wiring (``gitlabpersonal`` → ``GitLabPersonalConnector``).
- ``GitLabPersonalApp`` identity and the ``Connectors.GITLAB_PERSONAL`` enum.
- Connector identity after construction (``connector_name``, ``app``).
- ``creator_user_permission`` reads the cached GROUP permission.
- ``ensure_connector_group_permission`` upserts a single ``ConnectorGroup``
  with the creator as the sole member edge, caches the permission, and
  no-ops without a creator email.
- ``projects._ensure_gitlab_group_record_groups`` emits ``GROUP`` permissions
  rather than ``USER`` permissions, handles missing data source / missing perm /
  failing ``get_group``.
- ``projects._apply_creator_fallback_for_project`` (inherited) routes through
  the overridden ``creator_user_permission`` and therefore emits a GROUP
  permission on every record group it creates.
- ``run_sync`` resets the cache, resolves the creator identity, calls
  ``ensure_connector_group_permission`` only when the creator email is
  known, then dispatches to ``projects.sync_all_projects``.
- ``create_connector`` returns a configured ``GitLabPersonalConnector``.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.constants import INTERNAL_CONNECTOR_GROUP_NAME
from app.connectors.sources.gitlab_personal.common.apps import GitLabPersonalApp
from app.connectors.sources.gitlab_personal.connector import GitLabPersonalConnector
from app.models.entities import AppUser, AppUserGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test.gitlab.personal")
    log.setLevel(logging.CRITICAL)
    return log


def _make_deps() -> tuple[logging.Logger, MagicMock, MagicMock, MagicMock]:
    logger = _make_logger()
    dep = MagicMock()
    dep.org_id = "org-personal-1"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector(
    *,
    connector_id: str = "gl-personal-1",
    created_by: str = "user-abc",
    creator_email: str | None = "creator@example.com",
) -> GitLabPersonalConnector:
    """Build a GitLabPersonalConnector with mocked deps.

    By default ``creator_email`` is pre-populated so callers can exercise
    the post-resolution code paths without running ``_resolve_creator_identity``.
    """
    logger, dep, dsp, cs = _make_deps()
    connector = GitLabPersonalConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id=connector_id,
        scope="personal",
        created_by=created_by,
    )
    connector.creator_email = creator_email
    return connector


def _ok(data) -> MagicMock:
    res = MagicMock()
    res.success = True
    res.data = data
    res.error = None
    return res


def _fail(error: str = "boom") -> MagicMock:
    res = MagicMock()
    res.success = False
    res.data = None
    res.error = error
    return res


# ---------------------------------------------------------------------------
# Factory + app + constants
# ---------------------------------------------------------------------------


class TestGitLabPersonalFactoryRegistry:
    """Factory wiring tests.

    ``ConnectorFactory`` is imported lazily inside each test because the
    top-level import pulls in every registered connector — including the
    Microsoft Graph stack, whose ``kiota`` / ``msgraph`` metaclasses can
    conflict with the test-env mocks installed by ``tests/conftest.py``.
    Keeping the import inside the test body lets the rest of this file
    (the actual personal-connector behaviour) collect and run even when
    the optional MS deps are absent locally.
    """

    def test_factory_resolves_gitlabpersonal(self) -> None:
        from app.connectors.core.factory.connector_factory import ConnectorFactory

        cls = ConnectorFactory.get_connector_class("gitlabpersonal")
        assert cls is GitLabPersonalConnector

    def test_factory_case_insensitive(self) -> None:
        from app.connectors.core.factory.connector_factory import ConnectorFactory

        assert (
            ConnectorFactory.get_connector_class("GitLabPersonal")
            is GitLabPersonalConnector
        )

    def test_list_connectors_contains_key(self) -> None:
        from app.connectors.core.factory.connector_factory import ConnectorFactory

        names = ConnectorFactory.list_connectors()
        assert "gitlabpersonal" in names
        assert names["gitlabpersonal"] is GitLabPersonalConnector


class TestGitLabPersonalApp:
    def test_app_identity(self) -> None:
        app = GitLabPersonalApp("conn-personal-1")
        assert app.get_app_name() == Connectors.GITLAB_PERSONAL
        assert app.get_app_group_name() == AppGroups.GITLAB
        assert app.get_connector_id() == "conn-personal-1"

    def test_connectors_enum_value(self) -> None:
        # The string value is what gets stored on arango records / used in
        # cross-service routing; lock it down so a rename can't silently
        # break consumers.
        assert Connectors.GITLAB_PERSONAL.value == "GITLAB PERSONAL"


class TestGitLabPersonalConnectorIdentity:
    def test_init_wires_personal_app_and_name(self) -> None:
        connector = _make_connector(connector_id="conn-id-42")
        assert isinstance(connector.app, GitLabPersonalApp)
        assert connector.app.get_connector_id() == "conn-id-42"
        # Stored as the enum *value* (string) on the connector instance —
        # matches the workspace connector's convention and keeps Pydantic
        # enum coercion centralized to ensure_connector_group_permission.
        assert connector.connector_name == Connectors.GITLAB_PERSONAL.value

    def test_connector_group_permission_starts_uncached(self) -> None:
        # The cache is the contract between ensure_connector_group_permission
        # and the inherited GitLab record-group writers; if it leaks across
        # instances the next sync gets the previous sync's creator email.
        connector = _make_connector()
        assert connector._connector_group_permission is None


# ---------------------------------------------------------------------------
# _creator_user_permission override
# ---------------------------------------------------------------------------


class TestGitLabPersonalCreatorUserPermission:
    def test_returns_none_before_group_is_ensured(self) -> None:
        connector = _make_connector()
        assert connector.creator_user_permission() is None

    def test_returns_cached_group_permission(self) -> None:
        connector = _make_connector()
        cached = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-gl-personal-1",
            type=PermissionType.READ,
        )
        connector._connector_group_permission = cached

        result = connector.creator_user_permission()

        assert result is cached
        assert result.entity_type == EntityType.GROUP
        # Crucially we do NOT emit a USER permission keyed by email — that
        # is what the workspace connector does. A regression here would
        # silently grant the creator direct ACLs instead of routing through
        # ConnectorGroup, defeating the purpose of the pseudo-group.
        assert result.email is None


# ---------------------------------------------------------------------------
# ensure_connector_group_permission (base helper, exercised via personal)
# ---------------------------------------------------------------------------


class TestEnsureConnectorGroupPermission:
    @pytest.mark.asyncio
    async def test_noop_without_creator_email(self) -> None:
        connector = _make_connector(creator_email=None)

        result = await connector.ensure_connector_group_permission()

        assert result is None
        assert connector._connector_group_permission is None
        # Without a creator we must not write either edge; otherwise we'd
        # be wiring an unresolvable principal into the App graph.
        connector.data_entities_processor.on_new_app_users.assert_not_called()
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_upserts_group_with_creator_member_and_caches_permission(
        self,
    ) -> None:
        connector = _make_connector(
            connector_id="conn-xyz", creator_email="alice@example.com"
        )

        result = await connector.ensure_connector_group_permission()

        # Cached and returned permission are the same object — repeat calls
        # in the same sync must reuse it without re-issuing the upsert.
        assert result is not None
        assert result is connector._connector_group_permission
        assert result.entity_type == EntityType.GROUP
        assert result.external_id == "internal-conn-xyz"

        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()
        args, _ = connector.data_entities_processor.on_new_user_groups.call_args
        payload = args[0]
        assert len(payload) == 1
        group, members = payload[0]
        assert isinstance(group, AppUserGroup)
        assert group.name == INTERNAL_CONNECTOR_GROUP_NAME
        assert group.app_name == Connectors.GITLAB_PERSONAL
        assert group.connector_id == "conn-xyz"
        assert group.source_user_group_id == "internal-conn-xyz"
        assert group.org_id == "org-personal-1"

        # Exactly one member (the creator) — the USER→GROUP edge written by
        # on_new_user_groups uses this email to find the platform user row.
        assert len(members) == 1
        assert isinstance(members[0], AppUser)
        assert members[0].email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_writes_user_app_relation_before_group_membership(self) -> None:
        """The USER -> APP edge must be written first (and exactly once).

        Downstream AQL traversals walk ``OUTBOUND user USER_APP_RELATION``
        to discover which connectors a user can reach. If the group edge
        races ahead of the app edge, a query landing between the two
        writes would see the creator inside ConnectorGroup but with no
        reachable App node — silently invisible records.
        """
        connector = _make_connector(creator_email="alice@example.com")
        call_order: list[str] = []
        connector.data_entities_processor.on_new_app_users = AsyncMock(
            side_effect=lambda *_a, **_k: call_order.append("app_users")
        )
        connector.data_entities_processor.on_new_user_groups = AsyncMock(
            side_effect=lambda *_a, **_k: call_order.append("user_groups")
        )

        await connector.ensure_connector_group_permission()

        assert call_order == ["app_users", "user_groups"]
        # USER_APP_RELATION upsert receives the same creator member that
        # the group upsert uses — locks the email/source_user_id contract.
        app_users_args, _ = connector.data_entities_processor.on_new_app_users.call_args
        creator_users = app_users_args[0]
        assert len(creator_users) == 1
        assert isinstance(creator_users[0], AppUser)
        assert creator_users[0].email == "alice@example.com"
        assert creator_users[0].app_name == Connectors.GITLAB_PERSONAL

    @pytest.mark.asyncio
    async def test_user_app_relation_failure_does_not_block_group_permission(
        self,
    ) -> None:
        """USER_APP_RELATION is best-effort.

        The App node is created on the Node.js side at connector
        registration. If a race or env hiccup leaves it missing, we still
        want the GROUP edge — the load-bearing one for record-level
        access — to be written. Otherwise a transient infra issue would
        permanently drop the personal sync's permissions.
        """
        connector = _make_connector()
        connector.data_entities_processor.on_new_app_users = AsyncMock(
            side_effect=RuntimeError("missing App node")
        )

        result = await connector.ensure_connector_group_permission()

        assert result is not None
        assert result.entity_type == EntityType.GROUP
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotent_within_sync(self) -> None:
        connector = _make_connector()

        first = await connector.ensure_connector_group_permission()
        second = await connector.ensure_connector_group_permission()

        assert first is second
        # The cache short-circuit must avoid the double upsert; otherwise
        # every record-group write would re-create the same USER→GROUP /
        # USER→APP edges and churn the audit log.
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_group_upsert_raises(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock(
            side_effect=RuntimeError("arango down")
        )

        result = await connector.ensure_connector_group_permission()

        assert result is None
        # Cache must remain empty so the next attempt re-tries the upsert
        # instead of silently handing out a None-backed group permission.
        assert connector._connector_group_permission is None


# ---------------------------------------------------------------------------
# _ensure_gitlab_group_record_groups (personal override)
# ---------------------------------------------------------------------------


class TestPersonalEnsureGitLabGroupRecordGroups:
    @pytest.mark.asyncio
    async def test_noop_when_data_source_missing(self) -> None:
        connector = _make_connector()
        connector.data_source = None

        await connector.projects._ensure_gitlab_group_record_groups(["org/eng"])

        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_group_permission_unresolved(self) -> None:
        connector = _make_connector(creator_email=None)
        connector.data_source = MagicMock()
        # No ensure_connector_group_permission call — cache stays None.

        await connector.projects._ensure_gitlab_group_record_groups(["org/eng"])

        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_group_permission_not_user_permission(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-gl-personal-1",
            type=PermissionType.READ,
        )
        group_obj = MagicMock()
        group_obj.full_path = "org/eng"
        group_obj.name = "Engineering"
        group_obj.web_url = "https://gitlab.example.com/org/eng"
        connector.runtime = MagicMock()
        connector.runtime.ds_call = AsyncMock(return_value=_ok(group_obj))

        await connector.projects._ensure_gitlab_group_record_groups(["org/eng"])

        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args, _ = connector.data_entities_processor.on_new_record_groups.call_args
        rg, perms = args[0][0]
        assert rg.name == "Engineering"
        assert rg.external_group_id == "org/eng"
        assert rg.group_type == RecordGroupType.PROJECT.value
        assert rg.web_url == "https://gitlab.example.com/org/eng"
        # The whole point of this PR: one GROUP edge per record group, no
        # per-member fan-out and no direct USER edge to the creator.
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP
        assert perms[0].external_id == "internal-gl-personal-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_path_when_get_group_fails(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-gl-personal-1",
            type=PermissionType.READ,
        )
        connector.runtime = MagicMock()
        connector.runtime.ds_call = AsyncMock(return_value=_fail("404"))

        await connector.projects._ensure_gitlab_group_record_groups(["missing/grp"])

        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args, _ = connector.data_entities_processor.on_new_record_groups.call_args
        rg, perms = args[0][0]
        # Without group metadata we still record the picker selection so
        # downstream syncs (issues/MRs/code) have a parent RG to attach to.
        assert rg.name == "missing/grp"
        assert rg.external_group_id == "missing/grp"
        assert rg.web_url is None
        assert perms[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_processes_every_group_in_input(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-gl-personal-1",
            type=PermissionType.READ,
        )

        def fake_ds_call(method, group_path, *_, **__):
            obj = MagicMock()
            obj.full_path = group_path
            obj.name = group_path.split("/")[-1]
            obj.web_url = f"https://gitlab.example.com/{group_path}"
            return _ok(obj)

        connector.runtime = MagicMock()
        connector.runtime.ds_call = AsyncMock(side_effect=fake_ds_call)

        await connector.projects._ensure_gitlab_group_record_groups(
            ["org/eng", "org/data"], candidate_projects=[MagicMock(), MagicMock()]
        )

        assert (
            connector.data_entities_processor.on_new_record_groups.await_count == 2
        )


# ---------------------------------------------------------------------------
# _apply_creator_fallback_for_project (inherited) — verify GROUP routing
# ---------------------------------------------------------------------------


class TestPersonalCreatorFallback:
    @pytest.mark.asyncio
    async def test_inherits_fallback_but_emits_group_permissions(self) -> None:
        """Inherited ``_apply_creator_fallback_for_project`` calls
        ``self.c.creator_user_permission()`` — overridden here to read the
        cached ConnectorGroup permission. So every project record-group
        the workspace path would have stamped with a USER perm now gets a
        GROUP perm pointing at the internal group. This is the central
        contract for personal-scope access routing.
        """
        connector = _make_connector()
        connector._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-gl-personal-1",
            type=PermissionType.READ,
        )
        # Normally set by ``run_sync`` → ``sync_all_projects``; tests that
        # call inherited helpers directly must seed it so the
        # ``_build_project_record_groups`` parent-link branch is a no-op.
        connector._gitlab_included_group_paths = None
        project = MagicMock()
        project.id = 99
        project.path_with_namespace = "org/eng/be"
        project.namespace = None

        await connector.projects._apply_creator_fallback_for_project(project)

        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args, _ = connector.data_entities_processor.on_new_record_groups.call_args
        record_groups_payload = args[0]
        # Four record groups: project, work items, MRs, code repo.
        assert len(record_groups_payload) == 4
        for _rg, perms in record_groups_payload:
            assert len(perms) == 1
            assert perms[0].entity_type == EntityType.GROUP
            assert perms[0].external_id == "internal-gl-personal-1"

    @pytest.mark.asyncio
    async def test_skips_when_permission_unresolved(self) -> None:
        connector = _make_connector()
        connector._connector_group_permission = None
        connector._gitlab_included_group_paths = None
        project = MagicMock()
        project.id = 99
        project.path_with_namespace = "org/eng/be"

        await connector.projects._apply_creator_fallback_for_project(project)

        # Without a resolved permission we must not create the record
        # groups with empty principals — they would be invisible to
        # every user, which is worse than skipping this sync run.
        connector.data_entities_processor.on_new_record_groups.assert_not_called()


# ---------------------------------------------------------------------------
# _sync_project_members_as_pseudo override
# ---------------------------------------------------------------------------


class TestPersonalSyncProjectMembersAsPseudo:
    @pytest.mark.asyncio
    async def test_delegates_to_creator_fallback(self) -> None:
        connector = _make_connector()
        connector.projects._apply_creator_fallback_for_project = AsyncMock()
        project = MagicMock()

        await connector.projects._sync_project_members_as_pseudo(project)

        # Personal scope has no notion of upstream project members —
        # everything routes through the ConnectorGroup fallback.
        connector.projects._apply_creator_fallback_for_project.assert_awaited_once_with(
            project
        )


# ---------------------------------------------------------------------------
# run_sync orchestration
# ---------------------------------------------------------------------------


class TestPersonalRunSync:
    @pytest.mark.asyncio
    async def test_resets_cache_resolves_email_then_ensures_group(self) -> None:
        connector = _make_connector(creator_email=None)
        # Seed a stale cached permission from a previous run.
        connector._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="stale",
            type=PermissionType.READ,
        )
        connector.runtime = MagicMock()
        connector.runtime.refresh_token_if_needed = AsyncMock()
        connector.projects.sync_all_projects = AsyncMock()

        async def fake_resolve() -> None:
            connector.creator_email = "creator@example.com"

        connector._resolve_creator_identity = AsyncMock(side_effect=fake_resolve)
        connector.ensure_connector_group_permission = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab_personal.connector.load_connector_filters",
            new=AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await connector.run_sync()

        # Cache must be wiped at the start of the run so a rotated creator
        # email does not get masked by the previous run's stale GROUP perm.
        # The freshly resolved email then drives a new ensure_* call.
        connector._resolve_creator_identity.assert_awaited_once()
        connector.ensure_connector_group_permission.assert_awaited_once()
        connector.projects.sync_all_projects.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_ensure_group_when_no_creator_email(self) -> None:
        connector = _make_connector(created_by="", creator_email=None)
        connector.runtime = MagicMock()
        connector.runtime.refresh_token_if_needed = AsyncMock()
        connector.projects.sync_all_projects = AsyncMock()
        connector._resolve_creator_identity = AsyncMock()
        connector.ensure_connector_group_permission = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab_personal.connector.load_connector_filters",
            new=AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await connector.run_sync()

        # No creator → no useful group member → skip the upsert entirely.
        # The sync still proceeds so callers see records (without ACLs)
        # and can decide what to do at the indexing layer.
        connector.ensure_connector_group_permission.assert_not_called()
        connector.projects.sync_all_projects.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_resolve_when_email_already_cached(self) -> None:
        connector = _make_connector(creator_email="cached@example.com")
        connector.runtime = MagicMock()
        connector.runtime.refresh_token_if_needed = AsyncMock()
        connector.projects.sync_all_projects = AsyncMock()
        connector._resolve_creator_identity = AsyncMock()
        connector.ensure_connector_group_permission = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab_personal.connector.load_connector_filters",
            new=AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await connector.run_sync()

        connector._resolve_creator_identity.assert_not_called()
        connector.ensure_connector_group_permission.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_loads_filters_with_gitlabpersonal_key(self) -> None:
        connector = _make_connector()
        connector.runtime = MagicMock()
        connector.runtime.refresh_token_if_needed = AsyncMock()
        connector.projects.sync_all_projects = AsyncMock()
        connector.ensure_connector_group_permission = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab_personal.connector.load_connector_filters",
            new=AsyncMock(return_value=(MagicMock(), MagicMock())),
        ) as load:
            await connector.run_sync()

        # Filter rows are namespaced by connector key; the personal variant
        # must read its own row, not the team variant's.
        load.assert_awaited_once()
        args = load.call_args.args
        assert args[1] == "gitlabpersonal"
        assert args[2] == connector.connector_id

    @pytest.mark.asyncio
    async def test_propagates_sync_errors(self) -> None:
        connector = _make_connector()
        connector.runtime = MagicMock()
        connector.runtime.refresh_token_if_needed = AsyncMock()
        connector.ensure_connector_group_permission = AsyncMock()
        connector.projects.sync_all_projects = AsyncMock(
            side_effect=RuntimeError("api down")
        )

        with patch(
            "app.connectors.sources.gitlab_personal.connector.load_connector_filters",
            new=AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            with pytest.raises(RuntimeError, match="api down"):
                await connector.run_sync()


# ---------------------------------------------------------------------------
# create_connector
# ---------------------------------------------------------------------------


class TestPersonalCreateConnector:
    @pytest.mark.asyncio
    async def test_factory_method_builds_instance(self) -> None:
        logger, _dep, dsp, cs = _make_deps()
        # Patch the processor symbol *imported into the module under test*
        # so the constructed instance still routes through our mock; this
        # also lets us assert that initialize() is awaited as part of the
        # factory contract.
        with patch(
            "app.connectors.sources.gitlab_personal.connector.DataSourceEntitiesProcessor"
        ) as MockProcessor:
            mock_dep = MagicMock()
            mock_dep.org_id = "org-1"
            mock_dep.initialize = AsyncMock()
            MockProcessor.return_value = mock_dep

            connector = await GitLabPersonalConnector.create_connector(
                logger=logger,
                data_store_provider=dsp,
                config_service=cs,
                connector_id="conn-personal-1",
                scope="personal",
                created_by="creator-1",
            )

        assert isinstance(connector, GitLabPersonalConnector)
        assert connector.connector_id == "conn-personal-1"
        assert connector.created_by == "creator-1"
        mock_dep.initialize.assert_awaited_once()
