# pyright: ignore-file

"""Helpers for Google Drive Workspace folder-filter + entity integration tests."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import mimetypes
import os
import time
import uuid
from datetime import datetime
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from google.auth.transport import requests as google_auth_requests  # type: ignore[import-not-found]
from google.oauth2 import service_account  # type: ignore[import-not-found]
from googleapiclient.discovery import build  # type: ignore[import-not-found]
from googleapiclient.errors import HttpError  # type: ignore[import-not-found]

from app.config.constants.arangodb import MimeTypes  # type: ignore[import-not-found]
from app.connectors.sources.google.drive.utils.drive_mime import (  # type: ignore[import-not-found]
    SHORTCUT_MIME,
)
from app.connectors.sources.google.common.drive_file_fields import (  # type: ignore[import-not-found]
    DRIVE_WORKSPACE_FILE_GET_FIELDS,
)
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.pipeshub_users import (  # type: ignore[import-not-found]  # noqa: F401 - re-exported
    _user_id_from_payload,
    ensure_pipeshub_user_exists,
    find_pipeshub_user_by_email,
)

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
    from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("drive-workspace-test-utils")

FOLDER_MIME = MimeTypes.GOOGLE_DRIVE_FOLDER.value
# Full drive scope for fixture create/delete under DWD impersonation.
FULL_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_UPLOAD_ATTEMPTS = 3
ADMIN_DIRECTORY_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
]

ENV_SA_JSON = "GOOGLE_DRIVE_WORKSPACE_SERVICE_ACCOUNT_JSON"
ENV_ADMIN_EMAIL = "GOOGLE_DRIVE_WORKSPACE_ADMIN_EMAIL"
ENV_TEST_USER = "GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL"
# A second Workspace member in the same domain, to share files with.
ENV_SECOND_USER = "GOOGLE_DRIVE_WORKSPACE_SECOND_USER_EMAIL"

# A freshly created Shared Drive lags its create call twice over: in the member
# drives.list the connector discovers drives through, and in the drive-wide
# files.list index it enumerates their contents from. Both have exceeded 60s in
# CI, so this matches the suite's sync budget instead of undercutting it.
PROPAGATION_TIMEOUT_SEC = float(
    os.getenv("GOOGLE_DRIVE_WORKSPACE_PROPAGATION_TIMEOUT", "300")
)


def connector_filters(
    *,
    sync_values: Optional[dict[str, Any]] = None,
    manual_indexing: bool = True,
) -> dict[str, Any]:
    """Connector ``filters`` with ``enable_manual_sync`` set explicitly, plus optional sync values.

    Manual indexing keeps sync-only suites off the extraction/embedding pipeline;
    pass ``manual_indexing=False`` where records must reach ``COMPLETED``.
    """
    filters: dict[str, Any] = {
        "indexing": {
            "values": {
                "enable_manual_sync": {
                    "operator": "is",
                    "type": "boolean",
                    "value": manual_indexing,
                }
            }
        }
    }
    if sync_values:
        filters["sync"] = {"values": sync_values}
    return filters


def require_drive_workspace_env() -> tuple[str, str, str]:
    """Return (sa_json, admin_email, test_user_email) or raise for pytest.skip callers."""
    sa_json = os.getenv(ENV_SA_JSON, "").strip()
    admin_email = os.getenv(ENV_ADMIN_EMAIL, "").strip()
    test_user = os.getenv(ENV_TEST_USER, "").strip()
    missing = [
        name
        for name, val in (
            (ENV_SA_JSON, sa_json),
            (ENV_ADMIN_EMAIL, admin_email),
            (ENV_TEST_USER, test_user),
        )
        if not val
    ]
    if missing:
        raise ValueError(
            "Drive Workspace credentials not set: " + ", ".join(missing)
        )
    return sa_json, admin_email, test_user


def load_service_account_info(sa_json: str, admin_email: str) -> dict[str, Any]:
    """Parse SA JSON string and attach adminEmail for connector auth payloads."""
    info = json.loads(sa_json)
    if not isinstance(info, dict):
        raise ValueError(f"{ENV_SA_JSON} must be a JSON object")
    if info.get("type") != "service_account":
        raise ValueError(f"{ENV_SA_JSON} must have type=service_account")
    info = dict(info)
    info["adminEmail"] = admin_email
    return info


async def build_drive_datasource(
    sa_json: str,
    admin_email: str,
    user_email: str,
) -> GoogleDriveDataSource:
    """Build GoogleDriveDataSource impersonating user_email with full drive scope.

    Uses only ``https://www.googleapis.com/auth/drive`` so DWD must authorize that
    exact scope (required for fixture create/delete). Connector sync still uses its
    own optimized Drive scopes via Pipeshub.
    """
    _ = admin_email
    service_account_info = json.loads(sa_json)
    if not isinstance(service_account_info, dict):
        raise ValueError(f"{ENV_SA_JSON} must be a JSON object")
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=[FULL_DRIVE_SCOPE],
        subject=user_email,
    )
    client = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    return GoogleDriveDataSource(client)


async def create_drive_folder(
    drive: GoogleDriveDataSource,
    name: str,
    parent_id: Optional[str] = None,
) -> str:
    """Create a Drive folder; return its file id."""
    body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
    if parent_id:
        body["parents"] = [parent_id]
    created = await drive.files_create(
        body=body,
        supportsAllDrives=True,
        fields="id,name,mimeType",
    )
    folder_id = created.get("id")
    if not folder_id:
        raise RuntimeError(f"files_create returned no id for folder {name!r}: {created}")
    return str(folder_id)


async def create_drive_text_file(
    drive: GoogleDriveDataSource,
    name: str,
    parent_id: str,
    content: str,
) -> str:
    """Create a plain-text Drive file under parent_id; return its file id."""
    created = await drive.files_create_with_media(
        file_metadata={"name": name, "parents": [parent_id]},
        content=content.encode("utf-8"),
        mime_type="text/plain",
        supportsAllDrives=True,
    )
    file_id = created.get("id")
    if not file_id:
        raise RuntimeError(f"files_create_with_media returned no id for {name!r}: {created}")
    return str(file_id)


async def create_drive_google_file(
    drive: GoogleDriveDataSource,
    name: str,
    parent_id: str,
    mime_type: str,
) -> str:
    """Create a native Google Doc/Sheet/Slide (empty stub) under parent_id."""
    created = await drive.files_create(
        body={"name": name, "mimeType": mime_type, "parents": [parent_id]},
        supportsAllDrives=True,
        fields="id,name,mimeType",
    )
    file_id = created.get("id")
    if not file_id:
        raise RuntimeError(f"files_create returned no id for {name!r}: {created}")
    return str(file_id)


async def create_drive_ids_filter_fixtures(
    drive: GoogleDriveDataSource,
    drive_a_id: str,
    drive_b_id: str,
) -> dict[str, str]:
    """Create one root file on Shared Drive A and one on B for pure drive_ids ITs."""
    file_a_name = "drive-ids-a.txt"
    file_b_name = "drive-ids-b.txt"
    file_a_id = await create_drive_text_file(
        drive,
        file_a_name,
        parent_id=drive_a_id,
        content="pipeshub drive_ids filter it — drive A\n",
    )
    file_b_id = await create_drive_text_file(
        drive,
        file_b_name,
        parent_id=drive_b_id,
        content="pipeshub drive_ids filter it — drive B\n",
    )
    fixtures = {
        "drive_a_id": drive_a_id,
        "drive_b_id": drive_b_id,
        "drive_a_file_id": file_a_id,
        "drive_a_file_name": file_a_name,
        "drive_b_file_id": file_b_id,
        "drive_b_file_name": file_b_name,
    }
    logger.info("Created drive_ids filter fixtures: %s", fixtures)
    return fixtures


async def create_extension_filter_fixtures(
    drive: GoogleDriveDataSource,
) -> dict[str, str]:
    """Create seed folder with Docs/Sheets/Slides + keep.txt for extension-filter ITs."""
    root_name = f"pipeshub-it-ext-{uuid.uuid4().hex[:8]}"
    root_id = await create_drive_folder(drive, root_name)
    seed_id = await create_drive_folder(drive, "seed", parent_id=root_id)

    doc_name = "ext-doc"
    sheet_name = "ext-sheet"
    slide_name = "ext-slide"
    txt_name = "keep.txt"

    doc_id = await create_drive_google_file(
        drive, doc_name, seed_id, MimeTypes.GOOGLE_DOCS.value
    )
    sheet_id = await create_drive_google_file(
        drive, sheet_name, seed_id, MimeTypes.GOOGLE_SHEETS.value
    )
    slide_id = await create_drive_google_file(
        drive, slide_name, seed_id, MimeTypes.GOOGLE_SLIDES.value
    )
    txt_id = await create_drive_text_file(
        drive, txt_name, parent_id=seed_id, content="extension filter keep\n"
    )

    fixtures = {
        "root_folder_id": root_id,
        "root_folder_name": root_name,
        "seed_folder_id": seed_id,
        "seed_folder_name": "seed",
        "doc_file_id": doc_id,
        "doc_file_name": doc_name,
        "sheet_file_id": sheet_id,
        "sheet_file_name": sheet_name,
        "slide_file_id": slide_id,
        "slide_file_name": slide_name,
        "txt_file_id": txt_id,
        "txt_file_name": txt_name,
    }
    logger.info("Created extension-filter fixtures: %s", fixtures)
    return fixtures


async def move_drive_item(
    drive: GoogleDriveDataSource,
    file_id: str,
    *,
    new_parent_id: str,
    old_parent_id: str,
) -> None:
    """Reparent a Drive file/folder from old_parent_id to new_parent_id."""
    await drive.files_update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parent_id,
        supportsAllDrives=True,
    )
    logger.info(
        "Moved Drive item %s from %s to %s",
        file_id,
        old_parent_id,
        new_parent_id,
    )


def _files_update_with_body(
    drive: GoogleDriveDataSource,
    file_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """PATCH files.update with a metadata body (typed files_update has no body param)."""
    request = drive.client.files().update(  # type: ignore[attr-defined]
        fileId=file_id,
        body=body,
        supportsAllDrives=True,
    )
    return request.execute()


async def rename_drive_item(
    drive: GoogleDriveDataSource,
    file_id: str,
    name: str,
) -> None:
    """Rename a Drive file/folder."""
    _files_update_with_body(drive, file_id, {"name": name})
    logger.info("Renamed Drive item %s to %r", file_id, name)


async def update_drive_file_content(
    drive: GoogleDriveDataSource,
    file_id: str,
    content: str,
    *,
    mime_type: str = "text/plain",
) -> dict[str, Any]:
    """Overwrite a Drive file's media content; return updated metadata."""
    import io

    from googleapiclient.http import MediaIoBaseUpload  # type: ignore[import-not-found]

    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype=mime_type,
        resumable=True,
    )
    updated = drive.client.files().update(  # type: ignore[attr-defined]
        fileId=file_id,
        media_body=media,
        supportsAllDrives=True,
        fields="id,name,size,md5Checksum,sha1Checksum,headRevisionId,version,modifiedTime",
    ).execute()
    logger.info(
        "Updated Drive file content %s (revision=%s size=%s)",
        file_id,
        updated.get("headRevisionId") or updated.get("version"),
        updated.get("size"),
    )
    return updated if isinstance(updated, dict) else {}


