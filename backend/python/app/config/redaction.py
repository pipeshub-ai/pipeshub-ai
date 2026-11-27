"""Placeholder shown in place of a credential that is stored but must not be returned."""

import logging
import os
import time

from starlette.requests import Request

from app.core.org_context import peek
from app.utils.sensitive_names import OAUTH_CLIENT_SECRET_FIELDS  # noqa: F401

REDACTED_PLACEHOLDER = "••••••••"

logger = logging.getLogger(__name__)

_OBSERVATION_WINDOW_SECONDS = 3600.0
_observed: dict[tuple[object, ...], float] = {}


def hide_secrets_by_default() -> bool:
    """Same switch as the Node config routes: HIDE_SECRET_CONFIG=true masks stored secrets."""
    return os.getenv("HIDE_SECRET_CONFIG") == "true"


def reveal_requested(request: Request) -> bool:
    """The admin UI's "show secrets" button refetches a config with ``?reveal=true``.

    OAuth-app tokens are refused: reveal is for an admin looking at their own
    settings page, not for a delegated client.
    """
    user = getattr(request.state, "user", None) or {}
    return request.query_params.getlist("reveal") == ["true"] and user.get("isOAuth") is not True


def can_reveal_secrets(request: Request) -> bool:
    """This edition can only ever hold one org, so there is no other tenant to leak to.

    Callers still have to check that the requester is an admin.
    """
    return reveal_requested(request)


def token_restrictions_enforced() -> bool:
    """Same switch as the Node token restrictions: only the literal ``enforce`` turns them on."""
    return os.getenv("TOKEN_RESTRICTIONS_MODE") == "enforce"


def hide_secrets_for(request: Request | None, *, hidden: bool) -> bool:
    """``hidden`` is the HIDE_SECRET_CONFIG answer. A token caller (a PAT or an OAuth app)
    is masked whatever it says when TOKEN_RESTRICTIONS_MODE=enforce, as on the Node config
    routes; otherwise it keeps today's response and the read is logged."""
    user = getattr(getattr(request, "state", None), "user", None)
    if hidden or not isinstance(user, dict) or user.get("isOAuth") is not True:
        return hidden
    if token_restrictions_enforced():
        return True
    # The route template, never the raw path: that would log ids and grow the map per URL.
    route = getattr(request.scope.get("route"), "path", "unmatched")
    bound = peek()
    org_id = bound.org_id if bound is not None else None
    key = (org_id, request.method, route)
    now = time.monotonic()
    if now - _observed.get(key, -_OBSERVATION_WINDOW_SECONDS) >= _OBSERVATION_WINDOW_SECONDS:
        # Drop keys whose window has ended, so the map holds at most one window of keys.
        for stale in [k for k, seen in _observed.items() if now - seen >= _OBSERVATION_WINDOW_SECONDS]:
            del _observed[stale]
        _observed[key] = now
        logger.warning(
            "securityEvent: token_restriction_observed org=%s route=%s %s reason=token_caller_unmasked",
            org_id, request.method, route,
        )
    return False
