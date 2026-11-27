"""Whether the authenticated principal is a person acting as themself, for a build that runs every agent only as the
identified person asking (``app.edition.AGENTS_RUN_AS_THE_CALLER``).

A service token, an application's client-credentials token (it acts as an identity its app names, marked by
``actsAsServiceAccount``) and a service account are not a person; neither is a principal with no user or no org.
With the switch off nothing here refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.api.middlewares.auth import SERVICE_IDENTITY_CLAIM
from app.api.middlewares.token_policy import is_service_token
from app.core.org_context import org_of

if TYPE_CHECKING:
    from collections.abc import Mapping

NOT_A_PERSON_DETAIL = (
    "This agent answers only a signed-in person. Sign in with your own account, or use a personal access token, "
    "which acts as its user."
)


def acts_as_a_person(user: Mapping[str, Any] | None) -> bool:
    principal = user or {}
    return bool(
        principal.get("userId")
        and org_of(principal)
        and not is_service_token(principal)
        and SERVICE_IDENTITY_CLAIM not in principal
        and not principal.get("isServiceAccount")
    )


def refuse_unless_a_person(user: Mapping[str, Any] | None) -> None:
    """403 where the edition runs agents only as the caller and ``user`` is not a person acting as themself."""
    from app import edition

    if edition.AGENTS_RUN_AS_THE_CALLER and not acts_as_a_person(user):
        raise HTTPException(status_code=403, detail=NOT_A_PERSON_DETAIL)