async def trash_drive_item(
    drive: GoogleDriveDataSource,
    file_id: str,
    *,
    trashed: bool = True,
) -> None:
    """Move a Drive item to the trash, or restore it with ``trashed=False``."""
    _files_update_with_body(drive, file_id, {"trashed": trashed})
    logger.info("%s Drive item %s", "Trashed" if trashed else "Restored", file_id)


async def delete_drive_item_permanently(
    drive: GoogleDriveDataSource,
    file_id: str,
) -> None:
    """Delete a Drive item for good, bypassing the trash. Raises on failure."""
    await drive.files_delete(fileId=file_id, supportsAllDrives=True)
    logger.info("Permanently deleted Drive item %s", file_id)


async def create_drive_shortcut(
    drive: GoogleDriveDataSource,
    target_id: str,
    parent_id: str,
    name: str,
) -> str:
    """Create a shortcut to *target_id* under *parent_id*; return the shortcut's id."""
    created = drive.client.files().create(  # type: ignore[attr-defined]
        body={
            "name": name,
            "mimeType": SHORTCUT_MIME,
            "parents": [parent_id],
            "shortcutDetails": {"targetId": target_id},
        },
        supportsAllDrives=True,
        fields="id",
    ).execute()
    shortcut_id = created.get("id")
    if not shortcut_id:
        raise RuntimeError(f"files.create returned no id for shortcut to {target_id}: {created}")
    logger.info("Created shortcut %s -> %s under %s", shortcut_id, target_id, parent_id)
    return str(shortcut_id)


async def upload_drive_file_resumable(
    drive: GoogleDriveDataSource,
    local_path: Path,
    parent_id: str,
    *,
    name: Optional[str] = None,
    mime_type: str = "application/octet-stream",
    convert_to_mime: Optional[str] = None,
) -> str:
    """Upload *local_path* in resumable chunks (large files); return the new file id.

    ``files_create_with_media`` holds the whole body in memory and sends it in one
    request, which is not how a 200 MB file reaches Drive in practice.
    """
    from googleapiclient.http import MediaFileUpload  # type: ignore[import-not-found]

    body: dict[str, Any] = {"name": name or local_path.name, "parents": [parent_id]}
    if convert_to_mime:
        body["mimeType"] = convert_to_mime

    def _upload() -> dict[str, Any]:
        media = MediaFileUpload(
            str(local_path), mimetype=mime_type, resumable=True, chunksize=16 * 1024 * 1024
        )
        request = drive.client.files().create(  # type: ignore[attr-defined]
            body=body, media_body=media, supportsAllDrives=True, fields="id,size,mimeType"
        )
        response = None
        while response is None:
            _status, response = request.next_chunk()
        return response

    created = await _create_upload_once(
        drive, parent_id, body["name"], lambda: asyncio.to_thread(_upload)
    )
    file_id = created.get("id")
    if not file_id:
        raise RuntimeError(f"resumable upload returned no id for {local_path}: {created}")
    logger.info(
        "Uploaded %s (%s bytes, %s) as Drive file %s under %s",
        local_path.name, created.get("size"), created.get("mimeType"), file_id, parent_id,
    )
    return str(file_id)


