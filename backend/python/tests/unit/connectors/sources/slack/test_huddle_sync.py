from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.slack.common.huddle import huddle_id, sync_huddles
from app.models.permission import EntityType, Permission, PermissionType


class Store:
    def __init__(self) -> None:
        self.org_id = "org"
        self.records = {}
        self.permissions = {}
        self.deleted = []

    async def get_records_by_status(self, **kwargs) -> list:
        return sorted(self.records.values(), key=lambda r: r.id)

    async def on_new_records(self, records, *, replace_permissions=False) -> None:
        assert replace_permissions
        for record, permissions in records:
            old = self.records.get(record.external_record_id)
            if old:
                record.id = old.id
            self.records[record.external_record_id] = record
            self.permissions[record.external_record_id] = list(permissions)

    async def get_record_by_external_id(self, connector_id, external_record_id) -> object | None:
        return self.records.get(external_record_id)

    async def on_updated_record_permissions(self, record, permissions) -> None:
        self.permissions[record.external_record_id] = list(permissions)

    async def on_record_deleted(self, record_id) -> None:
        self.deleted.append(record_id)
        self.records = {k: r for k, r in self.records.items() if r.id != record_id}

    def note_kept_records(self, connector_id, record_ids) -> None:
        pass


class Slack:
    def __init__(self) -> None:
        self.files = {}
        self.contents = {}
        self.list_error = None
        self.info_error = None
        self.content_error = None

    async def files_list(self, **kwargs) -> SimpleNamespace:
        if self.list_error:
            return SimpleNamespace(success=False, error=self.list_error, retry_after="5")
        channel = kwargs["channel"]
        files = [dict(fd) for fd in self.files.values() if channel in fd.get("channels", [])]
        return SimpleNamespace(success=True, data={"files": files, "paging": {"page": 1, "pages": 1}})

    async def files_info(self, file) -> SimpleNamespace:
        if self.info_error:
            return SimpleNamespace(success=False, error=self.info_error)
        fd = self.files.get(file)
        return SimpleNamespace(success=bool(fd), error=None if fd else "file_not_found", data={"file": fd})

    async def canvases_get_content(self, file_id) -> SimpleNamespace:
        if self.content_error:
            return SimpleNamespace(success=False, error=self.content_error)
        return SimpleNamespace(success=True, data={"content": self.contents[file_id]})


def setup() -> tuple:
    store, slack = Store(), Slack()
    connector = SimpleNamespace(
        connector_id="connector", data_entities_processor=store, display_name="Slack",
        rate_limiter=SimpleNamespace(acquire=AsyncMock()),
        _fresh_datasource=AsyncMock(return_value=slack), _full_sync_read_failed=False,
        _compute_sync_window_oldest=lambda: None, _is_channel_indexing_enabled=lambda channel: True,
        indexing_filters=FilterCollection(), logger=MagicMock(),
        notify=AsyncMock(),
    )
    return connector, store, slack


def reader(email) -> Permission:
    return Permission(email=email, entity_type=EntityType.USER, type=PermissionType.READ)


def canvas(*channels) -> dict:
    return {"id": "F1", "is_huddle_canvas": True, "channels": list(channels), "groups": [],
            "title": "Huddle", "created": 1, "permalink": "https://example.slack.com/docs/F1"}


@pytest.mark.asyncio
async def test_separate_channel_grants_edit_and_unshare() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1", "C2")
    slack.contents["F1"] = "First notes"
    channels = {"C1": [reader("alice@example.test")], "C2": [reader("bob@example.test")]}
    await sync_huddles(connector, channels, personal=False)
    one, two = store.records[huddle_id("C1", "F1")], store.records[huddle_id("C2", "F1")]
    assert one.record_group_id is None and not one.inherit_permissions
    assert [p.email for p in store.permissions[one.external_record_id]] == ["alice@example.test"]
    assert [p.email for p in store.permissions[two.external_record_id]] == ["bob@example.test"]
    old_revision = two.external_revision_id
    slack.files["F1"] = canvas("C2")
    slack.contents["F1"] = "Edited notes"
    await sync_huddles(connector, channels, personal=False)
    assert store.permissions[one.external_record_id] == []
    assert [p.email for p in store.permissions[two.external_record_id]] == ["bob@example.test"]
    assert store.records[two.external_record_id].external_revision_id != old_revision


@pytest.mark.asyncio
async def test_rate_limited_scan_keeps_existing_grants() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    channels = {"C1": [reader("alice@example.test")]}
    await sync_huddles(connector, channels, personal=False)
    slack.list_error = "ratelimited"
    await sync_huddles(connector, channels, personal=False)
    assert store.permissions[huddle_id("C1", "F1")][0].email == "alice@example.test"
    assert connector._full_sync_read_failed


