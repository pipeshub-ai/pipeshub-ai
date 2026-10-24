"""Mail folder listing, inventory, mail ids, error mapping and run notifications shared by
Outlook Personal and Outlook (team) connectors."""

import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from logging import Logger
from typing import Any, NamedTuple

import httpx
from fastapi import HTTPException
from kiota_abstractions.method import Method
from kiota_abstractions.request_information import RequestInformation
from msgraph.generated.models.mail_folder import MailFolder  # type: ignore
from msgraph.generated.models.message import Message  # type: ignore

from app.config.constants.arangodb import ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    map_source_status,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.core.registry.filters import SyncFilterKey
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.microsoft.common.outlook_constants import (
    OutlookDefaults,
    OutlookSyncConfig,
    OutlookSyncPointKeys,
    describe_sign_in_error,
    graph_error_status,
    is_expired_delta_link_error,
)
from app.models.entities import AppUser, FileRecord, RecordType
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.client.resilience import ResiliencePolicy

IMMUTABLE_ID_TYPE = "ImmutableId"
_PREFER_IMMUTABLE_IDS = f'IdType="{IMMUTABLE_ID_TYPE}"'
_RECORD_SCAN_PAGE_SIZE = 500
_DAY_MS = 24 * 60 * 60 * 1000


def _graph_http_session(client: MSGraphClient) -> httpx.AsyncClient:
    """The httpx session under the client's Kiota adapter (a private attribute: fail clearly if a
    Kiota release renames it)."""
    session = getattr(client.get_client().get_ms_graph_service_client().request_adapter, "_http_client", None)
    if session is None:
        raise RuntimeError("The Graph SDK's request adapter has no HTTP session (_http_client); check the kiota-http version")
    return session


def prefer_immutable_ids(client: MSGraphClient) -> MSGraphClient:
    """Ask Graph for immutable ids on every mailbox call ``client`` makes.

    A regular message id changes when the mail moves to another folder; an immutable one
    does not, so a move updates the record instead of deleting and re-indexing it. A hook
    on the client's HTTP session covers every call, nextLink and deltaLink pages included.
    Group calls are left as they are, so stored group post ids never change.
    """
    http_client = _graph_http_session(client)

    async def add_prefer(request: httpx.Request) -> None:
        if "groups" in request.url.path.split("/"):
            return
        prefer = request.headers.get("Prefer")
        if not prefer:
            request.headers["Prefer"] = _PREFER_IMMUTABLE_IDS
        elif "IdType=" not in prefer:
            request.headers["Prefer"] = f"{prefer}, {_PREFER_IMMUTABLE_IDS}"

    http_client.event_hooks["request"].append(add_prefer)
    return client


def pace_graph_calls(client: MSGraphClient, policy: ResiliencePolicy | None) -> MSGraphClient:
    """Take a rate-limit slot from the connector's policy before each Graph call ``client`` sends.
    Retries stay with the SDK's retry handler, below this hook."""
    if policy is not None:
        http_client = _graph_http_session(client)

        async def take_slot(_request: httpx.Request) -> None:
            await policy.acquire()

        http_client.event_hooks["request"].append(take_slot)
    return client


async def open_graph_stream(client: MSGraphClient, path: str) -> httpx.Response:
    """GET ``path`` (under the Graph base URL) on the client's own HTTP session, so its sign-in,
    retry handler and hooks apply, and return the response with its body still unread."""
    adapter = client.get_client().get_ms_graph_service_client().request_adapter
    info = RequestInformation(Method.GET)
    info.url = f"{adapter.base_url.rstrip('/')}/{path}"
    request = await adapter.convert_to_native_async(info)
    return await _graph_http_session(client).send(request, stream=True)


async def raise_for_graph_stream(response: httpx.Response, connector: str) -> None:
    """Close a failed stream response and raise its status as a stream error (401 asks to reconnect)."""
    if response.is_success:
        return
    await response.aclose()
    raise map_source_status(
        response.status_code, connector=connector, retry_after=response.headers.get("Retry-After"),
    )


