"""Who a model call is made for.

A ``Principal`` is immutable and says nothing about what the call is for (see
``workload``) or which request it belongs to (see ``call_context``). Keeping the
three apart lets the same user be a principal for chat, search and an
embedding lookup without three copies of the identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

SYSTEM_SERVICE_NAME = "system"


class PrincipalKind(str, Enum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    org_id: str
    subject_id: str
    idp_object_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    service_name: str | None = None

    @property
    def is_user(self) -> bool:
        return self.kind is PrincipalKind.USER


def _clean(value: Any) -> str | None:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def user_principal(
    user: Mapping[str, Any],
    org_id: str | None = None,
    *,
    idp_object_id: str | None = None,
) -> Principal:
    """Principal for a signed-in person, from ``request.state.user``.

    The ids come from the verified session or service token, never from a
    request body.
    """
    user_id = _clean(user.get("userId") or user.get("user_id"))
    resolved_org = _clean(org_id or user.get("orgId") or user.get("org_id"))
    if user_id is None or resolved_org is None:
        raise ValueError("a user principal needs both a user id and an org id")
    return Principal(
        kind=PrincipalKind.USER,
        org_id=resolved_org,
        subject_id=user_id,
        idp_object_id=_clean(idp_object_id),
        email=_clean(user.get("email")),
        display_name=_clean(user.get("fullName") or user.get("full_name")),
    )


def service_account_principal(
    agent_id: str, org_id: str, *, name: str | None = None
) -> Principal:
    """Principal for an agent that runs as its own service account."""
    agent = _clean(agent_id)
    org = _clean(org_id)
    if agent is None or org is None:
        raise ValueError("a service-account principal needs an agent id and an org id")
    return Principal(
        kind=PrincipalKind.SERVICE_ACCOUNT,
        org_id=org,
        subject_id=agent,
        service_name=_clean(name),
    )


def service_principal(name: str = SYSTEM_SERVICE_NAME, org_id: str = "") -> Principal:
    """Principal for PipesHub itself (indexing, health checks, warmup)."""
    service = _clean(name) or SYSTEM_SERVICE_NAME
    return Principal(
        kind=PrincipalKind.SERVICE,
        org_id=_clean(org_id) or "",
        subject_id=service,
        service_name=service,
    )
