"""The licence gate, design B: an unlicensed company-wide connector is limited to its creator and copies no access.

- Sync start (``apply_licence_gate``) and startup (the registry sweep) write ``accessLimit: CREATOR`` and
  ``permissionModel: APP_LEVEL``; a failure raises and the sync does not start.
- While unlicensed, every permission write in the processor leaves what is stored alone; content is still written.
- Licensed again, writes resume, the connector owes a full sync, and only a finished full sync lifts the limit.
- With no licence config (no key yet), nothing changes. Open types and personal connectors are never limited.
"""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    AccessLimit,
    AppStatus,
    CollectionNames,
    Connectors,
    PermissionModel,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_processor.permission_sync_gate import (
    OPEN_CONNECTOR_TYPES,
    PERMISSION_SYNC_LICENCE_KEY,
    gated,
    permission_sync_licensed,
)
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.sync.sync_runner import run_sync_task
from app.models.entities import AppRole, AppUser, AppUserGroup, RecordGroup
from app.models.permission import EntityType, Permission, PermissionType
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_ctx,
    _make_processor,
    _make_record,
    _make_tx_store,
)

_LOG = logging.getLogger("test.licence_gate")
CONNECTOR = "conn-1"
UNLICENSED = {"allowed": False}
LICENSED = {"allowed": True}
GROUPS = CollectionNames.GROUPS.value
ROLES = CollectionNames.ROLES.value
RECORDS = CollectionNames.RECORDS.value
APPS = CollectionNames.APPS.value


def _app(type_: str = "JIRA", scope: str = "team", limit: str | None = None, model: str | None = None) -> dict:
    return {"_key": CONNECTOR, "type": type_, "scope": scope, "createdBy": "u-creator", "accessLimit": limit,
            "permissionModel": model or (PermissionModel.APP_LEVEL.value if limit else PermissionModel.RECORD_LEVEL.value)}


_NOTIFY = "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_permissions_changed"


def _real_write(proc: DataSourceEntitiesProcessor, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Use the real set_access_limit: the transaction (where the store bumps the generation) and the two refreshes
    are recorded. Returns the search-cache drop."""
    del proc.set_access_limit
    proc.data_store_provider.execute_in_transaction = AsyncMock()
    proc.data_store_provider.graph_provider.kh_scope_mark_changed = AsyncMock()
    drop = AsyncMock()
    monkeypatch.setattr(_NOTIFY, drop)
    return drop


def _config(value: object) -> MagicMock:
    config = MagicMock()
    config.get_config = AsyncMock(return_value=value)
    return config


def _gate_processor(
    licence: object, app: dict | None = None, *, initialized: bool = True,
) -> tuple[DataSourceEntitiesProcessor, AsyncMock]:
    """The shared processor test double, with a licence and the connector's app document."""
    proc, tx_store = _make_processor(), _make_tx_store()
    proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
    proc.config_service = _config(licence)
    graph = proc.data_store_provider.graph_provider
    graph.get_document = AsyncMock(return_value=_app() if app is None else app)
    graph.update_node = AsyncMock(return_value=True)
    if initialized:
        proc._reads_licence = True
    proc.set_access_limit = AsyncMock()
    proc.clear_access_limit = AsyncMock()
    return proc, tx_store


def _no_writes(tx_store: AsyncMock) -> list[str]:
    """The write calls the store saw (reads are allowed)."""
    return [c[0] for c in tx_store.method_calls if not c[0].startswith("get_")]


# ---- the licence and the connector rule ----

@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "licensed"), [
    (None, True),  # no licence config yet: nothing changes before licence keys exist
    (LICENSED, True),
    (UNLICENSED, False),
    ({}, False),
    # Exactly what the Node licence check writes (license.service.ts): ``allowed`` decides, unless ``until`` has passed.
    ({"allowed": True, "until": "2999-12-23T12:00:00.000Z", "status": "active"}, True),
    ({"allowed": True, "until": "2999-12-23T12:00:00.000Z", "status": "grace"}, True),
    ({"allowed": True, "until": "2999-12-23T12:00:00.000Z", "status": "expiring"}, True),
    ({"allowed": True, "until": None, "status": "active"}, True),  # no end given
    ({"allowed": False, "until": None, "status": "expired"}, False),
    ({"allowed": False, "until": None, "status": "active"}, False),  # a licence without the feature
    ({"allowed": False, "until": None, "status": "none"}, False),  # files installed, none verifying here
    # The backstop: an answer whose ``until`` has passed is stale (Node stopped checking), so it is not licensed.
    ({"allowed": True, "until": "2000-01-01T00:00:00.000Z", "status": "active"}, False),
    ({"allowed": True, "until": "2000-01-01T00:00:00+05:30", "status": "grace"}, False),
    ({"allowed": True, "until": "2000-01-01T00:00:00.000Z", "status": "expiring"}, False),
    # An ``until`` that is not an ISO time with a zone is malformed: not licensed.
    ({"allowed": True, "until": "soon", "status": "active"}, False),
    ({"allowed": True, "until": 4102444800, "status": "active"}, False),
    ({"allowed": True, "until": "2999-12-23T12:00:00", "status": "active"}, False),
])
async def test_the_licence_is_read_from_the_config_store(value: object, licensed: bool) -> None:
    config = _config(value)
    assert await permission_sync_licensed(config) is licensed
    assert config.get_config.await_args.args[0] == PERMISSION_SYNC_LICENCE_KEY
    assert config.get_config.await_args.kwargs["raise_on_error"] is True