@pytest.mark.asyncio
async def test_lost_bot_access_removes_grant_without_deleting_source_record() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    channels = {"C1": [reader("alice@example.test")]}
    await sync_huddles(connector, channels, personal=False)
    slack.info_error = "not_visible"
    await sync_huddles(connector, channels, personal=False)
    assert store.permissions[huddle_id("C1", "F1")] == []
    assert not store.deleted


@pytest.mark.asyncio
async def test_personal_not_visible_is_deleted_only_when_canvas_is_also_unavailable() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    await sync_huddles(connector, {"C1": []}, personal=True)
    slack.info_error = "not_visible"
    slack.content_error = "canvas_not_found"
    await sync_huddles(connector, {"C1": []}, personal=True)
    assert huddle_id("C1", "F1") not in store.records
    assert len(store.deleted) == 1


@pytest.mark.asyncio
async def test_missing_share_information_does_not_grant_access() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    slack.files["F1"]["has_more_shares"] = True
    slack.files["F1"]["channels"] = []
    # Keep the item discoverable while simulating a partial metadata response.
    slack.files_list = AsyncMock(return_value=SimpleNamespace(success=True, data={
        "files": [{"id": "F1", "is_huddle_canvas": True}], "paging": {"page": 1, "pages": 1},
    }))
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    assert connector._full_sync_read_failed
    assert store.permissions[huddle_id("C1", "F1")][0].email == "alice@example.test"

@pytest.mark.asyncio
@pytest.mark.parametrize("personal", [False, True])
async def test_confirmed_deleted_file_removes_its_index_record(personal) -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=personal)
    del slack.files["F1"]
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=personal)
    assert huddle_id("C1", "F1") not in store.records
    assert len(store.deleted) == 1


@pytest.mark.asyncio
async def test_old_indexed_note_is_refreshed_outside_discovery_window() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "First"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    previous = store.records[huddle_id("C1", "F1")].external_revision_id
    slack.contents["F1"] = "Edited later"
    slack.files_list = AsyncMock(return_value=SimpleNamespace(success=True, data={
        "files": [], "paging": {"page": 1, "pages": 0},
    }))
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    assert store.records[huddle_id("C1", "F1")].external_revision_id != previous


@pytest.mark.asyncio
async def test_removed_channel_revokes_team_grant_but_unknown_channel_does_not() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    await sync_huddles(connector, {}, personal=False, unknown_channels={"C1"})
    assert store.permissions[huddle_id("C1", "F1")]
    await sync_huddles(connector, {}, personal=False)
    assert store.permissions[huddle_id("C1", "F1")] == []


@pytest.mark.asyncio
async def test_missing_canvas_scope_does_not_treat_content_as_deleted() -> None:
    connector, store, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    slack.content_error = "missing_scope"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    assert connector._full_sync_read_failed
    assert not store.deleted
    assert store.permissions[huddle_id("C1", "F1")]
    assert "canvases:read" in connector.notify.await_args.kwargs["message"]


@pytest.mark.asyncio
async def test_stream_rechecks_the_current_channel_share() -> None:
    from fastapi import HTTPException

    from app.connectors.sources.slack.common.huddle import stream_huddle

    connector, _, slack = setup()
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "# Private notes"
    assert await stream_huddle(connector, "C1", "F1") == b"# Private notes"
    slack.files["F1"] = canvas("C2")
    with pytest.raises(HTTPException) as exc:
        await stream_huddle(connector, "C1", "F1")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_confirmed_legacy_attachment_is_retired_after_replacement() -> None:
    from app.models.entities import RecordType

    connector, store, slack = setup()
    legacy = SimpleNamespace(id="legacy", external_record_id="F1", record_type=RecordType.FILE)
    store.records["F1"] = legacy
    slack.files["F1"] = canvas("C1")
    slack.contents["F1"] = "Notes"
    await sync_huddles(connector, {"C1": [reader("alice@example.test")]}, personal=False)
    assert huddle_id("C1", "F1") in store.records
    assert "F1" not in store.records
    assert store.deleted == ["legacy"]




@pytest.mark.parametrize("prefix", ["C", "G"])
def test_private_channel_ids_are_resolved_from_groups(prefix) -> None:
    from app.connectors.sources.slack.common.huddle import channel_share
    cid = prefix + "PRIVATE"
    metadata = {"channels": [], "groups": [cid], "ims": []}
    assert channel_share(metadata, cid) is True
    metadata["groups"] = []
    assert channel_share(metadata, cid) is False
    del metadata["groups"]
    assert channel_share(metadata, cid) is None