async def create_shared_drive(drive: GoogleDriveDataSource, name: str) -> str:
    """Create a Shared Drive; return its drive id.

    The typed ``drives_create`` wrapper omits the name body, so this calls the
    client directly with ``requestId`` + ``body={"name": ...}``.
    """
    request_id = str(uuid.uuid4())
    created = drive.client.drives().create(  # type: ignore[attr-defined]
        requestId=request_id,
        body={"name": name},
    ).execute()
    drive_id = created.get("id")
    if not drive_id:
        raise RuntimeError(f"drives.create returned no id for {name!r}: {created}")
    logger.info("Created Shared Drive %r id=%s", name, drive_id)
    return str(drive_id)


async def rename_shared_drive(drive: GoogleDriveDataSource, drive_id: str, name: str) -> None:
    """Rename a Shared Drive (the caller must be an organizer)."""
    drive.client.drives().update(driveId=drive_id, body={"name": name}).execute()  # type: ignore[attr-defined]
    logger.info("Renamed Shared Drive %s to %r", drive_id, name)


async def create_shared_drive_lifecycle_fixtures(
    drive: GoogleDriveDataSource,
) -> dict[str, str]:
    """A fresh Shared Drive holding ``sd-folder/sd-file.txt`` and ``group-share.txt``."""
    suffix = uuid.uuid4().hex[:8]
    drive_name = f"pipeshub-it-sd-lc-{suffix}"
    drive_id = await create_shared_drive(drive, drive_name)
    folder_id = await create_drive_folder(drive, "sd-folder", parent_id=drive_id)
    fixtures = {
        "drive_id": drive_id,
        "drive_name": drive_name,
        "folder_id": folder_id,
        "file_id": await create_drive_text_file(
            drive, "sd-file.txt", parent_id=folder_id, content=f"shared drive lifecycle {suffix}\n"
        ),
        "group_file_id": await create_drive_text_file(
            drive, "group-share.txt", parent_id=drive_id, content=f"group share {suffix}\n"
        ),
    }
    logger.info("Created Shared Drive lifecycle fixtures: %s", fixtures)
    return fixtures


async def list_shared_drive_ids(drive: GoogleDriveDataSource) -> set[str]:
    """Return all Shared Drive ids visible via member ``drives.list`` (paginated)."""
    found: set[str] = set()
    page_token: Optional[str] = None
    while True:
        resp = await drive.drives_list(pageSize=100, pageToken=page_token)
        for entry in resp.get("drives") or []:
            drive_id = entry.get("id")
            if drive_id:
                found.add(str(drive_id))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return found


async def wait_until_shared_drives_listed(
    drive: GoogleDriveDataSource,
    drive_ids: list[str],
    *,
    timeout: float = PROPAGATION_TIMEOUT_SEC,
    interval: float = 2.0,
) -> None:
    """Poll member ``drives.list`` until every id is visible.

    Connector per-user Shared Drive sync uses the same API (no domain-admin
    access). Newly created Shared Drives can lag before they appear there even
    when admin ``drives.list`` / folder probes already succeed.
    """
    from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]

    wanted = {str(d) for d in drive_ids if d}
    if not wanted:
        return

    async def _all_visible() -> set[str] | None:
        found = await list_shared_drive_ids(drive)
        missing = wanted - found
        if missing:
            logger.info(
                "Waiting for Shared Drives in drives.list; missing=%s (listed=%d)",
                sorted(missing),
                len(found),
            )
            return None
        return found & wanted

    await async_poll_until(
        _all_visible,
        timeout=timeout,
        interval=interval,
        description=f"Shared Drives visible in drives.list: {sorted(wanted)}",
    )
    logger.info("Shared Drives visible in drives.list: %s", sorted(wanted))


async def list_shared_drive_file_ids(
    drive: GoogleDriveDataSource,
    drive_id: str,
) -> set[str]:
    """Return every file id a drive-wide ``files.list`` reports for ``drive_id``.

    Mirrors the connector's Shared Drive enumeration (``corpora=drive``, no ``q``)
    on purpose: that call reads the drive's search index, which lags well behind
    both ``files.get`` and ``'<parent>' in parents`` queries on new items.
    """
    found: set[str] = set()
    page_token: Optional[str] = None
    while True:
        resp = await drive.files_list(
            driveId=drive_id,
            corpora="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=1000,
            pageToken=page_token,
            fields="nextPageToken, files(id)",
        )
        for entry in resp.get("files") or []:
            file_id = entry.get("id")
            if file_id:
                found.add(str(file_id))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return found


async def wait_until_shared_drive_files_listed(
    drive: GoogleDriveDataSource,
    drive_id: str,
    file_ids: list[str],
    *,
    timeout: float = PROPAGATION_TIMEOUT_SEC,
    interval: float = 5.0,
) -> None:
    """Poll the drive-wide ``files.list`` until every id is visible.

    A sync started before this settles enumerates an empty (or partial) drive,
    saves its start page token anyway and reports success with no records. The
    missing items never arrive after that: the incremental path only carries
    changes made *after* that token, so a fixture can only poll the graph until
    it times out.
    """
    from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]

    wanted = {str(f) for f in file_ids if f}
    if not wanted:
        return

    async def _all_visible() -> set[str] | None:
        found = await list_shared_drive_file_ids(drive, drive_id)
        missing = wanted - found
        if missing:
            logger.info(
                "Waiting for items in drive-wide files.list for %s; missing=%s (listed=%d)",
                drive_id,
                sorted(missing),
                len(found),
            )
            return None
        return found & wanted

    await async_poll_until(
        _all_visible,
        timeout=timeout,
        interval=interval,
        description=f"items visible in files.list for Shared Drive {drive_id}: {sorted(wanted)}",
    )
    logger.info("Items visible in files.list for Shared Drive %s: %s", drive_id, sorted(wanted))


async def _user_corpus_ids_present(
    drive: GoogleDriveDataSource,
    wanted: set[str],
) -> set[str]:
    """Ids from ``wanted`` visible in the user-corpus listing; stops once all are found."""
    found: set[str] = set()
    page_token: Optional[str] = None
    while True:
        resp = await drive.files_list(
            pageSize=1000,
            pageToken=page_token,
            fields="nextPageToken, files(id)",
        )
        for entry in resp.get("files") or []:
            file_id = entry.get("id")
            if file_id and str(file_id) in wanted:
                found.add(str(file_id))
        if found == wanted:
            return found
        page_token = resp.get("nextPageToken")
        if not page_token:
            return found


async def wait_until_drive_files_listed(
    drive: GoogleDriveDataSource,
    file_ids: list[str],
    *,
    timeout: float = PROPAGATION_TIMEOUT_SEC,
    interval: float = 5.0,
) -> None:
    """Poll the user-corpus ``files.list`` until every id is visible.

    Both Drive connectors walk My Drive through this listing — the workspace one
    unfiltered, the personal one with ``trashed=false``, which cannot hide a file the
    fixtures just created. It reads the search index, so a sync started before that
    settles never sees the fixture tree, and the graph poll after it can only time out.
    """
    from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]

    wanted = {str(f) for f in file_ids if f}
    if not wanted:
        return

    async def _all_visible() -> set[str] | None:
        found = await _user_corpus_ids_present(drive, wanted)
        missing = wanted - found
        if missing:
            logger.info(
                "Waiting for items in user-corpus files.list; missing=%s", sorted(missing)
            )
            return None
        return found

    await async_poll_until(
        _all_visible,
        timeout=timeout,
        interval=interval,
        description=f"items visible in user files.list: {sorted(wanted)}",
    )
    logger.info("Items visible in user files.list: %s", sorted(wanted))


