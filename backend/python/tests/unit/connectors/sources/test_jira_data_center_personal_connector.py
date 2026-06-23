"""Unit tests for the Jira Data Center Personal connector and the
``BaseConnector.ensure_connector_group_permission`` helper exercised through it.

The personal connector subclasses the workspace ``JiraDataCenterConnector`` and
routes record-group access through a single pseudo ``AppUserGroup``
(``ConnectorGroup``) rather than direct USER grants. These tests pin down:

  * the helper's idempotency, identity scheme, and error fallback,
  * that ``run_sync`` resets the cache and calls the helper before fetching,
  * that ``_fetch_projects`` attaches the cached GROUP permission to every
    project record group.

We avoid the workspace path entirely (``_sync_all_project_issues`` etc.) —
those flows are owned by the parent connector's test module.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.sources.atlassian.core.apps import JiraDataCenterPersonalApp
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.models.entities import AppUser, AppUserGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test.jira.dc.personal")
    log.setLevel(logging.CRITICAL)
    return log


def _make_deps():
    """Minimal processor / data store / config service for connector ctor."""
    dep = MagicMock()
    dep.org_id = "org-personal-1"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_user_by_user_id = AsyncMock(return_value=None)
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return _make_logger(), dep, dsp, cs


def _make_connector(
    connector_id: str = "jdc-personal-1",
    created_by: str = "creator-user-1",
) -> JiraDataCenterPersonalConnector:
    logger, dep, dsp, cs = _make_deps()
    return JiraDataCenterPersonalConnector(
        logger, dep, dsp, cs, connector_id, "personal", created_by
    )


# -----------------------------------------------------------------------------
# App identity
# -----------------------------------------------------------------------------


class TestJiraDcPersonalAppIdentity:
    def test_app_class_returns_personal_enum(self) -> None:
        app = JiraDataCenterPersonalApp("cid-x")
        assert app.get_app_name() == Connectors.JIRA_DATA_CENTER_PERSONAL
        assert app.get_app_group_name() == AppGroups.ATLASSIAN

    def test_connector_overrides_app_and_name_after_super_init(self) -> None:
        conn = _make_connector()
        # Personal __init__ swaps the parent's JiraDataCenterApp for the
        # personal variant so downstream RecordGroup / AppUser construction
        # gets the JIRA_DATA_CENTER_PERSONAL connector name.
        assert isinstance(conn.app, JiraDataCenterPersonalApp)
        assert conn.connector_name == Connectors.JIRA_DATA_CENTER_PERSONAL


# -----------------------------------------------------------------------------
# ensure_connector_group_permission (BaseConnector helper) — exercised here
# -----------------------------------------------------------------------------


class TestEnsureConnectorGroupPermission:
    async def test_returns_none_when_no_creator_email(self) -> None:
        conn = _make_connector()
        conn.creator_email = None

        result = await conn.ensure_connector_group_permission()

        assert result is None
        assert conn._connector_group_permission is None
        conn.data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_creates_group_and_returns_group_permission(self) -> None:
        conn = _make_connector(connector_id="cid-42")
        conn.creator_email = "owner@example.com"

        result = await conn.ensure_connector_group_permission()

        assert result is not None
        assert result.entity_type == EntityType.GROUP
        assert result.type == PermissionType.READ
        # Stable external_id keyed by connector_id keeps the upsert idempotent
        # across runs and matches the on-disk historical scheme.
        assert result.external_id == "internal-cid-42"
        assert conn._connector_group_permission is result

        conn.data_entities_processor.on_new_user_groups.assert_awaited_once()
        call_args = conn.data_entities_processor.on_new_user_groups.call_args
        groups_arg = call_args.args[0]
        assert len(groups_arg) == 1
        group, members = groups_arg[0]

        assert isinstance(group, AppUserGroup)
        assert group.name == "ConnectorGroup"
        assert group.app_name == Connectors.JIRA_DATA_CENTER_PERSONAL
        assert group.connector_id == "cid-42"
        assert group.source_user_group_id == "internal-cid-42"
        assert group.org_id == "org-personal-1"

        assert len(members) == 1
        member = members[0]
        assert isinstance(member, AppUser)
        # Member lookup in on_new_user_groups is by email; the email is the
        # only field that has to be correct.
        assert member.email == "owner@example.com"
        assert member.app_name == Connectors.JIRA_DATA_CENTER_PERSONAL
        assert member.connector_id == "cid-42"
        assert member.is_active is True

    async def test_idempotent_within_run_uses_cache(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"

        first = await conn.ensure_connector_group_permission()
        second = await conn.ensure_connector_group_permission()

        assert first is second
        # Second call must not re-upsert the group; the cached permission is
        # what every record-group / record path consumes downstream.
        conn.data_entities_processor.on_new_user_groups.assert_awaited_once()

    async def test_returns_none_when_upsert_fails(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_entities_processor.on_new_user_groups = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        result = await conn.ensure_connector_group_permission()

        # A failed upsert must not leave a half-cached permission that future
        # record-group writes would emit and fail to resolve.
        assert result is None
        assert conn._connector_group_permission is None

    async def test_accepts_string_connector_name(self) -> None:
        """Some connector subclasses store ``connector_name`` as the string
        ``.value`` rather than the ``Connectors`` enum. The helper must coerce
        instead of raising at the ``AppUserGroup`` pydantic boundary.
        """
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.connector_name = Connectors.JIRA_DATA_CENTER_PERSONAL.value  # str

        result = await conn.ensure_connector_group_permission()

        assert result is not None
        call_args = conn.data_entities_processor.on_new_user_groups.call_args
        group, _ = call_args.args[0][0]
        assert group.app_name == Connectors.JIRA_DATA_CENTER_PERSONAL


# -----------------------------------------------------------------------------
# _fetch_projects: GROUP permission emission
# -----------------------------------------------------------------------------


class TestFetchProjectsEmitsGroupPermission:
    async def test_attaches_connector_group_permission_to_each_project(self) -> None:
        conn = _make_connector(connector_id="cid-fp")
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()

        raw_projects = [
            {"id": "10001", "key": "ALPHA", "name": "Alpha", "url": "https://j/ALPHA"},
            {"id": "10002", "key": "BETA", "name": "Beta", "url": "https://j/BETA"},
        ]
        conn._list_all_projects_dc = AsyncMock(return_value=raw_projects)

        record_groups, returned_raw = await conn._fetch_projects()

        assert returned_raw == raw_projects
        assert len(record_groups) == 2
        # All projects share the same cached GROUP permission instance — that
        # one upsert is the entire ACL surface for this connector.
        perm_a = record_groups[0][1][0]
        perm_b = record_groups[1][1][0]
        assert perm_a.entity_type == EntityType.GROUP
        assert perm_a.external_id == "internal-cid-fp"
        assert perm_b.external_id == "internal-cid-fp"
        # Idempotency check: ``_fetch_projects`` lazily calls the helper if
        # ``run_sync`` didn't, so a single project list still produces a
        # single user-group upsert (not one per project).
        conn.data_entities_processor.on_new_user_groups.assert_awaited_once()

        rg_alpha, _ = record_groups[0]
        assert rg_alpha.external_group_id == "10001"
        assert rg_alpha.short_name == "ALPHA"
        assert rg_alpha.group_type == RecordGroupType.PROJECT
        assert rg_alpha.connector_name == Connectors.JIRA_DATA_CENTER_PERSONAL

    async def test_skips_permissions_when_no_creator(self) -> None:
        conn = _make_connector()
        conn.creator_email = None
        conn.data_source = MagicMock()
        conn._list_all_projects_dc = AsyncMock(
            return_value=[{"id": "10001", "key": "ALPHA", "name": "Alpha"}]
        )

        record_groups, _ = await conn._fetch_projects()

        assert len(record_groups) == 1
        # Without a resolvable creator we emit the project group with no ACLs
        # rather than fabricating an unowned grant.
        assert record_groups[0][1] == []
        conn.data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_applies_include_project_key_filter(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()
        conn._list_all_projects_dc = AsyncMock(
            return_value=[
                {"id": "1", "key": "KEEP", "name": "Keep"},
                {"id": "2", "key": "DROP", "name": "Drop"},
            ]
        )

        record_groups, _ = await conn._fetch_projects(project_keys=["KEEP"])

        assert [rg.external_group_id for rg, _ in record_groups] == ["1"]


# -----------------------------------------------------------------------------
# run_sync orchestration
# -----------------------------------------------------------------------------


class TestPersonalRunSyncOrchestration:
    async def test_run_sync_resets_cache_and_ensures_group(self) -> None:
        conn = _make_connector()
        conn.data_source = MagicMock()
        # Pretend a previous run had cached a permission; run_sync must clear
        # it so a rotated creator email picks up the new identity.
        conn._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="stale",
            type=PermissionType.READ,
        )
        conn.creator_email = "owner@example.com"

        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0}
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        # Helper invoked exactly once and produces a fresh, non-stale permission.
        conn.data_entities_processor.on_new_user_groups.assert_awaited_once()
        assert conn._connector_group_permission is not None
        assert conn._connector_group_permission.external_id != "stale"
        # Downstream steps still run.
        conn._fetch_projects.assert_awaited_once()
        conn._sync_all_project_issues.assert_awaited_once()


# -----------------------------------------------------------------------------
# run_sync edge cases, init, creator lookup, filters
# -----------------------------------------------------------------------------


class TestPersonalRunSyncEdgeCases:
    async def test_run_sync_raises_when_init_fails(self) -> None:
        conn = _make_connector()
        conn.data_source = None
        conn.init = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="init failed"):
            await conn.run_sync()

    async def test_run_sync_calls_init_when_data_source_missing(self) -> None:
        conn = _make_connector()
        conn.data_source = None
        conn.init = AsyncMock(return_value=True)
        conn.creator_email = "owner@example.com"
        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        conn.init.assert_awaited_once()

    async def test_run_sync_resolves_creator_email_from_created_by(self) -> None:
        conn = _make_connector(created_by="creator-id-99")
        conn.data_source = MagicMock()
        conn.creator_email = None

        resolved_user = MagicMock()
        resolved_user.email = "resolved@example.com"
        conn.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=resolved_user,
        )

        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        conn.data_entities_processor.get_user_by_user_id.assert_awaited_once_with(
            "creator-id-99",
        )
        assert conn.creator_email == "resolved@example.com"
        conn.data_entities_processor.on_new_user_groups.assert_awaited_once()

    async def test_run_sync_creator_lookup_exception_is_logged(self) -> None:
        conn = _make_connector(created_by="creator-id")
        conn.data_source = MagicMock()
        conn.creator_email = None
        conn.data_entities_processor.get_user_by_user_id = AsyncMock(
            side_effect=RuntimeError("lookup failed"),
        )
        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        assert conn.creator_email is None
        conn.data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_run_sync_no_creator_email_logs_and_syncs_without_permissions(self) -> None:
        conn = _make_connector(created_by="")
        conn.data_source = MagicMock()
        conn.creator_email = None
        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        conn._fetch_projects.assert_awaited_once()

    async def test_run_sync_project_keys_filter_in_logs(self) -> None:
        from app.connectors.core.registry.filters import ListOperator, SyncFilterKey

        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.creator_email = "owner@example.com"

        pfilter = MagicMock()
        pfilter.get_value = MagicMock(return_value=["ALPHA", "BETA"])
        pfilter.get_operator = MagicMock(return_value=ListOperator.IN)
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(
            side_effect=lambda k: pfilter if k == SyncFilterKey.PROJECT_KEYS else None,
        )

        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(sync_filters, None),
        ):
            await conn.run_sync()

        conn._fetch_projects.assert_awaited_once_with(
            ["ALPHA", "BETA"],
            ListOperator.IN,
            [],
        )

    async def test_run_sync_project_keys_filter_not_in_logs(self) -> None:
        from app.connectors.core.registry.filters import ListOperator, SyncFilterKey

        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.creator_email = "owner@example.com"

        pfilter = MagicMock()
        pfilter.get_value = MagicMock(return_value=["SKIP"])
        pfilter.get_operator = MagicMock(return_value=ListOperator.NOT_IN)
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(
            side_effect=lambda k: pfilter if k == SyncFilterKey.PROJECT_KEYS else None,
        )

        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(sync_filters, None),
        ):
            await conn.run_sync()

        conn._fetch_projects.assert_awaited_once_with(
            ["SKIP"],
            ListOperator.NOT_IN,
            [],
        )

    async def test_run_sync_empty_project_keys_filter_syncs_all(self) -> None:
        from app.connectors.core.registry.filters import SyncFilterKey

        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.creator_email = "owner@example.com"

        pfilter = MagicMock()
        pfilter.get_value = MagicMock(return_value=[])
        pfilter.get_operator = MagicMock(return_value=None)
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(
            side_effect=lambda k: pfilter if k == SyncFilterKey.PROJECT_KEYS else None,
        )

        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(sync_filters, None),
        ):
            await conn.run_sync()

        conn._fetch_projects.assert_awaited_once_with(None, None, [])

    async def test_run_sync_with_sync_filters_but_no_project_keys(self) -> None:
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.creator_email = "owner@example.com"
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(return_value=None)

        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
        )
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_issues_sync_checkpoint = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(sync_filters, None),
        ):
            await conn.run_sync()

        conn._fetch_projects.assert_awaited_once_with(None, None, [])

    async def test_run_sync_error_is_logged_and_reraised(self) -> None:
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.creator_email = "owner@example.com"
        conn._fetch_projects = AsyncMock(side_effect=RuntimeError("sync boom"))

        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            with pytest.raises(RuntimeError, match="sync boom"):
                await conn.run_sync()


# -----------------------------------------------------------------------------
# _fetch_projects edge cases
# -----------------------------------------------------------------------------


class TestFetchProjectsEdgeCases:
    async def test_raises_when_data_source_not_initialized(self) -> None:
        conn = _make_connector()
        conn.data_source = None

        with pytest.raises(ValueError, match="DataSource not initialized"):
            await conn._fetch_projects()

    async def test_applies_exclude_project_key_filter(self) -> None:
        from app.connectors.core.registry.filters import ListOperator

        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()
        conn._list_all_projects_dc = AsyncMock(
            return_value=[
                {"id": "1", "key": "KEEP", "name": "Keep"},
                {"id": "2", "key": "DROP", "name": "Drop"},
            ],
        )

        record_groups, raw = await conn._fetch_projects(
            project_keys=["DROP"],
            project_keys_operator=ListOperator.NOT_IN,
        )

        assert [p["key"] for p in raw] == ["KEEP"]
        assert [rg.short_name for rg, _ in record_groups] == ["KEEP"]

    async def test_syncs_all_projects_when_no_key_filter(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()
        conn._list_all_projects_dc = AsyncMock(
            return_value=[
                {"id": "1", "key": "A", "name": "A"},
                {"id": "2", "key": "B", "name": "B"},
            ],
        )

        record_groups, raw = await conn._fetch_projects()

        assert len(raw) == 2
        assert len(record_groups) == 2

    async def test_non_string_description_becomes_none(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()
        conn._list_all_projects_dc = AsyncMock(
            return_value=[
                {"id": "1", "key": "X", "name": "X", "description": {"type": "doc"}},
                {"id": "2", "key": "Y", "name": "Y", "description": ""},
            ],
        )

        record_groups, _ = await conn._fetch_projects()

        assert record_groups[0][0].description is None
        assert record_groups[1][0].description is None

    async def test_plain_string_description_preserved(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()
        conn._list_all_projects_dc = AsyncMock(
            return_value=[
                {"id": "1", "key": "X", "name": "X", "description": "Summary text"},
            ],
        )

        record_groups, _ = await conn._fetch_projects()

        assert record_groups[0][0].description == "Summary text"

    async def test_logs_debug_when_project_permissions_present(self) -> None:
        conn = _make_connector()
        conn.creator_email = "owner@example.com"
        conn.data_source = MagicMock()
        conn._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-cid",
            type=PermissionType.READ,
        )
        conn._list_all_projects_dc = AsyncMock(
            return_value=[{"id": "1", "key": "X", "name": "X"}],
        )

        with patch.object(conn.logger, "debug") as mock_debug:
            await conn._fetch_projects()

        mock_debug.assert_called_once()
        assert "ConnectorGroup" in mock_debug.call_args[0][0]


# -----------------------------------------------------------------------------
# create_connector factory entry point
# -----------------------------------------------------------------------------


class TestPersonalCreateConnector:
    async def test_create_connector_returns_personal_subclass(self) -> None:
        logger, _dep, dsp, cs = _make_deps()
        with patch(
            "app.connectors.sources.atlassian.jira_data_center_personal.connector.DataSourceEntitiesProcessor",
        ) as MockProc:
            mock_proc = MagicMock()
            mock_proc.org_id = "org-x"
            mock_proc.initialize = AsyncMock()
            MockProc.return_value = mock_proc

            instance = await JiraDataCenterPersonalConnector.create_connector(
                logger=logger,
                data_store_provider=dsp,
                config_service=cs,
                connector_id="cid-create",
                scope="personal",
                created_by="u1",
            )

        assert isinstance(instance, JiraDataCenterPersonalConnector)
        assert instance.connector_id == "cid-create"
        assert instance.connector_name == Connectors.JIRA_DATA_CENTER_PERSONAL
        mock_proc.initialize.assert_awaited_once()
