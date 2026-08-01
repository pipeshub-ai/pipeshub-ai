# pyright: ignore-file

"""Helpers for Google Drive Workspace folder-filter + entity integration tests."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from google.oauth2 import service_account  # type: ignore[import-not-found]
from googleapiclient.discovery import build  # type: ignore[import-not-found]
from pymongo import MongoClient  # type: ignore[import-not-found]

from app.config.constants.arangodb import MimeTypes  # type: ignore[import-not-found]
from app.connectors.sources.google.common.drive_file_fields import (  # type: ignore[import-not-found]
    DRIVE_WORKSPACE_FILE_GET_FIELDS,
)
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from helper.config import MONGO_DB_NAME, MONGO_URI  # type: ignore[import-not-found]

logger = logging.getLogger("drive-workspace-test-utils")

FOLDER_MIME = MimeTypes.GOOGLE_DRIVE_FOLDER.value
# Full drive scope for fixture create/delete under DWD impersonation.
FULL_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
ADMIN_DIRECTORY_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
]

ENV_SA_JSON = "GOOGLE_DRIVE_WORKSPACE_SERVICE_ACCOUNT_JSON"
ENV_ADMIN_EMAIL = "GOOGLE_DRIVE_WORKSPACE_ADMIN_EMAIL"
ENV_TEST_USER = "GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL"
ENV_SECOND_USER = "GOOGLE_DRIVE_WORKSPACE_SECOND_USER_EMAIL"


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
    timeout: float = 60.0,
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
        Drive B/
          ignored.txt
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
    }
    logger.info("Created Shared Drive folder-filter fixtures: %s", fixtures)
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


def _users_from_list_response(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [u for u in payload if isinstance(u, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("users", "data", "items"):
        items = payload.get(key)
        if isinstance(items, list):
            return [u for u in items if isinstance(u, dict)]
    return []


def _user_id_from_payload(payload: dict[str, Any]) -> str:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    if not isinstance(user, dict):
        return ""
    return str(user.get("_id") or user.get("userId") or user.get("id") or "")


def find_pipeshub_user_by_email(users_client: Any, email: str) -> Optional[dict[str, Any]]:
    """Return the active (non-soft-deleted) Mongo user doc for email, else None."""
    target = email.strip().lower()
    # Escape so '.' in emails is matched literally by the API's $regex search.
    search = re.escape(email.strip())
    resp = users_client.get_all_users(search=search, limit=100)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to list users while looking up {email}: "
            f"HTTP {resp.status_code}: {resp.text}"
        )
    for user in _users_from_list_response(resp.json()):
        if str(user.get("email") or "").strip().lower() == target:
            return user
    return None


def _restore_soft_deleted_pipeshub_user(email: str) -> Optional[dict[str, Any]]:
    """Undelete a soft-deleted users doc (and re-add to the everyone group).

    Soft-delete leaves the unique email index in place, so create_user fails with
    E11000 while getAllUsers (isDeleted != true) cannot see the row. Mirror the
    bulk-invite restore path via direct Mongo, same as other IT helpers.
    """
    target = email.strip().lower()
    # Backend may use MONGO_DB_NAME=es or enterprise-search depending on deploy.
    db_candidates: list[str] = []
    for name in (MONGO_DB_NAME, "es", "enterprise-search"):
        if name and name not in db_candidates:
            db_candidates.append(name)

    client = MongoClient(MONGO_URI)
    try:
        for db_name in db_candidates:
            db = client[db_name]
            users = db["users"]
            doc = users.find_one(
                {"email": {"$regex": f"^{re.escape(target)}$", "$options": "i"}}
            )
            if not doc:
                continue
            if not doc.get("isDeleted"):
                return doc

            users.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {"isDeleted": False, "hasLoggedIn": True},
                    "$unset": {"deletedBy": ""},
                },
            )
            org_id = doc.get("orgId")
            if org_id is not None:
                db["userGroups"].update_one(
                    {"orgId": org_id, "type": "everyone", "isDeleted": {"$ne": True}},
                    {"$addToSet": {"users": doc["_id"]}},
                )
            doc["isDeleted"] = False
            doc["hasLoggedIn"] = True
            logger.info(
                "Restored soft-deleted Pipeshub user %s (id=%s) in db=%s",
                email,
                doc["_id"],
                db_name,
            )
            return doc
        return None
    finally:
        client.close()


def ensure_pipeshub_user_exists(users_client: Any, email: str) -> tuple[str, bool]:
    """Ensure a Pipeshub org user exists for email; create if missing.

    Also marks ``hasLoggedIn=True`` so the user is treated as active in the product UI.
    Graph ``isActive`` is set by the ``userAdded`` / ``userUpdated`` entity events.

    Returns:
        ``(user_id, created)`` — ``created`` is True when this call issued create_user.
    """
    existing = find_pipeshub_user_by_email(users_client, email)
    created = False
    need_graph_reactivation = False
    if existing is None:
        local = email.split("@", 1)[0] or "drive-it"
        resp = users_client.create_user(
            email=email,
            full_name=f"Drive IT {local}",
        )
        if resp.status_code in (200, 201):
            created = True
            existing = resp.json() if isinstance(resp.json(), dict) else {}
            logger.info("Created Pipeshub user %s", email)
        else:
            # Soft-deleted leftover (unique email index) or race — restore then re-lookup.
            restored = _restore_soft_deleted_pipeshub_user(email)
            existing = find_pipeshub_user_by_email(users_client, email)
            if existing is None and restored is not None:
                existing = {
                    "_id": str(restored["_id"]),
                    "id": str(restored["_id"]),
                    "email": restored.get("email") or email,
                    "hasLoggedIn": bool(restored.get("hasLoggedIn")),
                }
            if existing is None:
                raise RuntimeError(
                    f"Failed to create Pipeshub user {email}: "
                    f"HTTP {resp.status_code}: {resp.text}"
                )
            need_graph_reactivation = True
            logger.info("Reused existing/restored Pipeshub user %s after create conflict", email)

    user_id = _user_id_from_payload(existing if isinstance(existing, dict) else {})
    if not user_id:
        raise RuntimeError(f"Could not resolve Pipeshub user id for {email}: {existing}")

    # Always fire update when restoring so entity-events set graph isActive=true again
    # (userDeleted previously set isActive=false). Otherwise only when not yet logged in.
    if need_graph_reactivation or not existing.get("hasLoggedIn"):
        upd = users_client.update_user(user_id, hasLoggedIn=True)
        if upd.status_code >= 400:
            raise RuntimeError(
                f"Failed to activate Pipeshub user {email} ({user_id}): "
                f"HTTP {upd.status_code}: {upd.text}"
            )
        logger.info("Marked Pipeshub user %s active (hasLoggedIn=true)", email)

    return user_id, created


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


async def share_drive_item_with_user(
    drive: GoogleDriveDataSource,
    file_id: str,
    email: str,
    *,
    role: str = "reader",
) -> str:
    """Share a Drive item with ``email``; return the new permission id.

    Uses the underlying Google client because generated ``permissions_create``
    does not accept a request body.
    """
    created = (
        drive.client.permissions()
        .create(
            fileId=file_id,
            body={
                "type": "user",
                "role": role,
                "emailAddress": email,
            },
            sendNotificationEmail=False,
            supportsAllDrives=True,
            fields="id",
        )
        .execute()
    )
    permission_id = created.get("id") if isinstance(created, dict) else None
    if not permission_id:
        raise RuntimeError(
            f"permissions.create returned no id for {file_id!r} → {email!r}: {created}"
        )
    return str(permission_id)


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
    created = await drive.files_create_with_media(
        file_metadata=file_metadata,
        content=content,
        mime_type=mime_type,
        supportsAllDrives=True,
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


async def _list_group_user_member_emails(
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
    return len(await _list_group_user_member_emails(admin_client, group_email))


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
            members = await _list_group_user_member_emails(admin_client, group_email)
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
            members = await _list_group_user_member_emails(admin_client, group_email)
            if not members:
                continue
            name = group.get("name") or group_email
            return {"email": str(group_email), "name": str(name)}
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return None