async def delete_shared_drive(
    drive: GoogleDriveDataSource,
    drive_id: Optional[str],
    *,
    admin_drive: Optional[GoogleDriveDataSource] = None,
) -> None:
    """Permanently delete a Shared Drive. Best-effort.

    Prefers admin delete with ``allowItemDeletion`` (required by the API together
    with ``useDomainAdminAccess``). Falls back to organizer delete after that.
    """
    if not drive_id:
        return

    if admin_drive is not None:
        try:
            await admin_drive.drives_delete(
                driveId=drive_id,
                useDomainAdminAccess=True,
                allowItemDeletion=True,
            )
            logger.info("Deleted Shared Drive %s (admin + allowItemDeletion)", drive_id)
            return
        except Exception as e:
            logger.warning(
                "Admin delete failed for Shared Drive %s: %s; trying organizer delete",
                drive_id,
                e,
            )

    try:
        await drive.drives_delete(driveId=drive_id)
        logger.info("Deleted Shared Drive %s (organizer)", drive_id)
    except Exception as e:
        logger.warning("Failed to delete Shared Drive %s: %s", drive_id, e)


IT_SHARED_DRIVE_PREFIXES = ("phit-", "pipeshub-it-")
# A CI job running both graph backends takes about 6h, so a younger drive may belong
# to a run still in progress.
STALE_SHARED_DRIVE_AGE_SEC = 12 * 3600


async def delete_stale_it_shared_drives(
    admin_drive: GoogleDriveDataSource,
    *,
    max_age_sec: float = STALE_SHARED_DRIVE_AGE_SEC,
) -> list[str]:
    """Delete IT Shared Drives a killed run never tore down. Best-effort.

    The unfiltered connectors sync the whole tenant, so a leftover drive's items
    show up in every later run's graph next to the ones that run created.
    """
    cutoff = time.time() - max_age_sec
    stale: list[dict[str, str]] = []
    page_token: Optional[str] = None
    while True:
        resp = await admin_drive.drives_list(
            pageSize=100,
            pageToken=page_token,
            useDomainAdminAccess=True,
            fields="nextPageToken, drives(id, name, createdTime)",
        )
        for entry in resp.get("drives") or []:
            name = entry.get("name") or ""
            created = entry.get("createdTime")
            if (
                name.startswith(IT_SHARED_DRIVE_PREFIXES)
                and created
                and datetime.fromisoformat(created).timestamp() < cutoff
            ):
                stale.append(entry)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    for entry in stale:
        logger.info("Deleting stale IT Shared Drive %r (%s, created %s)", entry["name"], entry["id"], entry["createdTime"])
        await delete_shared_drive(admin_drive, entry["id"], admin_drive=admin_drive)
    return [entry["id"] for entry in stale]


async def create_folder_filter_fixtures(
    drive: GoogleDriveDataSource,
) -> dict[str, str]:
    """Create the folder-filter fixture tree under the impersonated user's My Drive.

    Layout::

        {root}/
          seed/
            nested/
              child.txt
          out_of_scope/
            sibling.txt
    """
    suffix = uuid.uuid4().hex[:8]
    root_name = f"pipeshub-it-drive-ff-{suffix}"

    root_id = await create_drive_folder(drive, root_name)
    seed_id = await create_drive_folder(drive, "seed", parent_id=root_id)
    nested_id = await create_drive_folder(drive, "nested", parent_id=seed_id)
    child_id = await create_drive_text_file(
        drive, "child.txt", parent_id=nested_id, content="pipeshub drive folder-filter it\n"
    )
    oos_id = await create_drive_folder(drive, "out_of_scope", parent_id=root_id)
    sibling_id = await create_drive_text_file(
        drive, "sibling.txt", parent_id=oos_id, content="out of scope\n"
    )

    fixtures = {
        "root_folder_id": root_id,
        "root_folder_name": root_name,
        "seed_folder_id": seed_id,
        "seed_folder_name": "seed",
        "nested_folder_id": nested_id,
        "nested_folder_name": "nested",
        "child_file_id": child_id,
        "child_file_name": "child.txt",
        "oos_folder_id": oos_id,
        "oos_folder_name": "out_of_scope",
        "oos_file_id": sibling_id,
        "oos_file_name": "sibling.txt",
    }
    logger.info("Created Drive folder-filter fixtures: %s", fixtures)
    return fixtures


async def create_lifecycle_fixtures(drive: GoogleDriveDataSource) -> dict[str, str]:
    """Tree for the trash / delete / rename / same-name / outside-share lifecycle ITs.

    root/
      trash-me.txt
      doomed/            (deleted for good in one test)
        doomed-a.txt
        doomed-sub/
          doomed-b.txt
      renamable/         (renamed in one test)
        renamable-child.txt
      dup/
        same-name.txt    (two files with this exact name)
        same-name.txt
      share-out.txt      (shared with someone outside the Workspace)
    """
    suffix = uuid.uuid4().hex[:8]
    root_name = f"pipeshub-it-drive-lc-{suffix}"
    root_id = await create_drive_folder(drive, root_name)
    doomed_id = await create_drive_folder(drive, "doomed", parent_id=root_id)
    doomed_sub_id = await create_drive_folder(drive, "doomed-sub", parent_id=doomed_id)
    renamable_id = await create_drive_folder(drive, "renamable", parent_id=root_id)
    dup_id = await create_drive_folder(drive, "dup", parent_id=root_id)
    fixtures = {
        "root_folder_id": root_id,
        "root_folder_name": root_name,
        "trash_file_id": await create_drive_text_file(
            drive, "trash-me.txt", parent_id=root_id,
            content=f"Lifecycle trash fixture {suffix}. The quarterly lighthouse audit covers lamp hours.\n",
        ),
        "doomed_folder_id": doomed_id,
        "doomed_a_id": await create_drive_text_file(
            drive, "doomed-a.txt", parent_id=doomed_id, content=f"doomed a {suffix}\n"
        ),
        "doomed_sub_id": doomed_sub_id,
        "doomed_b_id": await create_drive_text_file(
            drive, "doomed-b.txt", parent_id=doomed_sub_id, content=f"doomed b {suffix}\n"
        ),
        "renamable_folder_id": renamable_id,
        "renamable_child_id": await create_drive_text_file(
            drive, "renamable-child.txt", parent_id=renamable_id, content=f"rename child {suffix}\n"
        ),
        "dup_folder_id": dup_id,
        "dup_first_id": await create_drive_text_file(
            drive, "same-name.txt", parent_id=dup_id, content=f"first twin {suffix}\n"
        ),
        "dup_second_id": await create_drive_text_file(
            drive, "same-name.txt", parent_id=dup_id, content=f"second twin {suffix}\n"
        ),
        "share_out_file_id": await create_drive_text_file(
            drive, "share-out.txt", parent_id=root_id, content=f"outside share {suffix}\n"
        ),
    }
    logger.info("Created lifecycle fixtures: %s", fixtures)
    return fixtures


