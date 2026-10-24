"""What a Google failure says about the connector's setup, as opposed to a passing outage.

Only failures someone has to fix are named here: a service account that domain-wide delegation does not
cover, a key Google no longer accepts, a saved key that is not a key file, an admin account that cannot read
the directory, an API turned off for the Cloud project, a token without the scopes it needs, or a personal
sign-in Google no longer accepts.
Anything else (rate limits, 5xx, timeouts, one file refused) returns None: a later run may succeed on its own.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from googleapiclient.errors import HttpError

from app.connectors.sources.google.common.connector_google_exceptions import (
    ServiceAccountKeyError,
    is_missing_scope_error,
)


class AccessProblem(Enum):
    DELEGATION = "delegation"
    SERVICE_ACCOUNT_KEY = "service_account_key"
    # The saved key is not a key file at all, so nothing ever reached Google.
    UNREADABLE_KEY = "unreadable_key"
    ADMIN_ACCOUNT = "admin_account"
    API_DISABLED = "api_disabled"
    MISSING_SCOPE = "missing_scope"
    SIGNED_OUT = "signed_out"
    # The impersonated user cannot sign in (suspended, archived or deleted): not a setup problem.
    USER_UNAVAILABLE = "user_unavailable"


_API_DISABLED_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})
_UNKNOWN_USER = "invalid email or user id"
_BAD_KEY = ("invalid jwt signature", "account not found", "invalid jwt")
_REVOKED = ("expired or revoked", "token has been revoked")


def _chain(error: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    out: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        out.append(current)
        current = current.__cause__ or current.__context__
    return out


def _refresh_error(error: BaseException) -> tuple[str, str] | None:
    """(OAuth error code, description) of a google-auth RefreshError, else None."""
    from google.auth.exceptions import RefreshError

    if not isinstance(error, RefreshError):
        return None
    data = error.args[1] if len(error.args) > 1 and isinstance(error.args[1], dict) else {}
    code = str(data.get("error") or "")
    description = str(data.get("error_description") or "")
    if not code and error.args:
        code, _, description = str(error.args[0]).partition(":")
    return code.strip().lower(), description.strip().lower()


def _reasons(error: HttpError) -> set[str]:
    details = getattr(error, "error_details", None) or []
    return {d.get("reason") for d in details if isinstance(d, dict) and d.get("reason")} if \
        isinstance(details, list) else set()


def classify_access_problem(
    error: BaseException, *, personal: bool, impersonating_admin: bool = True
) -> AccessProblem | None:
    """The setup problem behind `error`, or None when it is not one.

    `impersonating_admin` says whether the failed call was made as the configured admin account, so a user
    Google cannot impersonate is told apart from an admin account that is not usable.
    """
    for link in _chain(error):
        if isinstance(link, ServiceAccountKeyError) and not personal:
            return AccessProblem.UNREADABLE_KEY
        refresh = _refresh_error(link)
        if refresh is not None:
            code, description = refresh
            if personal:
                if code in ("invalid_grant", "invalid_client", "unauthorized_client") or \
                        any(r in description for r in _REVOKED):
                    return AccessProblem.SIGNED_OUT
                if code == "invalid_scope":
                    return AccessProblem.MISSING_SCOPE
                return None
            if code == "unauthorized_client":
                return AccessProblem.DELEGATION
            if code == "invalid_grant" and _UNKNOWN_USER in description:
                return AccessProblem.ADMIN_ACCOUNT if impersonating_admin else AccessProblem.USER_UNAVAILABLE
            if code in ("invalid_grant", "invalid_client") and (
                code == "invalid_client" or any(b in description for b in _BAD_KEY)
            ):
                return AccessProblem.SERVICE_ACCOUNT_KEY
            if code == "invalid_scope":
                return AccessProblem.DELEGATION
            return None
        if isinstance(link, HttpError):
            status = link.resp.status
            reasons = _reasons(link)
            if reasons & _API_DISABLED_REASONS:
                return AccessProblem.API_DISABLED
            if is_missing_scope_error(link):
                return AccessProblem.MISSING_SCOPE if personal else AccessProblem.DELEGATION
            host = (urlparse(getattr(link, "uri", "") or "").hostname or "").lower()
            if status == 403 and host == "admin.googleapis.com" and "forbidden" in reasons:
                return AccessProblem.ADMIN_ACCOUNT
            if status == 401 and personal:
                return AccessProblem.SIGNED_OUT
            return None
        if personal and type(link).__name__ == "GoogleAuthError" and (
            "credentials" in str(link).lower() or "refresh_token" in str(link).lower()
        ):
            return AccessProblem.SIGNED_OUT
    return None
