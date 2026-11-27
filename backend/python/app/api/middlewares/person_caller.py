"""Whether the authenticated principal is a person acting as themself, for a build that runs every agent only as the
identified person asking (``app.edition.AGENTS_RUN_AS_THE_CALLER``).

A person is a user with an org who signed in, or who uses their own personal access token or an OAuth app's
authorization-code token (it acts for the user who granted it). A service token, an application's client-credentials
token (it acts as an identity its app names, marked by ``actsAsServiceAccount``) and a service account are not; neither
is a principal with no user or no org. With the switch off nothing here refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from app.api.middlewares.auth import SERVICE_IDENTITY_CLAIM
from app.api.middlewares.token_policy import is_service_token
from app.core.org_context import org_of

if TYPE_CHECKING:
    from collections.abc import Mapping

NOT_A_PERSON_DETAIL = (
    "This agent answers only a signed-in person. Sign in with your own account, or use a personal access token, "
    "which acts as its user."
)


# How a durable run's actor reached it (``x.pipeshub`` ``via``), where the actor is a person: a session, their own
# personal access token, an OAuth app's user token, or a trigger they set. A service identity is not. The run spec
# does not say which OAuth grant an ``oauth_app`` run came from; admission refuses a client-credentials token when it
# asks the agent authority (run-definition), so ``oauth_app`` here is the user's own grant.
PERSON_VIAS = frozenset({"session", "pat", "oauth_app", "trigger"})


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


async def require_a_person(request: Request) -> None:
    """Route dependency form of ``refuse_unless_a_person``, for the routes that run an agent as the caller. It stays
    ``async`` so it runs in the request's own context, as the other auth dependencies do."""
    refuse_unless_a_person(getattr(request.state, "user", None))