@pytest.mark.asyncio
async def test_a_failed_licence_read_raises_and_is_not_remembered() -> None:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=[ConnectionError("store down"), UNLICENSED])
    with pytest.raises(ConnectionError):
        await permission_sync_licensed(config)
    assert await permission_sync_licensed(config) is False, "the next call reads the store again"
    assert config.get_config.await_count == 2


@pytest.mark.asyncio
async def test_a_missing_key_is_read_once_across_many_records() -> None:
    """The config cache keeps no entry for a missing key; the gate remembers the answer itself."""
    proc, tx_store = _gate_processor(None)
    records = []
    for n in range(20):
        record = _make_record(external_record_id=f"ext-{n}")
        record.id = f"rec-{n}"
        records.append((record, [Permission(type=PermissionType.READ, entity_type=EntityType.ORG)]))

    await proc.on_new_records(records)

    proc.config_service.get_config.assert_awaited_once()
    tx_store.batch_upsert_records.assert_awaited()


def test_the_open_types_are_storage_database_web_and_collections() -> None:
    assert OPEN_CONNECTOR_TYPES == {
        "S3", "MINIO", "GCS", "AZURE BLOB", "AZURE FILES", "SMB", "CIFS", "POSTGRESQL", "MARIADB", "SNOWFLAKE", "WEB",
        "RSS", "KB",
    }


@pytest.mark.parametrize("type_", ["S3", "MinIO", "GCS", "Azure Blob", "Azure Files", "SMB", "CIFS", "PostgreSQL",
                                   "MariaDB", "Snowflake", "Web", "RSS", "KB"])
def test_open_types_are_never_gated(type_: str) -> None:
    assert gated(_app(type_)) is False


@pytest.mark.parametrize(("type_", "scope", "expected"), [
    ("JIRA", "team", True),
    ("Confluence", "TEAM", True),
    ("Drive Workspace", "team", True),
    ("SharePoint Online", "team", True),
    ("Jira", "personal", False),
    ("Drive", "personal", False),
])
def test_only_company_wide_connectors_that_read_source_access_are_gated(type_: str, scope: str, expected: bool) -> None:
    assert gated(_app(type_, scope)) is expected


def test_a_missing_app_is_not_gated() -> None:
    assert gated(None) is False


# ---- who is unlicensed ----

@pytest.mark.asyncio
async def test_a_processor_not_yet_initialized_changes_nothing() -> None:
    proc, _ = _gate_processor(UNLICENSED, initialized=False)
    assert await proc.skips_permission_writes(CONNECTOR) is False
    assert await proc.apply_licence_gate(CONNECTOR, full_sync=False) is False
    proc.config_service.get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_licensed_install_reads_no_connector() -> None:
    proc, _ = _gate_processor(LICENSED)
    assert await proc.skips_permission_writes(CONNECTOR) is False
    proc.data_store_provider.graph_provider.get_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_connector_is_read_once_and_a_failed_read_raises() -> None:
    proc, _ = _gate_processor(UNLICENSED)
    assert await proc.skips_permission_writes(CONNECTOR) is True
    assert await proc.skips_permission_writes(CONNECTOR) is True
    proc.data_store_provider.graph_provider.get_document.assert_awaited_once_with(CONNECTOR, APPS, raise_on_error=True)

    failing, _ = _gate_processor(UNLICENSED)
    failing.data_store_provider.graph_provider.get_document = AsyncMock(side_effect=ConnectionError("down"))
    with pytest.raises(ConnectionError):
        await failing.skips_permission_writes(CONNECTOR)