async def iter_graph_stream(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    """The response body in chunks; the response is closed when the reader stops."""
    try:
        async for chunk in response.aiter_bytes(OutlookSyncConfig.ATTACHMENT_CHUNK_SIZE):
            yield chunk
    finally:
        await response.aclose()


# Attached mails and events, and links to cloud files, have no file content to index:
# no parser reads a MIME message, and Graph serves no bytes for a link.
_UNSYNCED_ATTACHMENT_TYPES = {
    "#microsoft.graph.itemAttachment": "an attached mail or event",
    "#microsoft.graph.referenceAttachment": "a link to a cloud file",
}


def unsynced_attachment_kind(attachment: object) -> str | None:
    """What ``attachment`` is when it gets no record (it could never be downloaded), else None."""
    return _UNSYNCED_ATTACHMENT_TYPES.get(getattr(attachment, "odata_type", None))


def cap_attachment_indexing(record: FileRecord, logger: Logger) -> None:
    """Keep an attachment over the size cap out of automatic indexing; it still syncs and previews."""
    if (record.size_in_bytes or 0) > OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES:
        logger.info(
            f"Attachment '{record.record_name}' ({record.size_in_bytes} bytes) is over the "
            f"{OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES} byte cap; saved without indexing"
        )
        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value


async def read_delta_pages(
    fetch_page: Callable[[str | None], Awaitable[Any]], delta_link: str | None, folder_id: str, logger: Logger,
) -> AsyncGenerator[tuple[list[Message], str | None, bool], None]:
    """A folder's delta one page at a time, as (messages, new delta link, full read); the link is
    set on the last page only. ``fetch_page(None)`` starts a full read, ``fetch_page(url)`` follows
    a link. "Full read" means the pages list every mail of the folder, not changes.

    A saved link Graph reports expired is dropped and the folder read again in full, before any
    page was handed on. Any other failure raises: an empty result would read as "no changes".
    """
    url = delta_link
    first_page = True
    full_read = delta_link is None
    followed: set[str] = set()
    while True:
        response = await fetch_page(url)
        if not response.success:
            if first_page and url and is_expired_delta_link_error(response.error):
                logger.warning(f"Delta link of folder {folder_id} expired; reading the folder again in full: {response.error}")
                url = None
                full_read = True
                continue
            raise Exception(f"Delta query failed: {response.error}")
        first_page = False
        page = response.data
        if page.odata_next_link:
            if page.odata_next_link in followed:
                raise Exception(f"Graph repeated a delta nextLink for folder {folder_id}")
            followed.add(page.odata_next_link)
            yield list(page.value or []), None, full_read
            url = page.odata_next_link
        elif page.odata_delta_link:
            yield list(page.value or []), page.odata_delta_link, full_read
            return
        else:
            raise Exception("Response missing both odata_next_link and odata_delta_link")


async def read_child_folders(
    fetch_page: Callable[[int, int], Awaitable[Any]],
    fetch_next: Callable[[str], Awaitable[Any]],
    parent: MailFolder,
) -> tuple[list[MailFolder], bool]:
    """Every direct child folder of ``parent``, and whether the listing is proven complete.

    The first page is ``fetch_page(top, 0)``; then each @odata.nextLink is followed with
    ``fetch_next(url)``, and only a full page without one falls back to ``fetch_page(top, skip)``.
    Graph pages by offset either way, so a folder deleted between two pages shifts the next one
    out of both: a listing of several pages with fewer folders than the parent's childFolderCount
    is not complete, and no folder may be dropped on it. A single page has no such race, and its
    count may differ for a good reason: childFolderCount includes hidden folders, which the
    listing leaves out (e.g. "Conversation History"). A failure raises.
    """
    parent_name = parent.display_name or OutlookDefaults.UNKNOWN_FOLDER_LABEL
    page_size = OutlookSyncConfig.MAX_FOLDER_PAGE_SIZE
    by_id: dict[str, MailFolder] = {}
    read = 0
    followed: set[str] = set()
    response = await fetch_page(page_size, 0)
    while True:
        if not response.success:
            raise Exception(f"Failed to get child folders for '{parent_name}': {response.error}")
        data = response.data
        page = list(data.value or []) if data else []
        read += len(page)
        new = [f for f in page if f.id and f.id not in by_id]
        if page and not new and by_id:
            raise Exception(f"Graph repeated a childFolders page for '{parent_name}'")
        by_id.update((f.id, f) for f in new)
        next_link = data.odata_next_link if data else None
        if next_link:
            if next_link in followed:
                raise Exception(f"Graph repeated a childFolders nextLink for '{parent_name}'")
            followed.add(next_link)
            response = await fetch_next(next_link)
        elif len(page) >= page_size:
            response = await fetch_page(page_size, read)
        else:
            break
    children = list(by_id.values())
    single_page = not followed and read == len(page)
    return children, single_page or len(children) >= (parent.child_folder_count or 0)


def graph_stream_error(error: Exception, connector: str) -> HTTPException:
    """The stream response for a failed Graph call: a rejected sign-in or a 401 asks to reconnect
    (409), and 403/404/429 keep their meaning. The data source passes on only the error text,
    so the status is read from it."""
    if describe_sign_in_error(error):
        return map_source_status(HttpStatusCode.UNAUTHORIZED.value, connector=connector)
    status = graph_error_status(error)
    if status is not None:
        return map_source_status(status, connector=connector)
    return to_stream_error(error, connector=connector)


def sign_in_problem(error: object) -> str | None:
    """What to fix when ``error`` is a failed sign-in or a Graph 401, else None."""
    problem = describe_sign_in_error(error)
    if problem is None and graph_error_status(error) == HttpStatusCode.UNAUTHORIZED.value:
        problem = "Microsoft rejected the connector's sign-in (401). Check the connector's credentials, or sign in again."
    return problem


async def notify_sync_outcome(connector: Any, error: BaseException | None, skipped: dict[str, int]) -> None:  # noqa: ANN401
    """One notification per run: a failed sign-in, a failed run, or what was skipped after errors."""
    if error is not None:
        problem = sign_in_problem(error)
        if problem:
            await connector.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR, severity=NotificationSeverity.ERROR,
                title=connector._notification_title("sign-in failed"), message=problem,
            )
        else:
            # The error text can carry Graph URLs and ids; only its kind goes into the notification.
            status = graph_error_status(error)
            reason = f"Microsoft Graph answered {status}" if status else type(error).__name__
            await connector.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR, severity=NotificationSeverity.ERROR,
                title=connector._notification_title("sync failed"),
                message=f"The sync stopped with an error ({reason}). Run the sync again; "
                "if it keeps failing, check the connector's configuration and the logs.",
            )
        return
    counts = [f"{kind} {count}" for kind, count in skipped.items() if count]
    if counts:
        await connector.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR, severity=NotificationSeverity.WARNING,
            title=connector._notification_title("some mail was not synced"),
            message=f"Skipped after errors: {', '.join(counts)}. They are retried on the next sync.",
        )


