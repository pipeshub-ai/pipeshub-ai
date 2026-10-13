"""Placeholder shown in place of a credential that is stored but must not be returned."""

import os

from starlette.requests import Request

REDACTED_PLACEHOLDER = "••••••••"

# OAuth app secret fields, in each spelling the save paths accept.
OAUTH_CLIENT_SECRET_FIELDS = frozenset({"clientSecret", "client_secret", "clientsecret"})


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