async def create_shared_drive_folder_filter_fixtures(
    drive: GoogleDriveDataSource,
    drive_a_id: str,
    drive_b_id: str,
) -> dict[str, str]:
    """Create the Shared Drive folder-filter fixture trees.

    Layout::

        Drive A/
          seed/
            nested/
              child.txt
          out_of_scope/
            sibling.txt
          {root-seed}/
            root-child.txt
        Drive B/
          ignored.txt

    The root-seed pair is asserted on by tc_sd_ff_004, which builds its own connector
    minutes later. It is created here, with the rest of the tree, because a drive-wide
    files.list can omit an item created seconds earlier and a first sync reads nothing
    else — see ``wait_until_shared_drive_files_listed``.
    """
    seed_id = await create_drive_folder(drive, "seed", parent_id=drive_a_id)
    nested_id = await create_drive_folder(drive, "nested", parent_id=seed_id)
    child_id = await create_drive_text_file(
        drive,
        "child.txt",
        parent_id=nested_id,
        content="pipeshub shared-drive folder-filter it\n",
    )
    oos_id = await create_drive_folder(drive, "out_of_scope", parent_id=drive_a_id)
    sibling_id = await create_drive_text_file(
        drive, "sibling.txt", parent_id=oos_id, content="out of scope\n"
    )
    ignored_id = await create_drive_text_file(
        drive,
        "ignored.txt",
        parent_id=drive_b_id,
        content="drive B ignored by drive_ids\n",
    )
    root_folder_name = f"root-seed-{uuid.uuid4().hex[:6]}"
    root_folder_id = await create_drive_folder(
        drive, root_folder_name, parent_id=drive_a_id
    )
    root_file_id = await create_drive_text_file(
        drive,
        "root-child.txt",
        parent_id=root_folder_id,
        content="shared drive root seed it\n",
    )

    fixtures = {
        "drive_a_id": drive_a_id,
        "drive_b_id": drive_b_id,
        "seed_folder_id": seed_id,
        "seed_folder_name": "seed",
        "nested_folder_id": nested_id,
        "nested_folder_name": "nested",
        "child_file_id": child_id,
        "child_file_name": "child.txt",
        "oos_folder_id": oos_id,
        "oos_folder_name": "out_of_scope",
        "oos_file_id": sibling_id,
        "oos_file_name": "sibling.txt",
        "drive_b_ignored_file_id": ignored_id,
        "drive_b_ignored_file_name": "ignored.txt",
        "root_folder_id": root_folder_id,
        "root_folder_name": root_folder_name,
        "root_file_id": root_file_id,
        "root_file_name": "root-child.txt",
    }
    logger.info("Created Shared Drive folder-filter fixtures: %s", fixtures)
    return fixtures


def _create_permission(
    drive: GoogleDriveDataSource,
    file_id: str,
    body: dict[str, Any],
    **params: Any,
) -> str:
    """permissions.create with a body (the typed wrapper takes none); return its id."""
    created = drive.client.permissions().create(  # type: ignore[attr-defined]
        fileId=file_id,
        body=body,
        supportsAllDrives=True,
        fields="id",
        **params,
    ).execute()
    permission_id = created.get("id")
    if not permission_id:
        raise RuntimeError(f"permissions.create returned no id for {file_id}: {created}")
    return str(permission_id)


def share_drive_item_with_user(
    drive: GoogleDriveDataSource,
    file_id: str,
    email: str,
    role: str = "reader",
) -> str:
    """Share with one person, without the notification email; return the permission id."""
    permission_id = _create_permission(
        drive,
        file_id,
        {"type": "user", "role": role, "emailAddress": email},
        sendNotificationEmail=False,
    )
    logger.info("Shared Drive item %s with %s as %s", file_id, email, role)
    return permission_id


def share_drive_item_with_domain(
    drive: GoogleDriveDataSource,
    file_id: str,
    domain: str,
    role: str = "reader",
) -> str:
    """Share with everyone in ``domain``; return the permission id."""
    permission_id = _create_permission(
        drive,
        file_id,
        {"type": "domain", "role": role, "domain": domain},
    )
    logger.info("Shared Drive item %s with domain %s as %s", file_id, domain, role)
    return permission_id


def share_drive_item_with_group(
    drive: GoogleDriveDataSource,
    file_id: str,
    group_email: str,
    role: str = "reader",
) -> str:
    """Share with a Google Group, without the notification email; return the permission id."""
    permission_id = _create_permission(
        drive,
        file_id,
        {"type": "group", "role": role, "emailAddress": group_email},
        sendNotificationEmail=False,
    )
    logger.info("Shared Drive item %s with group %s as %s", file_id, group_email, role)
    return permission_id


async def unshare_drive_item(
    drive: GoogleDriveDataSource,
    file_id: str,
    permission_id: str,
) -> None:
    await drive.permissions_delete(
        fileId=file_id, permissionId=permission_id, supportsAllDrives=True
    )
    logger.info("Removed permission %s from Drive item %s", permission_id, file_id)


async def create_permission_fixtures(
    drive: GoogleDriveDataSource,
    second_user_email: str,
    domain: str,
) -> dict[str, str]:
    """One file per way of sharing, all owned by the impersonated user.

    Layout::

        {root}/
          private.txt          owner only
          shared-reader.txt    + second user as reader
          revoke.txt           + second user as reader (a test takes it away)
          domain.txt           + everyone in the domain as reader

    ``domain_share_error`` is set instead of ``domain_permission_id`` when the
    Workspace sharing policy refuses domain-wide shares, so only that case skips.
    """
    suffix = uuid.uuid4().hex[:8]
    root_name = f"pipeshub-it-drive-perm-{suffix}"
    root_id = await create_drive_folder(drive, root_name)

    # The caller only learns root_id from the return value, so a failure part-way
    # through must clean up here or the tree is left in Drive.
    try:
        fixtures: dict[str, str] = {"root_folder_id": root_id, "root_folder_name": root_name}
        for key, name in (
            ("private", "private.txt"),
            ("shared", "shared-reader.txt"),
            ("revoke", "revoke.txt"),
            ("domain", "domain.txt"),
        ):
            fixtures[f"{key}_file_id"] = await create_drive_text_file(
                drive, name, parent_id=root_id, content=f"pipeshub drive permission it: {name}\n"
            )
            fixtures[f"{key}_file_name"] = name

        fixtures["shared_permission_id"] = share_drive_item_with_user(
            drive, fixtures["shared_file_id"], second_user_email
        )
        fixtures["revoke_permission_id"] = share_drive_item_with_user(
            drive, fixtures["revoke_file_id"], second_user_email
        )
        try:
            fixtures["domain_permission_id"] = share_drive_item_with_domain(
                drive, fixtures["domain_file_id"], domain
            )
        except HttpError as e:
            if e.resp.status != 403:
                raise
            fixtures["domain_share_error"] = f"HTTP {e.resp.status}: {e}"
            logger.warning("Domain-wide share refused for %s: %s", domain, e)
    except BaseException:
        await delete_drive_folder(drive, root_id)
        raise

    logger.info("Created Drive permission fixtures: %s", fixtures)
    return fixtures