@pytest.mark.asyncio
@pytest.mark.parametrize("app", [_app("S3"), _app("Web"), _app("JIRA", "personal")])
async def test_open_and_personal_connectors_write_as_before(app: dict) -> None:
    proc, tx_store = _gate_processor(UNLICENSED, app)
    record = _make_record()
    record.id = "rec-1"
    grant = Permission(type=PermissionType.READ, entity_type=EntityType.ORG)

    await proc._handle_record_permissions(record, [grant], tx_store)

    assert tx_store.batch_create_edges.await_count == 1
    assert await proc.apply_licence_gate(CONNECTOR, full_sync=True) is False
    proc.set_access_limit.assert_not_awaited()


# ---- sync start: the limit ----

@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "model"), [
    (None, None),
    (AccessLimit.CREATOR.value, PermissionModel.RECORD_LEVEL.value),  # the field without the model: written again
])
async def test_an_unlicensed_start_writes_the_limit_unless_it_is_stored_whole(
    stored: str | None, model: str | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc, _ = _gate_processor(UNLICENSED, _app(limit=stored, model=model))
    drop = _real_write(proc, monkeypatch)

    assert await proc.apply_licence_gate(CONNECTOR, full_sync=False) is False

    proc.data_store_provider.execute_in_transaction.assert_awaited_once()
    proc.data_store_provider.graph_provider.kh_scope_mark_changed.assert_awaited_once_with(CONNECTOR)
    drop.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_already_limited_connector_is_not_written_again_but_is_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The generation bump committed with the stored limit; writing again would bump it, and re-stamp the whole
    connector, on every sync. Only the refreshes after the commit, which a crash can leave undone, run again."""
    proc, _ = _gate_processor(UNLICENSED, _app(limit=AccessLimit.CREATOR.value))
    drop = _real_write(proc, monkeypatch)

    await proc.apply_licence_gate(CONNECTOR, full_sync=False)

    proc.data_store_provider.execute_in_transaction.assert_not_awaited()
    proc.data_store_provider.graph_provider.kh_scope_mark_changed.assert_awaited_once_with(CONNECTOR)
    drop.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_stored_org_limit_is_kept_not_narrowed() -> None:
    proc, _ = _gate_processor(UNLICENSED, _app(limit=AccessLimit.ORG.value, model=PermissionModel.RECORD_LEVEL.value))
    await proc.apply_licence_gate(CONNECTOR, full_sync=False)
    proc.set_access_limit.assert_awaited_once_with(CONNECTOR, AccessLimit.ORG)


@pytest.mark.asyncio
async def test_no_licence_config_changes_nothing_at_start() -> None:
    proc, _ = _gate_processor(None)
    assert await proc.apply_licence_gate(CONNECTOR, full_sync=True) is False
    proc.set_access_limit.assert_not_awaited()
    proc.data_store_provider.graph_provider.update_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_and_limited_an_incremental_start_owes_a_full_sync() -> None:
    proc, _ = _gate_processor(LICENSED, _app(limit=AccessLimit.CREATOR.value))
    assert await proc.apply_licence_gate(CONNECTOR, full_sync=False) is False
    proc.data_store_provider.graph_provider.update_node.assert_awaited_once_with(
        CONNECTOR, APPS, {ConnectorStateKeys.PENDING_FULL_SYNC: True}
    )
    proc.set_access_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_and_limited_a_full_start_is_the_one_that_may_lift() -> None:
    proc, _ = _gate_processor(LICENSED, _app(limit=AccessLimit.CREATOR.value))
    assert await proc.apply_licence_gate(CONNECTOR, full_sync=True) is True
    proc.data_store_provider.graph_provider.update_node.assert_not_awaited()


# ---- a refresh that failed after the commit: retried at the next start, and the sync does not start ----

@pytest.mark.asyncio
async def test_a_refresh_that_failed_after_the_commit_is_run_again_at_the_next_start(monkeypatch: pytest.MonkeyPatch) -> None:
    proc, _ = _gate_processor(UNLICENSED)
    drop = _real_write(proc, monkeypatch)
    graph = proc.data_store_provider.graph_provider
    graph.kh_scope_mark_changed = AsyncMock(side_effect=[ConnectionError("stamps down"), None])

    with pytest.raises(ConnectionError):
        await proc.apply_licence_gate(CONNECTOR, full_sync=False)

    # The write landed: the stored app now carries the limit, so the next start runs the refreshes alone.
    graph.get_document = AsyncMock(return_value=_app(limit=AccessLimit.CREATOR.value))
    await proc.apply_licence_gate(CONNECTOR, full_sync=False)

    assert proc.data_store_provider.execute_in_transaction.await_count == 1
    assert graph.kh_scope_mark_changed.await_count == 2
    assert drop.await_count == 2, "the cache is dropped even when the mark failed"


@pytest.mark.asyncio
async def test_a_failed_refresh_of_a_stored_limit_stops_the_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(limit=AccessLimit.CREATOR.value)
    proc, _ = _gate_processor(UNLICENSED, app)
    _real_write(proc, monkeypatch)
    proc.data_store_provider.graph_provider.kh_scope_mark_changed = AsyncMock(side_effect=ConnectionError("stamps down"))
    gp = _runner_graph(app)
    _wire(proc, gp)
    connector = _connector(proc)
    connector.run_sync = AsyncMock()

    with pytest.raises(ConnectionError):
        await run_sync_task(connector, CONNECTOR, gp, _LOG, start_status=AppStatus.SYNCING.value)

    connector.run_sync.assert_not_awaited()
    proc.data_store_provider.execute_in_transaction.assert_not_awaited()


# ---- while unlicensed: permission writes are skipped, content is not ----

@pytest.mark.asyncio
async def test_record_grants_are_skipped_but_the_record_is_written() -> None:
    proc, tx_store = _gate_processor(UNLICENSED)
    tx_store.replace_edges_to = AsyncMock()
    record = _make_record()
    record.id = "rec-1"
    grant = Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="a@x.com")

    await proc.on_new_records([(record, [grant])])

    tx_store.batch_upsert_records.assert_awaited()
    tx_store.replace_edges_to.assert_not_awaited()
    assert not [c for c in tx_store.batch_create_edges.await_args_list
                if c.kwargs.get("collection") == CollectionNames.PERMISSION.value]
    tx_store.get_user_by_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_record_group_keeps_its_stored_grants_and_is_written() -> None:
    proc, tx_store = _gate_processor(UNLICENSED)
    tx_store.replace_edges_to = AsyncMock()
    group = RecordGroup(external_group_id="g1", name="Space", group_type="CONFLUENCE_SPACES",
                        connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR)

    await proc.on_new_record_groups([(group, [])])  # [] would mean "the source has none" and clear them

    tx_store.batch_upsert_record_groups.assert_awaited()
    tx_store.replace_edges_to.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_permission_update_moves_the_record_but_leaves_its_access() -> None:
    proc, tx_store = _gate_processor(UNLICENSED)
    tx_store.get_edges_from_node.return_value = [{"_to": "recordGroups/g1"}]
    tx_store.replace_record_permissions = AsyncMock()
    record = _make_record()
    record.id = "rec-1"

    await proc.on_updated_record_permissions(record, [Permission(type=PermissionType.READ, entity_type=EntityType.ORG)])

    tx_store.replace_record_permissions.assert_not_awaited()
    tx_store.batch_update_nodes.assert_not_awaited()  # the access rule


@pytest.mark.asyncio
async def test_a_slack_file_keeps_its_share_edges_on_a_permission_update() -> None:
    """A Slack file lists every channel it is shared to (``shared_groups_complete``): licensed, a channel it left loses
    its edges. Unlicensed, no share or group edge is written or removed."""
    proc, tx_store = _gate_processor(UNLICENSED, _app("SLACK WORKSPACE"))
    tx_store.get_edges_from_node.return_value = [{"_to": "recordGroups/c-old"}]
    tx_store.get_record_group_by_external_id.return_value = MagicMock(id="c-new")
    tx_store.replace_record_permissions = AsyncMock()
    proc._drop_shared_groups = AsyncMock()
    record = _make_record(connector_name=Connectors.SLACK_WORKSPACE, external_record_group_id="c-new")
    record.id = "rec-1"
    record.shared_with_me_record_group_ids = ["c-new"]
    record.shared_groups_complete = True

    await proc.on_updated_record_permissions(record, [])

    proc._drop_shared_groups.assert_not_awaited()
    tx_store.create_record_group_relation.assert_not_awaited()
    tx_store.create_inherit_permissions_relation_record_group.assert_not_awaited()
    tx_store.replace_record_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_record_write_leaves_its_inherit_and_share_edges_as_stored() -> None:
    proc, tx_store = _gate_processor(UNLICENSED, _app("SLACK WORKSPACE"))
    tx_store.link_record_to_group = AsyncMock(return_value=0)
    proc._drop_shared_groups = AsyncMock()
    stored = _make_record(connector_name=Connectors.SLACK_WORKSPACE)
    stored.id = "rec-1"
    stored.record_group_id = "c-old"
    record = _make_record(connector_name=Connectors.SLACK_WORKSPACE)
    record.id = "rec-1"
    record.shared_with_me_record_group_ids = ["c-new"]
    record.shared_groups_complete = True

    await proc._link_record_to_group(record, "c-old", tx_store, stored)

    assert tx_store.link_record_to_group.await_args.kwargs["inherit"] is None, "the inherit edge is left alone"
    proc._drop_shared_groups.assert_not_awaited()
    tx_store.create_record_group_relation.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_issue_keeps_its_stored_access_group_and_rule() -> None:
    """The payload's rule and access group are not applied: the stored group is kept (and counted as seen by
    ``_keep_access_group``), the stored rule is written back, and no access-group edge is touched."""
    from app.config.constants.arangodb import AccessRule
    from app.models.entities import TicketRecord

    proc, tx_store = _gate_processor(UNLICENSED)
    stored = _make_record(access_rule=AccessRule.RESTRICTED)
    stored.id = "rec-1"
    tx_store.get_record_by_external_id = AsyncMock(return_value=stored)
    proc._keep_access_group = AsyncMock()
    proc._apply_access_group = AsyncMock()
    written: list[AccessRule] = []

    async def body(record: object, *_a: object, **_k: object) -> tuple:
        written.append(record.access_rule)
        return record, []

    proc._process_record_body = body
    issue = TicketRecord(
        org_id="org-1", external_record_id="ext-1", record_name="JIRA-1", origin="CONNECTOR",
        connector_name=Connectors.JIRA, connector_id=CONNECTOR, record_type="TICKET", version=1,
        mime_type="text/plain", access_rule=AccessRule.OPEN, access_group_external_id="level-new",
    )

    await proc._process_record(issue, [], tx_store)

    proc._keep_access_group.assert_awaited_once()
    proc._apply_access_group.assert_not_awaited()
    assert written == [AccessRule.RESTRICTED]


@pytest.mark.asyncio
async def test_groups_roles_and_links_are_skipped() -> None:
    proc, tx_store = _gate_processor(UNLICENSED)
    user = AppUser(app_name=Connectors.JIRA, connector_id=CONNECTOR, source_user_id="s1", email="a@x.com", full_name="A")
    group = AppUserGroup(app_name=Connectors.JIRA, connector_id=CONNECTOR, source_user_group_id="g1", name="G")
    role = AppRole(app_name=Connectors.JIRA, connector_id=CONNECTOR, source_role_id="r1", name="R")

    await proc.on_new_app_users([user])
    await proc.on_new_user_groups([(group, [user])])
    await proc.on_new_app_roles([(role, [user])])
    await proc.batch_upsert_user_groups([group])
    await proc.on_external_app_users(["x@y.com"], CONNECTOR)
    await proc.withdraw_app_user_access(CONNECTOR, [user], drop_grants=True)
    await proc.link_authenticator_to_source_user(CONNECTOR, "u-creator", "src@x.com", "s9", Connectors.JIRA)
    await proc.unlink_authenticator(CONNECTOR)
    assert await proc.reap_external_app_users(CONNECTOR) == 0
    await proc.ensure_team_app_edge(CONNECTOR)
    assert await proc.open_app_to_org_team(CONNECTOR) is False
    await proc.withdraw_team_app_edge(CONNECTOR)
    assert await proc.on_user_group_member_added("g1", "a@x.com", PermissionType.READ, CONNECTOR) is False
    assert await proc.on_user_group_member_removed("g1", "a@x.com", CONNECTOR) is False
    assert await proc.create_user_group_membership("s1", "g1", CONNECTOR) is False
    assert await proc.on_user_group_deleted("g1", CONNECTOR) is False
    assert await proc.on_app_role_deleted("r1", CONNECTOR) is False
    assert await proc.update_user_group_name("g1", "New", CONNECTOR) is False
    assert await proc.migrate_group_permissions_to_user("g1", "a@x.com", CONNECTOR) is False
    await proc.migrate_group_to_user_by_external_id("g1", "a@x.com", CONNECTOR)
    await proc.finish_full_sync_memberships(CONNECTOR, MagicMock())
    await proc.delete_edges_between_collections("u1", "users", "permission", "roles", connector_id=CONNECTOR)

    assert _no_writes(tx_store) == []
    proc.data_store_provider.transaction.assert_not_called()


@pytest.mark.asyncio
async def test_grant_edits_and_withdrawals_are_skipped() -> None:
    proc, tx_store = _gate_processor(UNLICENSED)
    record = _make_record()
    record.id = "rec-1"
    grant = Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="a@x.com")

    await proc.add_permission_to_record(record, [grant])
    assert await proc.upsert_permission_edge("u1", "users", "rec-1", RECORDS, grant, connector_id=CONNECTOR) is None
    await proc.delete_permission_from_record("rec-1", "a@x.com", CONNECTOR)
    await proc.delete_group_permission_from_record("rec-1", "g1", CONNECTOR)
    await proc.delete_principal_permission_from_record("rec-1", "a@x.com", CONNECTOR)
    assert await proc.remove_user_grants_from_records(["rec-1"], {"a@x.com"}, CONNECTOR) == 0
    await proc.stop_inheriting_permissions("rec-1", CONNECTOR)
    await proc.resume_inheriting_permissions("rec-1", CONNECTOR, parent_record_id="rec-0")
    assert await proc.remove_user_access_to_connector(CONNECTOR, "a@x.com") == 0
    await proc.remove_user_grants(CONNECTOR, "u1", ["rec-1"])
    assert await proc.withdraw_user_from_connector(CONNECTOR, "u1") == 0
    await proc.remove_user_access_to_record(CONNECTOR, "ext-1", "u1")

    assert _no_writes(tx_store) == []


@pytest.mark.asyncio
async def test_licensed_again_writes_resume_while_the_limit_is_still_set() -> None:
    proc, tx_store = _gate_processor(LICENSED, _app(limit=AccessLimit.CREATOR.value))
    user = AppUser(app_name=Connectors.JIRA, connector_id=CONNECTOR, source_user_id="s1", email="a@x.com", full_name="A")

    await proc.on_new_app_users([user])

    tx_store.batch_upsert_app_users.assert_awaited_once()


# ---- the full sync: the unlink keeps what was skipped; the limit lifts only after a licensed one ----

class _Connector(MagicMock):
    """A connector type that registers its own permission model, which lifting the limit puts back."""

    _connector_metadata = {"config": {"permissionModel": PermissionModel.RECORD_GROUP_LEVEL.value}}


def _connector(proc: DataSourceEntitiesProcessor, *, writes: bool = True, coverage: FullSyncCoverage | None = None,
               skip_during_run: bool = False) -> MagicMock:
    connector = _Connector()
    connector.data_entities_processor = proc
    connector.full_sync_coverage = MagicMock(return_value=coverage or FullSyncCoverage())

    async def run_sync() -> None:
        if writes:
            proc._note_full_sync_seen(CONNECTOR, RECORDS, "r1")
        if skip_during_run:
            proc.config_service = _config(UNLICENSED)  # a new answer: the old one is remembered
            await proc.on_new_app_users([AppUser(
                app_name=Connectors.JIRA, connector_id=CONNECTOR, source_user_id="s1", email="a@x.com", full_name="A",
            )])
            proc.config_service = _config(LICENSED)

    connector.run_sync = run_sync
    return connector


def _runner_graph(app: dict) -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value=app)
    gp.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, True))
    gp.update_node = AsyncMock(return_value=True)
    return gp


def _wire(proc: DataSourceEntitiesProcessor, gp: AsyncMock) -> None:
    proc.data_store_provider.graph_provider.get_document = gp.get_document
    proc.data_store_provider.graph_provider.update_node = gp.update_node


@pytest.mark.asyncio
async def test_the_limit_stays_after_a_licensed_incremental_sync_and_lifts_after_the_licensed_full_sync() -> None:
    app = _app(limit=AccessLimit.CREATOR.value)
    proc, _ = _gate_processor(LICENSED, app)
    gp = _runner_graph(app)
    _wire(proc, gp)

    await run_sync_task(_connector(proc), CONNECTOR, gp, _LOG, start_status=AppStatus.SYNCING.value)
    proc.clear_access_limit.assert_not_awaited()
    assert any(c.args[2].get(ConnectorStateKeys.PENDING_FULL_SYNC) for c in gp.update_node.await_args_list)

    await run_sync_task(_connector(proc), CONNECTOR, gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)
    proc.clear_access_limit.assert_awaited_once_with(CONNECTOR, PermissionModel.RECORD_GROUP_LEVEL)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["skipped_during_run", "incomplete", "wrote_nothing", "unlicensed_at_end", "sync_points_not_reset"],
)
async def test_a_full_sync_that_may_not_have_rewritten_everything_keeps_the_limit(case: str) -> None:
    app = _app(limit=AccessLimit.CREATOR.value)
    proc, _ = _gate_processor(LICENSED, app)
    gp = _runner_graph(app)
    _wire(proc, gp)
    connector = _connector(
        proc,
        writes=case != "wrote_nothing",
        coverage=FullSyncCoverage(complete=False) if case == "incomplete" else None,
        skip_during_run=case == "skipped_during_run",
    )
    if case == "unlicensed_at_end":
        original = connector.run_sync

        async def then_lapse() -> None:
            await original()
            proc.config_service = _config(UNLICENSED)

        connector.run_sync = then_lapse

    # An incomplete run may owe its retry; it never lifts either way.
    with contextlib.suppress(Exception):
        await run_sync_task(
            connector, CONNECTOR, gp, _LOG, start_status=AppStatus.FULL_SYNCING.value,
            sync_points_reset=case != "sync_points_not_reset",
        )

    proc.clear_access_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_licence_read_stops_the_sync_before_the_source_is_read() -> None:
    app = _app()
    proc, _ = _gate_processor(UNLICENSED, app)
    proc.config_service.get_config = AsyncMock(side_effect=ConnectionError("store down"))
    gp = _runner_graph(app)
    _wire(proc, gp)
    connector = _connector(proc)
    connector.run_sync = AsyncMock()

    with pytest.raises(ConnectionError):
        await run_sync_task(connector, CONNECTOR, gp, _LOG, start_status=AppStatus.SYNCING.value)

    connector.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_limit_write_stops_the_sync() -> None:
    app = _app()
    proc, _ = _gate_processor(UNLICENSED, app)
    proc.set_access_limit = AsyncMock(side_effect=ConnectionError("write refused"))
    gp = _runner_graph(app)
    _wire(proc, gp)
    connector = _connector(proc)
    connector.run_sync = AsyncMock()

    with pytest.raises(ConnectionError):
        await run_sync_task(connector, CONNECTOR, gp, _LOG, start_status=AppStatus.SYNCING.value)

    connector.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unlicensed_full_sync_keeps_the_groups_and_roles_it_did_not_write() -> None:
    app = _app()
    proc, tx_store = _gate_processor(UNLICENSED, app)
    tx_store.get_nodes_by_filters = AsyncMock(side_effect=lambda collection, *_a, **_k: [
        {"_key": f"{collection}-1"}, {"id": f"{collection}-2"},
    ])
    gp = _runner_graph(app)
    _wire(proc, gp)
    connector = _connector(proc)
    original = connector.run_sync

    async def run_sync() -> None:
        await original()
        group = AppUserGroup(app_name=Connectors.JIRA, connector_id=CONNECTOR, source_user_group_id="g1", name="G")
        await proc.on_new_user_groups([(group, [])])

    connector.run_sync = run_sync

    await run_sync_task(connector, CONNECTOR, gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    seen = gp.unlink_nodes_missed_by_full_sync.await_args.args[1]
    assert seen[GROUPS] >= {f"{GROUPS}-1", f"{GROUPS}-2"}
    assert seen[ROLES] >= {f"{ROLES}-1", f"{ROLES}-2"}
    assert seen[RECORDS] == {"r1"}


@pytest.mark.asyncio
async def test_a_licensed_full_sync_unlinks_groups_as_before() -> None:
    proc, tx_store = _gate_processor(LICENSED)
    tx_store.get_nodes_by_filters = AsyncMock()
    gp = _runner_graph(_app())
    _wire(proc, gp)

    await run_sync_task(_connector(proc), CONNECTOR, gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    tx_store.get_nodes_by_filters.assert_not_awaited()
    assert not gp.unlink_nodes_missed_by_full_sync.await_args.args[1].get(GROUPS)


# ---- the startup sweep ----

def _registry(licence: object, documents: list[dict]) -> tuple[ConnectorRegistry, MagicMock]:
    container = MagicMock()
    container.logger.return_value = _LOG
    container.config_service.return_value = _config(licence)
    data_store = MagicMock()
    data_store.graph_provider.kh_scope_mark_changed = AsyncMock()
    container.data_store = AsyncMock(return_value=data_store)
    registry = ConnectorRegistry(container)
    return registry, container


@pytest.mark.asyncio
async def test_the_startup_sweep_limits_every_unlicensed_company_wide_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[tuple[str, AccessLimit]] = []

    async def record(self: DataSourceEntitiesProcessor, connector_id: str, limit: AccessLimit) -> None:
        if connector_id == "broken":
            raise ConnectionError("write refused")
        written.append((connector_id, limit))

    monkeypatch.setattr(DataSourceEntitiesProcessor, "set_access_limit", record)
    monkeypatch.setattr(_NOTIFY, AsyncMock())
    docs = [
        {"_key": "done", "type": "JIRA", "scope": "team", "accessLimit": "CREATOR", "permissionModel": "APP_LEVEL"},
        {"_key": "jira", "type": "JIRA", "scope": "team"},
        {"_key": "broken", "type": "JIRA", "scope": "team"},
        {"_key": "conf", "type": "CONFLUENCE", "scope": "team", "accessLimit": "CREATOR"},
        {"_key": "s3", "type": "S3", "scope": "team"},
        {"_key": "kb", "type": "KB", "scope": "team"},
        {"_key": "mine", "type": "JIRA", "scope": "personal"},
    ]
    registry, container = _registry(UNLICENSED, docs)

    await registry._limit_unlicensed_connectors("org-1", docs)

    assert written == [("jira", AccessLimit.CREATOR), ("conf", AccessLimit.CREATOR)]
    graph = (await container.data_store()).graph_provider
    graph.kh_scope_mark_changed.assert_awaited_once_with("done")  # limited already: refreshed, not written


@pytest.mark.asyncio
@pytest.mark.parametrize("licence", [None, LICENSED])
async def test_the_startup_sweep_changes_nothing_when_licensed_or_unconfigured(
    licence: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_limit = AsyncMock()
    monkeypatch.setattr(DataSourceEntitiesProcessor, "set_access_limit", set_limit)
    docs = [{"_key": "jira", "type": "JIRA", "scope": "team"}]
    registry, container = _registry(licence, docs)

    await registry._limit_unlicensed_connectors("org-1", docs)

    set_limit.assert_not_awaited()
    container.data_store.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_licence_read_at_startup_is_logged_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    set_limit = AsyncMock()
    monkeypatch.setattr(DataSourceEntitiesProcessor, "set_access_limit", set_limit)
    docs = [{"_key": "jira", "type": "JIRA", "scope": "team"}]
    registry, container = _registry(None, docs)
    container.config_service.return_value.get_config = AsyncMock(side_effect=ConnectionError("down"))

    await registry._limit_unlicensed_connectors("org-1", docs)

    set_limit.assert_not_awaited()
