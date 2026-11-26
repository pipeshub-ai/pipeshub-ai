"""The guard every agent-loop entry applies: no verified person, no run.

An agent, or the assistant, answers with what the person asking may read. A
request with no person behind it (a client_credentials app, a service account's
own token, a service-to-service token, a missing user) has nothing to answer
as, and must never run with someone's access instead. Integrations that need to chat use a personal access
token, which acts as its user.

The guard is checked twice:
- at the route: `require_identified_caller` is a dependency of every route that
  reaches the agent loop, so a refused caller gets a plain 403 before any work;
- at the sink: the agent loop's entry points (`run_agent_loop_stream`,
  `run_chat_stream`) call `loop_caller_refusal` first, and stream only the
  refusal unless the route guard verified this same person for this same
  request. A route registered without the guard therefore fails closed at run
  time.

`tests/unit/api/test_agent_loop_entries_refuse_unidentified.py` lists every route
that reaches the agent loop and fails when one does not apply the route guard.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from app.api.middlewares.principal_kind import PERSON_KINDS
from app.api.middlewares.token_policy import is_service_token
from app.utils.request_context import RequestContext
from app.utils.request_context import get_context as get_request_context

if TYPE_CHECKING:
    from collections.abc import Mapping

UNIDENTIFIED_CALLER_DETAIL = (
    "This agent can't answer unidentified users. Sign in to PipesHub with your own "
    "account to use it. Integrations should use a personal access token, which acts "
    "as its user."
)

@dataclass(frozen=True, slots=True)
class _VerifiedCaller:
    user_id: str
    org_id: str
    # The request it was verified for. Each request gets its own trace context object, so
    # a value left behind by an earlier request on the same task never matches a later one.
    request: RequestContext | None


# Set only by the route guard. Each request runs in its own task, which copies its
# context from the server's (where this is unset), so the value is per request.
_verified_caller: ContextVar[_VerifiedCaller | None] = ContextVar(
    "agent_loop_verified_caller", default=None
)


def _refuse() -> HTTPException:
    return HTTPException(status_code=403, detail=UNIDENTIFIED_CALLER_DETAIL)


def _person(user: Mapping[str, Any]) -> tuple[str, str]:
    return str(user.get("userId") or ""), str(user.get("orgId") or "")


def refuse_unidentified_caller(user: Mapping[str, Any] | None) -> None:
    """403 when the authenticated principal is not a verified person.

    `user` is the principal the auth layer built from the token
    (`request.state.user`) or `_get_user_context`'s view of it. The signals are
    the kind of principal (any kind the auth layer set other than a person:
    an app, a service account's own token, a service token) and a missing user
    or org. Nothing is read from the request body.
    """
    user = user or {}
    user_id, org_id = _person(user)
    kind = user.get("principalKind")
    if (
        (kind is not None and kind not in PERSON_KINDS)
        or user.get("isScopedToken")
        or is_service_token(user)
        or user.get("isServiceAccount")
        or not user_id
        or not org_id
    ):
        raise _refuse()


async def require_identified_caller(request: Request) -> None:
    """Route dependency form of `refuse_unidentified_caller`, for every agent-loop route.

    It must stay `async`: FastAPI runs a sync dependency in a worker thread, whose
    context changes never reach the endpoint, and the loop would then refuse every run.
    """
    user = getattr(request.state, "user", None)
    refuse_unidentified_caller(user)
    user_id, org_id = _person(user)
    _verified_caller.set(_VerifiedCaller(user_id, org_id, get_request_context()))


def loop_caller_refusal(user_info: Mapping[str, Any] | None) -> str | None:
    """The agent loop's own check, before it does anything: the public refusal, or None to run.

    Refuses (with the route guard's message) unless `require_identified_caller`
    ran for the current request and verified the person the loop is about to run
    as. Unknown state is a refusal: no guard, another request's guard, or another
    person. The loop streams the refusal as its only frame, since the response
    has usually started by the time it runs.
    """
    verified = _verified_caller.get()
    if (
        verified is None
        or verified.request is not get_request_context()
        or (verified.user_id, verified.org_id) != _person(user_info or {})
    ):
        return UNIDENTIFIED_CALLER_DETAIL
    return None
