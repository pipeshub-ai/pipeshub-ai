"""Helpers the route and event boundaries share.

Kept apart from ``runtime`` so the decision "who is this request" is one
function with its own tests, and every route answers it the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.model_identity.principal import (
    Principal,
    service_account_principal,
    service_principal,
    user_principal,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

UNATTRIBUTED_SERVICE = "unattributed"


def principal_from_request_user(
    user: Mapping[str, Any] | None,
    *,
    agent_id: str | None = None,
) -> Principal:
    """The principal for an authenticated request, from ``request.state.user``.

    * A person (a session, a PAT, an OAuth token for a user, or a Slack caller
      mapped to a user): that user.
    * A token minted for a service account: the service account, identified by
      ``agent_id`` when the route knows it and by the account's own id otherwise.
      It has no groups; budgets for it belong to the agent, not to a person.
    * Nothing usable: a service principal. Strict governance can refuse these.
    """
    user = user or {}
    org_id = str(user.get("orgId") or "").strip()
    user_id = str(user.get("userId") or "").strip()
    if user.get("isServiceAccount"):
        subject = (agent_id or user_id).strip() if (agent_id or user_id) else ""
        if subject and org_id:
            return service_account_principal(subject, org_id)
        return service_principal(UNATTRIBUTED_SERVICE, org_id)
    if user_id and org_id:
        return user_principal(user, org_id)
    return service_principal(UNATTRIBUTED_SERVICE, org_id)


def config_service_of(container: Any) -> Any:  # noqa: ANN401 - an untyped dependency-injection object
    """The container's config service, or ``None``: identity is best effort and must not fail a request."""
    try:
        return container.config_service()
    except Exception:
        return None
