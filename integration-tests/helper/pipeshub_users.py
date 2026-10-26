"""PipesHub org users for connector suites whose source users must also exist in PipesHub."""

from __future__ import annotations

import logging
import re
from typing import Any

from helper.config import MONGO_DB_NAME, MONGO_URI  # type: ignore[import-not-found]
from pymongo import MongoClient  # type: ignore[import-not-found]

logger = logging.getLogger("pipeshub-users")


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


def find_pipeshub_user_by_email(users_client: Any, email: str) -> dict[str, Any] | None:
    """Return the active (non-soft-deleted) Mongo user doc for email, else None."""
    target = email.strip().lower()
    # Raw: the API escapes the term itself (#3804). Escaping here too searched for a
    # literal backslash, so no address with a '.' was ever found.
    resp = users_client.get_all_users(search=email.strip(), limit=100)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to list users while looking up {email}: "
            f"HTTP {resp.status_code}: {resp.text}"
        )
    for user in _users_from_list_response(resp.json()):
        if str(user.get("email") or "").strip().lower() == target:
            return user
    return None


def _restore_soft_deleted_pipeshub_user(email: str) -> dict[str, Any] | None:
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
