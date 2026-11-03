"""PipesHub org users for connector suites whose source users must also exist in PipesHub."""

from __future__ import annotations

import logging
import os
from typing import Any

from helper.clients.config_client import ConfigClient  # type: ignore[import-not-found]

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


def _configure_smtp_from_env(users_client: Any) -> bool:
    host, port = os.getenv("SMTP_HOST"), os.getenv("SMTP_PORT")
    if not host or not port:
        return False
    username = os.getenv("SMTP_USERNAME", "")
    resp = ConfigClient(users_client._client).create_smtp_config(
        host=host,
        port=int(port),
        username=username,
        password=os.getenv("SMTP_PASSWORD", ""),
        fromEmail=os.getenv("SMTP_FROM_EMAIL") or username or "no-reply@example.com",
    )
    return resp.status_code in (200, 201)


def _restore_soft_deleted_pipeshub_user(users_client: Any, email: str) -> None:
    """Restore a soft-deleted user the way an admin does: invite the address again.

    Soft-delete keeps the unique email index, so create_user conflicts while
    getAllUsers cannot see the row. Only a restore sends ``userAdded``, the event
    that sets the graph user active again; ``userUpdated`` leaves ``isActive``
    alone since #4634, so undeleting the Mongo row directly left it inactive.
    """
    resp = users_client.invite_bulk([email])
    # The invite route refuses with 404 until SMTP settings are saved.
    if resp.status_code == 404 and _configure_smtp_from_env(users_client):
        resp = users_client.invite_bulk([email])
    # The account is restored and its event sent before the invite mail goes out,
    # so only a user still missing afterwards is a failed restore.
    if find_pipeshub_user_by_email(users_client, email) is None:
        raise RuntimeError(
            f"Failed to restore Pipeshub user {email}: HTTP {resp.status_code}: {resp.text}"
        )
    logger.info("Restored soft-deleted Pipeshub user %s through the invite route", email)


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
            _restore_soft_deleted_pipeshub_user(users_client, email)
            existing = find_pipeshub_user_by_email(users_client, email)
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

    # A restored account comes back with hasLoggedIn=false; mark it logged in.
    if need_graph_reactivation or not existing.get("hasLoggedIn"):
        upd = users_client.update_user(user_id, hasLoggedIn=True)
        if upd.status_code >= 400:
            raise RuntimeError(
                f"Failed to activate Pipeshub user {email} ({user_id}): "
                f"HTTP {upd.status_code}: {upd.text}"
            )
        logger.info("Marked Pipeshub user %s active (hasLoggedIn=true)", email)

    return user_id, created
