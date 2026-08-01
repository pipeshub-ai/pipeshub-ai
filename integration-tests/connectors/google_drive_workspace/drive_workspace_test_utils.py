# pyright: ignore-file

"""Helpers for Google Drive Workspace folder-filter integration tests."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Optional

from google.oauth2 import service_account  # type: ignore[import-not-found]
from googleapiclient.discovery import build  # type: ignore[import-not-found]
from pymongo import MongoClient  # type: ignore[import-not-found]

from app.config.constants.arangodb import MimeTypes  # type: ignore[import-not-found]
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from helper.config import MONGO_DB_NAME, MONGO_URI  # type: ignore[import-not-found]

logger = logging.getLogger("drive-workspace-test-utils")

FOLDER_MIME = MimeTypes.GOOGLE_DRIVE_FOLDER.value
# Full drive scope for fixture create/delete under DWD impersonation.
FULL_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

ENV_SA_JSON = "GOOGLE_DRIVE_WORKSPACE_SERVICE_ACCOUNT_JSON"
ENV_ADMIN_EMAIL = "GOOGLE_DRIVE_WORKSPACE_ADMIN_EMAIL"
ENV_TEST_USER = "GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL"


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
