"""Placeholder shown in place of a credential that is stored but must not be returned."""

from starlette.requests import Request

REDACTED_PLACEHOLDER = "••••••••"


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