async def delete_drive_folder(
    drive: GoogleDriveDataSource,
    folder_id: Optional[str],
) -> None:
    """Permanently delete a Drive folder (and owned descendants). Best-effort."""
    if not folder_id:
        return
    try:
        await drive.files_delete(fileId=folder_id, supportsAllDrives=True)
        logger.info("Deleted Drive fixture folder %s", folder_id)
    except Exception as e:
        logger.warning("Failed to delete Drive fixture folder %s: %s", folder_id, e)


_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))

_RESTART_SYNC_PAUSE_SEC = (5, 8)

_RESYNC_INTERVAL_SEC = 15
_GRAPH_POLL_INTERVAL_SEC = 10

# The longest a sync wait can take on a quiet connector: it spends the whole
# sync_start_timeout failing to see the sync start, then asks for twice the
# settle polls. A shorter timeout makes it raise before it can settle. Read from
# the helper's own defaults so the two cannot drift apart.
_WAIT_DEFAULTS = inspect.signature(wait_for_sync_completion).parameters
_SYNC_WAIT_FLOOR_SEC = (
    _WAIT_DEFAULTS["sync_start_timeout"].default
    + 2 * _WAIT_DEFAULTS["settle_checks"].default * _WAIT_DEFAULTS["settle_interval"].default
)


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Disable then re-enable the connector to trigger a fresh incremental sync."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(_RESTART_SYNC_PAUSE_SEC[0])
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(_RESTART_SYNC_PAUSE_SEC[1])


async def sync_and_wait(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    timeout: float | None = None,
) -> None:
    _restart_sync(pipeshub_client, connector_id)
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=_SYNC_TIMEOUT_SEC if timeout is None else timeout,
    )


def record_present(
    graph_provider: GraphProviderProtocol, connector_id: str, external_id: str
) -> Callable[[], Awaitable[bool]]:
    async def _present() -> bool:
        return (
            await graph_provider.get_record_by_external_id(connector_id, external_id)
            is not None
        )

    return _present


def record_absent(
    graph_provider: GraphProviderProtocol, connector_id: str, external_id: str
) -> Callable[[], Awaitable[bool]]:
    async def _absent() -> bool:
        return (
            await graph_provider.get_record_by_external_id(connector_id, external_id)
            is None
        )

    return _absent


async def sync_until(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    check: Callable[[], Awaitable[bool]],
    *,
    description: str,
) -> None:
    """Sync, and sync again, until ``check`` holds.

    An edit made through the Drive API can take a while to reach the changes
    feed an incremental sync reads. A sync started straight after the edit may
    see nothing and the next one will, so a single sync followed by a wait on
    the graph fails whenever that first sync ran too early.
    """
    # One budget for the whole wait. A round starts only when what is left
    # covers the restart pauses plus the slowest sync wait, and the wait is
    # given what remains after the restart. When no round fits any more, the
    # graph is still polled until the deadline.
    deadline = time.monotonic() + _SYNC_TIMEOUT_SEC
    restart_sec = sum(_RESTART_SYNC_PAUSE_SEC)
    round_sec = restart_sec + _SYNC_WAIT_FLOOR_SEC
    if round_sec > _SYNC_TIMEOUT_SEC:
        raise ValueError(
            f"sync timeout {_SYNC_TIMEOUT_SEC}s is shorter than one sync round "
            f"({round_sec}s); raise GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT"
        )
    last_sync_error: TimeoutError | None = None
    # Set when the pause was shortened to keep a round: re-measuring after the
    # pause would find a few milliseconds short and skip the round it kept.
    round_kept = False
    while True:
        if round_kept or deadline - time.monotonic() >= round_sec:
            round_kept = False
            try:
                await sync_and_wait(
                    pipeshub_client,
                    graph_provider,
                    connector_id,
                    timeout=deadline - time.monotonic() - restart_sec,
                )
            except TimeoutError as exc:
                # The graph decides, not whether the sync wait saw it settle.
                last_sync_error = exc
        if await check():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f" (last sync wait: {last_sync_error})" if last_sync_error else ""
            raise TimeoutError(
                f"Timed out waiting for {description} for connector {connector_id}: "
                f"not seen within {_SYNC_TIMEOUT_SEC}s of re-syncing{detail}"
            )
        if remaining >= round_sec:
            # Shortened if need be, so the pause never costs the last round.
            await asyncio.sleep(min(_RESYNC_INTERVAL_SEC, remaining - round_sec))
            round_kept = True
        else:
            await asyncio.sleep(min(_GRAPH_POLL_INTERVAL_SEC, remaining))


def ensure_pipeshub_user_inactive(users_client: Any, email: str) -> Optional[str]:
    """Soft-delete the Pipeshub user if present so Drive sync skips them.

    Returns the soft-deleted user id, or ``None`` if no active user existed.
    Callers should wait for graph ``isActive is False`` (or absent) before syncing.
    """
    existing = find_pipeshub_user_by_email(users_client, email)
    if existing is None:
        logger.info("Pipeshub user %s already inactive/absent", email)
        return None
    user_id = _user_id_from_payload(existing if isinstance(existing, dict) else {})
    if not user_id:
        raise RuntimeError(f"Could not resolve Pipeshub user id for {email}: {existing}")
    resp = users_client.delete_user(user_id)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to soft-delete Pipeshub user {email} ({user_id}): "
            f"HTTP {resp.status_code}: {resp.text}"
        )
    logger.info("Soft-deleted Pipeshub user %s (%s) for inactive share IT", email, user_id)
    return user_id


async def wait_until_user_has_file_permission(
    drive: GoogleDriveDataSource,
    file_id: str,
    email: str,
    *,
    timeout: float = 60.0,
    interval: float = 2.0,
) -> None:
    """Poll ``permissions.list`` until ``email`` has a direct file grant.

    Shared Drive individual shares can lag after ``permissions.create`` before
    they appear on ``permissions.list`` with ``permissionDetails.permissionType
    == "file"``. The Workspace connector only layers Shared-with-Me for those
    entries, so ITs must wait here before triggering sync.
    """
    from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]

    target = email.strip().lower()

    async def _has_file_grant() -> dict[str, Any] | None:
        listed = await drive.permissions_list(
            fileId=file_id,
            supportsAllDrives=True,
            fields=(
                "permissions(id,emailAddress,type,role,permissionDetails)"
            ),
        )
        permissions = listed.get("permissions") if isinstance(listed, dict) else None
        if not isinstance(permissions, list):
            logger.info(
                "Waiting for file permission %s on %s; permissions.list empty/invalid",
                target,
                file_id,
            )
            return None
        for perm in permissions:
            if not isinstance(perm, dict):
                continue
            if str(perm.get("emailAddress") or "").strip().lower() != target:
                continue
            details = perm.get("permissionDetails") or []
            # Match connector: Shared Drive SWM links only for permissionType == "file".
            if any(
                isinstance(d, dict) and d.get("permissionType") == "file"
                for d in details
            ):
                return perm
        logger.info(
            "Waiting for file permission %s on %s; not yet visible as file grant",
            target,
            file_id,
        )
        return None

    await async_poll_until(
        _has_file_grant,
        timeout=timeout,
        interval=interval,
        description=f"Drive file permission for {target} on {file_id}",
    )
    logger.info("Drive file permission visible for %s on %s", target, file_id)


