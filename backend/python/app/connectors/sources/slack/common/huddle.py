"""Discover Huddle canvases without treating a partial listing as complete."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.base.error.stream_errors import map_source_status
from app.connectors.core.registry.filters import IndexingFilterKey
from app.connectors.sources.slack.common.stream_errors import slack_stream_error
from app.models.entities import FileRecord, ProgressStatus, Record, RecordType
from app.services.notification.types import NotificationSeverity, NotificationType

if TYPE_CHECKING:
    from app.connectors.sources.slack.individual.connector import (
        SlackIndividualConnector,
    )
    from app.connectors.sources.slack.team.connector import SlackConnector
    from app.models.permission import Permission
    from app.sources.client.slack.slack import SlackResponse

    HuddleConnector = SlackConnector | SlackIndividualConnector


async def list_huddle_canvases(connector: HuddleConnector, channel_id: str, oldest: str | None = None) -> list[dict[str, Any]]:
    """List a channel's Huddle canvases; a failed or malformed page raises."""
    page = 1
    canvases: dict[str, dict[str, Any]] = {}
    while True:
        await connector.rate_limiter.acquire(3)
        ds = await connector._fresh_datasource()
        response = await ds.files_list(
            channel=channel_id, types="canvas", count=200, page=page,
            show_files_hidden_by_limit=1, ts_from=oldest,
        )
        if not response or not response.success:
            raise slack_stream_error(response, connector=connector.display_name)
        data = response.data
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise ValueError("Slack returned an incomplete canvas listing")
        paging = data.get("paging")
        if not isinstance(paging, dict) or paging.get("page") != page:
            raise ValueError("Slack returned invalid canvas pagination")
        pages = paging.get("pages")
        if not isinstance(pages, int) or isinstance(pages, bool) or pages < 0:
            raise ValueError("Slack returned invalid canvas pagination")
        for item in data["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("Slack returned incomplete canvas metadata")
            if item.get("is_huddle_canvas") is True:
                canvases[item["id"]] = item
        if page >= pages:
            return list(canvases.values())
        page += 1


HUDDLE_PREFIX = "slack-huddle:"


class HuddleScopeError(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "Slack Huddle notes require the canvases:read scope. Reconnect or update the Slack token.")


def huddle_id(channel_id: str, file_id: str) -> str:
    return f"{HUDDLE_PREFIX}{channel_id}:{file_id}"


def parse_huddle_id(record_id: str) -> tuple[str, str] | None:
    if not record_id.startswith(HUDDLE_PREFIX):
        return None
    parts = record_id[len(HUDDLE_PREFIX):].split(":")
    return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else None


def channel_share(file: dict[str, Any], channel_id: str) -> bool | None:
    """Return only a current, documented channel share, or None when unknown."""
    # Private channels can have C-prefixed IDs; the prefix does not identify privacy.
    fields = ("channels", "groups") if not channel_id.startswith("D") else ("ims",)
    shares = [file.get(field) for field in fields]
    if any(isinstance(values, list) and channel_id in values for values in shares):
        return True
    if any(not isinstance(values, list) for values in shares):
        return None
    if file.get("has_more_shares") or file.get("skipped_shares"):
        return None
    return False


async def _stored_huddles(connector: HuddleConnector) -> dict[str, Any]:
    records = {}
    cursor = None
    while True:
        page = await connector.data_entities_processor.get_records_by_status(
            connector_id=connector.connector_id, status_filters=None, limit=200, after_key=cursor,
        )
        for record in page:
            if parse_huddle_id(record.external_record_id):
                records[record.external_record_id] = record
        if len(page) < 200:
            return records
        if not page[-1].id or page[-1].id == cursor:
            raise ValueError("Invalid stored-record pagination")
        cursor = page[-1].id


async def _file_info(connector: HuddleConnector, file_id: str, *, retry_rate_limits: bool = True) -> SlackResponse:
    await connector.rate_limiter.acquire(4)
    ds = await connector._fresh_datasource(retry_rate_limits=retry_rate_limits)
    return await ds.files_info(file=file_id)


async def canvas_content(connector: HuddleConnector, file_id: str, *, retry_rate_limits: bool = True) -> str:
    await connector.rate_limiter.acquire(3)
    ds = await connector._fresh_datasource(retry_rate_limits=retry_rate_limits)
    response = await ds.canvases_get_content(file_id)
    if response and not response.success and response.error == "missing_scope":
        raise HuddleScopeError()
    if not response or not response.success:
        raise slack_stream_error(response, connector=connector.display_name)
    if not isinstance(response.data, dict) or not isinstance(response.data.get("content"), str):
        raise ValueError("Slack returned incomplete Canvas content")
    return response.data["content"]


async def stream_huddle(connector: HuddleConnector, channel_id: str, file_id: str) -> bytes:
    info = await _file_info(connector, file_id, retry_rate_limits=False)
    if not info or not info.success:
        raise slack_stream_error(info, connector=connector.display_name)
    fd = info.data.get("file") if isinstance(info.data, dict) else None
    if (not isinstance(fd, dict) or fd.get("id") != file_id
            or fd.get("is_huddle_canvas") is not True or channel_share(fd, channel_id) is not True):
        raise map_source_status(403, connector=connector.display_name)
    return (await canvas_content(connector, file_id, retry_rate_limits=False)).encode("utf-8")


async def _revoke(connector: HuddleConnector, record: Record | None, *, personal: bool) -> None:
    if personal:
        await connector.data_entities_processor.on_record_deleted(record.id)
    else:
        record.inherit_permissions = False
        await connector.data_entities_processor.on_updated_record_permissions(record, [])
        connector.data_entities_processor.note_kept_records(connector.connector_id, [record.id])


async def _readable_file(connector: HuddleConnector, file_id: str, channel_id: str, record: Record | None, *, personal: bool) -> dict[str, Any] | None:
    info = await _file_info(connector, file_id)
    if not info or not info.success:
        error = getattr(info, "error", None)
        if error in ("file_not_found", "file_deleted"):
            if record:
                await connector.data_entities_processor.on_record_deleted(record.id)
            return None
        if error == "not_visible":
            if not personal:
                if record:
                    await _revoke(connector, record, personal=False)
                return None
            # A direct Canvas not-found response confirms this user has lost access.
            try:
                await canvas_content(connector, file_id)
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    if record:
                        await _revoke(connector, record, personal=True)
                    return None
                raise
        raise slack_stream_error(info, connector=connector.display_name)
    fd = info.data.get("file") if isinstance(info.data, dict) else None
    if not isinstance(fd, dict) or fd.get("id") != file_id or fd.get("is_huddle_canvas") is not True:
        raise ValueError("Slack returned incomplete Huddle metadata")
    shared = channel_share(fd, channel_id)
    if shared is None:
        raise ValueError("Slack did not return the current channel share")
    if not shared:
        if record:
            await _revoke(connector, record, personal=personal)
        return None
    return fd


async def sync_huddles(
    connector: HuddleConnector, channels: dict[str, list[Permission]], *, personal: bool,
    unknown_channels: set[str] | None = None, channels_complete: bool = True,
) -> None:
    """Reconcile channel-shared notes; direct individual shares are deliberately omitted."""
    stored = await _stored_huddles(connector)
    pending = set(stored)
    failures = False
    missing_scope = False
    oldest = connector._compute_sync_window_oldest()
    for channel_id, permissions in channels.items():
        try:
            listed = await list_huddle_canvases(connector, channel_id, oldest)
            ids = {item["id"] for item in listed}
            # Refresh older notes that have aged out of the discovery window too.
            ids.update(parsed[1] for key in pending if (parsed := parse_huddle_id(key)) and parsed[0] == channel_id)
            for file_id in ids:
                external_id = huddle_id(channel_id, file_id)
                existing = stored.get(external_id)
                fd = await _readable_file(connector, file_id, channel_id, existing, personal=personal)
                if fd is None:
                    pending.discard(external_id)
                    continue
                try:
                    content = await canvas_content(connector, file_id)
                except Exception as exc:
                    if getattr(exc, "status_code", None) == 404:
                        if existing:
                            await _revoke(connector, existing, personal=personal)
                        pending.discard(external_id)
                        continue
                    raise
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                title = fd.get("title") or fd.get("name") or "Huddle notes"
                record = FileRecord(
                    org_id=connector.data_entities_processor.org_id,
                    record_name=f"{title}.md", record_type=RecordType.FILE,
                    external_record_id=external_id, external_revision_id=digest,
                    connector_name=Connectors.SLACK if personal else Connectors.SLACK_WORKSPACE,
                    connector_id=connector.connector_id, version=0, origin=OriginTypes.CONNECTOR,
                    mime_type=MimeTypes.MARKDOWN.value, is_file=True, extension="md",
                    inherit_permissions=False, sha256_hash=digest,
                    source_created_at=int(fd.get("created") or 0) * 1000,
                    source_updated_at=int(fd.get("edit_timestamp") or fd.get("created") or 0) * 1000,
                    weburl=fd.get("permalink"),
                    indexing_status=(ProgressStatus.QUEUED.value if (
                        connector.indexing_filters.is_enabled(IndexingFilterKey.FILES)
                        and (not personal or connector._is_channel_indexing_enabled(channel_id))
                    ) else ProgressStatus.AUTO_INDEX_OFF.value),
                )
                await connector.data_entities_processor.on_new_records(
                    [(record, permissions)], replace_permissions=True,
                )
                # A legacy attachment inherited a channel's blanket access.
                legacy = await connector.data_entities_processor.get_record_by_external_id(
                    connector_id=connector.connector_id, external_record_id=file_id,
                )
                if legacy and legacy.record_type == RecordType.FILE:
                    await connector.data_entities_processor.on_record_deleted(legacy.id)
                pending.discard(external_id)
        except Exception as exc:
            failures = True
            missing_scope = missing_scope or isinstance(exc, HuddleScopeError)
            connector._full_sync_read_failed = True
            connector.logger.warning("Huddle sync failed for channel %s: %s", channel_id, exc)
    if missing_scope:
        await connector.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR, severity=NotificationSeverity.WARNING,
            title=f"{connector.display_name}: Huddle notes need access",
            message="Grant canvases:read to the Slack token and reconnect it to index Huddle notes.",
        )
    if not failures and channels_complete:
        # Only a complete channel listing proves the old channel is out of scope.
        for external_id in pending:
            if parse_huddle_id(external_id)[0] not in channels and parse_huddle_id(external_id)[0] not in (unknown_channels or set()):
                await _revoke(connector, stored[external_id], personal=personal)