def removed_mail_update(message_id: str) -> RecordUpdate:
    """A mail a folder's delta reported removed; ``_save_folder_link`` deletes it."""
    return RecordUpdate(
        record=None, is_new=False, is_updated=False, is_deleted=True, metadata_changed=False,
        content_changed=False, permissions_changed=False, external_record_id=message_id,
    )


class MailThreadRef(NamedTuple):
    """What thread edges need of a synced mail, kept instead of the whole record."""
    id: str
    thread_id: str | None
    conversation_index: str | None


def mail_thread_ref(record: Any) -> MailThreadRef:  # noqa: ANN401
    return MailThreadRef(record.id, record.thread_id, record.conversation_index)


async def close_graph_client(client: MSGraphClient | None, logger: Logger) -> None:
    """Close a Graph client the connector no longer uses (its sign-in session and HTTP pool)."""
    if client is None:
        return
    try:
        await client.get_client().close()
    except Exception as e:
        logger.debug(f"Closing a Graph client failed: {e}")


class OutlookFolderInventoryMixin:
    """Removes folders and mail deleted in Outlook, and mail a full folder read did not see.
    Needs ``email_delta_sync_point``, ``sync_filters``, ``data_entities_processor``,
    ``connector_id`` and ``logger`` on the connector.

    The folder listing reports through two attributes, reset before each listing:
    ``_folder_listing_complete`` (False when a child-folder listing was not proven complete)
    and ``_folders_maybe_present`` (folder ids the listing left out that may still exist).
    Mail removals and full folder reads are collected per mailbox and applied once all its
    folders were read; ``_full_sync_incomplete`` tells the full-sync sweep to unlink nothing.
    """

    # The filters that decide which folders are listed.
    FOLDER_FILTER_KEYS: tuple[SyncFilterKey, ...] = ()

    _folder_listing_complete: bool = True
    _folders_maybe_present: frozenset[str] | set[str] = frozenset()
    _pending_removals: list[Any] | None = None
    _pending_full_reads: list[Any] | None = None
    _full_sync_incomplete: bool = False

    def _folder_sync_point_key(self, user: AppUser, folder_id: str) -> str:
        """The sync point holding a folder's delta link."""
        return generate_record_sync_point_key(
            RecordType.MAIL.value, OutlookSyncPointKeys.SEGMENT_FOLDERS, f"{user.source_user_id}_{folder_id}"
        )

    def _full_sync_running(self) -> bool:
        running = getattr(self.data_entities_processor, "full_sync_running", None)
        # A processor without the check: assume a full sync, which deletes no unseen mail.
        return bool(running(self.connector_id)) if callable(running) else True

    async def _folders_listed_before_upgrade(self, user: AppUser, listed: list[MailFolder]) -> set[str]:
        """Folders an earlier version of the connector synced that this one may no longer list."""
        return set()

    async def _drop_missing_user_folders(
        self, user: AppUser, current_folder_ids: set[str], listed: list[MailFolder] | None = None,
    ) -> None:
        """Delete the folders (and their mail) that were listed last run and are gone now.

        Folder listings are not delta queries, so a deleted folder is only visible as an
        absence. The inventory is a sync point, which a full sync clears: a filter change
        therefore never reads as a deletion. A listing not proven complete drops nothing.
        On a mailbox's first inventory outside a full sync (an upgrade), the stored folders
        an earlier version synced and this one no longer lists are dropped.
        """
        if not self._folder_listing_complete:
            self.logger.warning(f"The folder listing of {user.email} was not complete; no folder is removed this run")
            self._full_sync_incomplete = True
            return
        key = generate_record_sync_point_key(
            RecordType.MAIL.value, OutlookSyncPointKeys.SEGMENT_FOLDER_INVENTORY, user.source_user_id
        )
        fingerprint = self._filter_fingerprint(*self.FOLDER_FILTER_KEYS)
        previous = await self.email_delta_sync_point.read_sync_point(key) or {}
        kept: set[str] = set()
        gone: set[str] = set()
        if previous.get(OutlookSyncPointKeys.FILTER_FINGERPRINT) == fingerprint:
            previous_ids = set(previous.get(OutlookSyncPointKeys.FOLDER_IDS) or [])
            kept = previous_ids & self._folders_maybe_present
            gone = previous_ids - current_folder_ids - kept
        elif not previous and not self._full_sync_running():
            try:
                candidates = await self._folders_listed_before_upgrade(user, listed or [])
            except Exception as e:
                # The inventory is not saved, so the next run looks again.
                self.logger.warning(f"Could not check {user.email} for folders synced before the upgrade: {e}")
                return
            for folder_id in candidates - current_folder_ids:
                if await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, folder_id):
                    gone.add(folder_id)
        if kept:
            # Their mail was not read this run, so a full sync did not see it.
            self.logger.warning(f"Keeping folders {sorted(kept)} of {user.email}: Graph did not return them this run")
            self._full_sync_incomplete = True
        failed: set[str] = set()
        for folder_id in gone:
            self.logger.info("Folder %s is gone from %s's mailbox; deleting it", folder_id, user.email)
            if not await self._delete_record_group(folder_id):
                failed.add(folder_id)
                continue
            # The folder can come back with the same id (restored, or included again);
            # its old link would then report no mail.
            await self.email_delta_sync_point.delete_sync_point(self._folder_sync_point_key(user, folder_id))
        await self.email_delta_sync_point.update_sync_point(
            key,
            {
                OutlookSyncPointKeys.FOLDER_IDS: sorted(current_folder_ids | kept | failed),
                OutlookSyncPointKeys.FILTER_FINGERPRINT: fingerprint,
            },
        )

    async def _delete_record_group(self, external_group_id: str) -> bool:
        """Delete a record group with everything below it. True once it is gone, also when an
        earlier delete (its parent's) already took it; False keeps it for the next run."""
        if await self.data_entities_processor.on_record_group_deleted(
            external_group_id=external_group_id, connector_id=self.connector_id,
        ):
            return True
        if await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, external_group_id) is None:
            return True
        self.logger.error(f"Deleting record group {external_group_id} failed; it is retried on the next run")
        return False

    def _filter_fingerprint(self, *keys: SyncFilterKey) -> str:
        """The filter values an inventory was taken under.

        A full sync normally clears the inventories on a filter change; if that clearing
        failed, a narrowed filter must still never read as "these were deleted".
        """
        parts = []
        for key in keys:
            flt = self.sync_filters.get(key)
            if flt is None or flt.is_empty():
                parts.append(f"{key.value}=")
            else:
                operator = flt.get_operator()
                parts.append(f"{key.value}={getattr(operator, 'value', operator)}:{flt.get_value()!r}")
        return "|".join(parts)

    @staticmethod
    def _saved_delta_link(sync_point: dict | None) -> str | None:
        """A folder's saved delta link, if it was saved with immutable ids.

        A link (and the mail it read) saved before keeps the old ids and must not be continued
        under the new id type, so that folder is read again in full; the full read's cleanup
        then deletes the mail stored under old ids.
        """
        if sync_point and sync_point.get(OutlookSyncPointKeys.ID_TYPE) == IMMUTABLE_ID_TYPE:
            return sync_point.get(OutlookSyncPointKeys.DELTA_LINK)
        return None

    async def _save_folder_link(
        self, sync_point_key: str, sync_point_data: dict, folder_id: str, removed_ids: list[str]
    ) -> None:
        """Save a folder's new delta link once the mail it reported removed is deleted.

        Inside a mailbox run the deletes wait for ``_apply_mail_removals``, after every
        folder was read: a mail moved between two synced folders keeps its id, so its record
        must follow it to the new folder whichever of the two is read first.
        """
        save = functools.partial(
            self.email_delta_sync_point.update_sync_point, sync_point_key,
            {**sync_point_data, OutlookSyncPointKeys.ID_TYPE: IMMUTABLE_ID_TYPE, OutlookSyncPointKeys.HELD_BACK_RUNS: 0},
            encrypt_fields=[OutlookSyncPointKeys.ENCRYPT_FIELD_DELTA_LINK],
        )
        pending = self._pending_removals
        if removed_ids and pending is not None:
            pending.append((folder_id, removed_ids, save))
            return
        await self._delete_removed_mail(folder_id, removed_ids)
        await save()

    async def _hold_back_folder_link(
        self, sync_point_key: str, sync_point: dict | None, sync_point_data: dict,
        folder_id: str, removed_ids: list[str], failed_ids: list[str],
    ) -> None:
        """Some mails failed: keep the old link so delta returns them again next run.

        A mail that fails every run would hold the folder back forever (replayed, or read in
        full, each run), so after ``MAX_HELD_BACK_RUNS`` runs in a row the new link is saved
        anyway and the failed mails are counted as skipped.
        """
        self._full_sync_incomplete = True
        held = int((sync_point or {}).get(OutlookSyncPointKeys.HELD_BACK_RUNS) or 0)
        if held >= OutlookSyncConfig.MAX_HELD_BACK_RUNS:
            self.logger.error(
                f"Folder {folder_id}: mails {failed_ids} failed {held + 1} runs in a row; "
                "saving the delta link without them"
            )
            self._skipped["mails"] = self._skipped.get("mails", 0) + len(failed_ids)
            await self._save_folder_link(sync_point_key, sync_point_data, folder_id, removed_ids)
            return
        self.logger.warning(
            f"Folder {folder_id}: {len(failed_ids)} change(s) failed; keeping the delta link to retry them"
        )
        await self.email_delta_sync_point.update_sync_point(
            sync_point_key, {OutlookSyncPointKeys.HELD_BACK_RUNS: held + 1},
        )

    async def _note_full_read(self, folder_id: str, seen_ids: set[str]) -> None:
        """A folder was read in full: delete its stored mail the read did not see.

        A full read reports no removals, so mail deleted while a delta link was expired, or
        stored under an old id type, shows only as an absence. Inside a mailbox run this
        waits for ``_apply_mail_removals``, so a mail that moved to another folder this run
        has its record there first.
        """
        if self._full_sync_running():
            # A full sync reads every folder in full; what it did not see is unlinked by its
            # sweep, which a filter change (a narrower date window) must not turn into deletes.
            return
        pending = self._pending_full_reads
        if pending is not None:
            pending.append((folder_id, seen_ids))
            return
        await self._delete_unseen_mail(folder_id, seen_ids)

    async def _apply_mail_removals(self) -> None:
        """Delete the mail each folder's delta reported removed, then save that folder's link;
        then delete the mail each full folder read did not see."""
        pending, self._pending_removals = self._pending_removals or [], None
        for folder_id, removed_ids, save in pending:
            try:
                await self._delete_removed_mail(folder_id, removed_ids)
            except Exception as e:
                # The old link stays, so delta reports these removals again next run.
                self.logger.error(f"Deleting mail removed from folder {folder_id} failed; keeping its delta link: {e}")
                self._full_sync_incomplete = True
                continue
            await save()
        full_reads, self._pending_full_reads = self._pending_full_reads or [], None
        for folder_id, seen_ids in full_reads:
            try:
                await self._delete_unseen_mail(folder_id, seen_ids)
            except Exception as e:
                self.logger.error(f"Deleting mail no longer in folder {folder_id} failed: {e}")
                self._full_sync_incomplete = True

    async def _delete_removed_mail(self, folder_id: str, removed_ids: list[str]) -> None:
        for message_id in removed_ids:
            stored = await self.data_entities_processor.get_record_by_external_id(self.connector_id, message_id)
            # Gone already, or moved into another synced folder, whose delta moved the record.
            if stored is None or stored.external_record_group_id not in (None, folder_id):
                continue
            self.logger.info(f"Deleting mail {message_id} and its attachments, removed from folder {folder_id}")
            await self.data_entities_processor.delete_record_by_external_id(self.connector_id, message_id)

    def _received_window_ms(self) -> tuple[int | None, int | None]:
        """The Received Date filter as (start, end) epoch ms, each None when open."""
        flt = self.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        if flt is None or flt.is_empty():
            return None, None
        return flt.get_datetime_start(), flt.get_datetime_end()

    async def _delete_unseen_mail(self, folder_id: str, seen_ids: set[str]) -> None:
        """Delete the folder's stored mails (their attachments go with them) whose id is not in
        ``seen_ids``.

        Every page of the read was handed on, mails that failed to save included, so a stored
        mail it lacks is gone from the folder or was saved under another id type. Only mail
        surely inside the Received Date window counts: the read could not see mail outside it.
        The record keeps its creation time, at or before its receipt, so the window's end is
        moved a day earlier.
        shortcut: assumes a mail is received within a day of its creation; store receivedDateTime
        on the record if mail drafted long before sending must be told apart at the window's end.
        """
        start, end = self._received_window_ms()
        if end is not None:
            end -= _DAY_MS
        after_key: str | None = None
        while True:
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, folder_id, _RECORD_SCAN_PAGE_SIZE, after_key,
            )
            for record in page:
                created = record.source_created_at
                in_window = created is not None and (start is None or created >= start) and (end is None or created < end)
                if (record.parent_external_record_id is None and record.external_record_id not in seen_ids
                        and (in_window or (start is None and end is None))):
                    self.logger.info(f"Deleting mail {record.external_record_id}: no longer in folder {folder_id}")
                    await self.data_entities_processor.delete_record_by_external_id(
                        self.connector_id, record.external_record_id
                    )
            if len(page) < _RECORD_SCAN_PAGE_SIZE:
                break
            after_key = page[-1].id