async def set_drive_folder_limited_access(
    drive: GoogleDriveDataSource,
    folder_id: str,
    *,
    limited: bool = True,
) -> None:
    """Enable/disable limited access on a folder (My Drive / Shared Drive).

    Sets ``inheritedPermissionsDisabled`` via the raw client (generated
    ``files_update`` has no body parameter). Drive supports this for folders
    only — nested files cannot break inheritance this way.
    """
    updated = (
        drive.client.files()
        .update(
            fileId=folder_id,
            body={"inheritedPermissionsDisabled": limited},
            supportsAllDrives=True,
            fields="id,inheritedPermissionsDisabled,mimeType",
        )
        .execute()
    )
    if not isinstance(updated, dict):
        raise RuntimeError(
            f"files.update returned unexpected result for limited access on "
            f"{folder_id!r}: {updated}"
        )
    actual = updated.get("inheritedPermissionsDisabled")
    if bool(actual) != limited:
        raise RuntimeError(
            f"Limited access not applied on {folder_id!r}: "
            f"expected inheritedPermissionsDisabled={limited}, got {actual!r} "
            f"(mimeType={updated.get('mimeType')!r})"
        )
    logger.info(
        "Set inheritedPermissionsDisabled=%s on folder %s",
        limited,
        folder_id,
    )


async def unshare_drive_item_from_user(
    drive: GoogleDriveDataSource,
    file_id: str,
    email: str,
) -> bool:
    """Remove the user permission for ``email`` on ``file_id``.

    Returns True if a matching permission was deleted, False if none was found.
    """
    target = email.strip().lower()
    listed = await drive.permissions_list(
        fileId=file_id,
        supportsAllDrives=True,
        fields="permissions(id,emailAddress,type,role)",
    )
    permissions = listed.get("permissions") if isinstance(listed, dict) else None
    if not isinstance(permissions, list):
        return False
    deleted = False
    for perm in permissions:
        if not isinstance(perm, dict):
            continue
        if str(perm.get("emailAddress") or "").strip().lower() != target:
            continue
        perm_id = perm.get("id")
        if not perm_id:
            continue
        await drive.permissions_delete(
            fileId=file_id,
            permissionId=str(perm_id),
            supportsAllDrives=True,
        )
        deleted = True
    return deleted


# ---------------------------------------------------------------------------
# Admin Directory + entity IT helpers
# ---------------------------------------------------------------------------


def build_admin_directory_client(sa_json: str, admin_email: str) -> Any:
    """Build Admin Directory API client via DWD as ``admin_email``."""
    service_account_info = json.loads(sa_json)
    if not isinstance(service_account_info, dict):
        raise ValueError(f"{ENV_SA_JSON} must be a JSON object")
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=ADMIN_DIRECTORY_SCOPES,
        subject=admin_email,
    )
    return build(
        "admin",
        "directory_v1",
        credentials=credentials,
        cache_discovery=False,
    )


ADMIN_GROUP_MEMBER_SCOPE = "https://www.googleapis.com/auth/admin.directory.group.member"


def build_admin_group_member_client(sa_json: str, admin_email: str) -> Any:
    """Admin Directory client allowed to add/remove group members.

    Needs ``admin.directory.group.member`` in the service account's domain-wide
    delegation; the sync itself only needs the read-only scopes, so a tenant set up
    for the connector alone will not have it. Raises ``RefreshError`` when missing.
    """
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=[ADMIN_GROUP_MEMBER_SCOPE], subject=admin_email
    )
    credentials.refresh(google_auth_requests.Request())
    return build("admin", "directory_v1", credentials=credentials, cache_discovery=False)


async def resolve_my_drive_root_id(drive: GoogleDriveDataSource) -> str:
    """Return the impersonated user's My Drive root folder id."""
    info = await drive.files_get(
        fileId="root",
        supportsAllDrives=True,
        fields="id",
    )
    root_id = info.get("id")
    if not root_id:
        raise RuntimeError(f"files.get('root') returned no id: {info}")
    return str(root_id)


async def drive_files_get_workspace(
    drive: GoogleDriveDataSource,
    file_id: str,
) -> dict[str, Any]:
    """``files.get`` with the same field mask the Workspace connector uses."""
    meta = await drive.files_get(
        fileId=file_id,
        supportsAllDrives=True,
        fields=DRIVE_WORKSPACE_FILE_GET_FIELDS,
    )
    if not isinstance(meta, dict) or not meta.get("id"):
        raise RuntimeError(f"files.get({file_id!r}) failed: {meta}")
    return meta


def pick_entity_sample_files(sample_data_root: Path, n: int = 2) -> list[Path]:
    """Return the first *n* sample files (sorted by relative path) under *sample_data_root*."""
    from sample_data import list_sample_files  # type: ignore[import-not-found]

    entries = sorted(list_sample_files(), key=lambda t: t[0])
    if len(entries) < n:
        raise RuntimeError(
            f"Need at least {n} sample files under {sample_data_root}, found {len(entries)}"
        )
    paths: list[Path] = []
    for rel, _name in entries[:n]:
        path = sample_data_root / rel
        if not path.is_file():
            raise RuntimeError(f"Sample file missing on disk: {path}")
        paths.append(path)
    return paths


async def _child_by_name(
    drive: GoogleDriveDataSource, parent_id: str, *names: str
) -> Optional[dict[str, Any]]:
    """A non-trashed item directly under *parent_id* called any of *names*, if one exists."""
    matches = " or ".join(
        "name = '{}'".format(n.replace("\\", "\\\\").replace("'", "\\'")) for n in names
    )
    resp = await drive.files_list(
        q=f"'{parent_id}' in parents and ({matches}) and trashed = false",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name, mimeType)",
    )
    files = resp.get("files") or []
    return files[0] if files else None


async def _create_upload_once(
    drive: GoogleDriveDataSource,
    parent_id: str,
    file_name: str,
    upload: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run *upload*, retrying when Drive never answers, without making a second file.

    A converting upload now and then gets no response: either Drive created the file
    and the answer was lost, or it never finished. googleapiclient retries the start
    of a resumable upload on a timeout but not the request carrying the content, so
    the timeout reaches us. Before each retry the parent is checked for the file,
    under its own name or the extension-less one Drive gives a converted upload.
    """
    attempt = 1
    while True:
        try:
            return await upload()
        except TimeoutError:
            existing = await _child_by_name(drive, parent_id, file_name, Path(file_name).stem)
            if existing:
                return existing
            if attempt == DRIVE_UPLOAD_ATTEMPTS:
                raise
            logger.warning("Upload of %s timed out; retrying (%d/%d)", file_name, attempt, DRIVE_UPLOAD_ATTEMPTS)
            attempt += 1


async def upload_drive_local_file(
    drive: GoogleDriveDataSource,
    local_path: Path,
    parent_id: str,
    *,
    name: Optional[str] = None,
    convert_to_mime: Optional[str] = None,
    media_mime: Optional[str] = None,
) -> str:
    """Upload a local file into Drive under *parent_id*; return the new file id.

    When *convert_to_mime* is a Google Workspace MIME (Docs/Sheets/Slides), the
    upload converts Office binary to the native Google type via Drive API
    (``file_metadata.mimeType`` = target, media MIME = source Office type).
    """
    file_name = name or local_path.name
    mime_type = media_mime
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(str(local_path))
    if not mime_type:
        mime_type = "application/octet-stream"
    content = local_path.read_bytes()
    file_metadata: dict[str, Any] = {"name": file_name, "parents": [parent_id]}
    if convert_to_mime:
        file_metadata["mimeType"] = convert_to_mime
    created = await _create_upload_once(
        drive,
        parent_id,
        file_name,
        lambda: drive.files_create_with_media(
            file_metadata=file_metadata,
            content=content,
            mime_type=mime_type,
            supportsAllDrives=True,
        ),
    )
    file_id = created.get("id")
    if not file_id:
        raise RuntimeError(
            f"files_create_with_media returned no id for {file_name!r}: {created}"
        )
    result_mime = created.get("mimeType") or convert_to_mime or mime_type
    logger.info(
        "Uploaded sample %s (%d bytes, media=%s → %s) as Drive file %s under %s",
        file_name,
        len(content),
        mime_type,
        result_mime,
        file_id,
        parent_id,
    )
    return str(file_id)


# Blocks-snapshot sample filenames (integration-test repo google-drive-it-files).
DRIVE_BLOCKS_SAMPLE_SPECS: tuple[dict[str, str], ...] = (
    {
        "kind": "newsletter_doc",
        "local_name": "Newsletter.docx",
        "convert_to_mime": MimeTypes.GOOGLE_DOCS.value,
        "media_mime": MimeTypes.DOCX.value,
    },
    {
        "kind": "science_fair_slides",
        "local_name": "Science fair.pptx",
        "convert_to_mime": MimeTypes.GOOGLE_SLIDES.value,
        "media_mime": MimeTypes.PPTX.value,
    },
    {
        "kind": "gantt_chart_sheets",
        "local_name": "Gantt chart.xlsx",
        "convert_to_mime": MimeTypes.GOOGLE_SHEETS.value,
        "media_mime": MimeTypes.XLSX.value,
    },
    {
        "kind": "owasp_pdf",
        "local_name": "OWASP Test PDF.pdf",
        "media_mime": MimeTypes.PDF.value,
    },
    {
        "kind": "contributing_md",
        "local_name": "CONTRIBUTING.md",
        "media_mime": MimeTypes.MARKDOWN.value,
    },
)


async def create_drive_blocks_fixtures(
    drive: GoogleDriveDataSource,
) -> dict[str, Any]:
    """Create ``blocks-root/seed/`` and return folder ids for the blocks IT."""
    suffix = uuid.uuid4().hex[:8]
    root_name = f"phit-blocks-root-{suffix}"
    seed_name = f"phit-blocks-seed-{suffix}"
    root_id = await create_drive_folder(drive, root_name)
    seed_id = await create_drive_folder(drive, seed_name, parent_id=root_id)
    return {
        "root_folder_id": root_id,
        "root_folder_name": root_name,
        "seed_folder_id": seed_id,
        "seed_folder_name": seed_name,
    }


async def upload_drive_blocks_samples(
    drive: GoogleDriveDataSource,
    parent_id: str,
    samples_root: Path,
) -> list[dict[str, str]]:
    """Upload the five google-drive-it-files samples; convert Office → Google natives.

    Returns a list of ``{id, name, kind, local_name, target_mime}`` dicts.
    """
    uploaded: list[dict[str, str]] = []
    for spec in DRIVE_BLOCKS_SAMPLE_SPECS:
        local_name = spec["local_name"]
        kind = spec["kind"]
        local_path = samples_root / local_name
        if not local_path.is_file():
            raise RuntimeError(
                f"Drive blocks sample missing: {local_path} (kind={kind})"
            )
        convert_to = spec.get("convert_to_mime")
        file_id = await upload_drive_local_file(
            drive,
            local_path,
            parent_id,
            name=local_name,
            convert_to_mime=convert_to,
            media_mime=spec.get("media_mime"),
        )
        meta = await drive_files_get_workspace(drive, file_id)
        target_mime = str(meta.get("mimeType") or convert_to or "")
        uploaded.append(
            {
                "id": file_id,
                "name": str(meta.get("name") or local_name),
                "kind": kind,
                "local_name": local_name,
                "target_mime": target_mime,
            }
        )
    return uploaded


async def count_drive_directory_users(admin_client: Any) -> int:
    """Count Directory users with an email (mirrors connector ``_sync_users``)."""
    total = 0
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {
            "customer": "my_customer",
            "projection": "full",
            "orderBy": "email",
            "maxResults": 500,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        result = admin_client.users().list(**kwargs).execute()
        for user in result.get("users") or []:
            email = user.get("primaryEmail") or user.get("email") or ""
            if email:
                total += 1
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return total


async def get_drive_directory_user(
    admin_client: Any,
    email: str,
) -> dict[str, Any]:
    """Return Directory user dict for *email* (raises if missing)."""
    result = admin_client.users().get(userKey=email, projection="full").execute()
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError(f"Admin users.get({email!r}) failed: {result}")
    return result


async def list_group_user_member_emails(
    admin_client: Any,
    group_email: str,
) -> list[str]:
    """USER-type member emails for a group (connector skips GROUP/CUSTOMER)."""
    emails: list[str] = []
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {"groupKey": group_email, "maxResults": 200}
        if page_token:
            kwargs["pageToken"] = page_token
        result = admin_client.members().list(**kwargs).execute()
        for member in result.get("members") or []:
            if member.get("type") != "USER":
                continue
            member_email = member.get("email") or ""
            if member_email:
                emails.append(str(member_email))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return emails


async def count_drive_group_user_members(admin_client: Any, group_email: str) -> int:
    """Count USER members with email for *group_email*."""
    return len(await list_group_user_member_emails(admin_client, group_email))


async def count_drive_directory_groups_with_members(admin_client: Any) -> int:
    """Count Directory groups that have ≥1 USER member (empty groups are skipped)."""
    total = 0
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {
            "customer": "my_customer",
            "orderBy": "email",
            "maxResults": 200,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        result = admin_client.groups().list(**kwargs).execute()
        for group in result.get("groups") or []:
            group_email = group.get("email") or ""
            if not group_email:
                continue
            members = await list_group_user_member_emails(admin_client, group_email)
            if members:
                total += 1
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return total


async def find_reference_directory_group(
    admin_client: Any,
) -> Optional[dict[str, str]]:
    """Return ``{email, name}`` for the first Directory group with ≥1 USER member."""
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {
            "customer": "my_customer",
            "orderBy": "email",
            "maxResults": 200,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        result = admin_client.groups().list(**kwargs).execute()
        for group in result.get("groups") or []:
            group_email = group.get("email") or ""
            if not group_email:
                continue
            members = await list_group_user_member_emails(admin_client, group_email)
            if not members:
                continue
            name = group.get("name") or group_email
            return {"email": str(group_email), "name": str(name)}
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return None
