"""The one definition of "the OAuth provider refused this grant for good"."""
from typing import Any, Optional

from app.config.constants.http_status_code import HttpStatusCode

# RFC 6749 §5.2 and OIDC Core §3.1.2.6 codes a retry can't fix: the grant, the client
# registration or the user's consent is gone, so the user has to sign in again.
# GitHub (bad_refresh_token, incorrect_client_credentials) and Slack (invalid_refresh_token)
# use their own names for the same thing.
PERMANENT_GRANT_ERROR_CODES = frozenset({
    "invalid_grant", "invalid_client", "unauthorized_client",
    "interaction_required", "login_required", "consent_required",
    "bad_refresh_token", "incorrect_client_credentials", "invalid_refresh_token",
})

# Providers that only say it in prose (Atlassian's "refresh_token is invalid").
PERMANENT_REFRESH_TEXT_MARKERS = (
    "invalid_grant",
    "refresh_token is invalid",
    "refresh token is invalid",
    "refresh token has expired",
    "invalid_refresh_token",
    "bad_refresh_token",
)


# The `error` an authorization endpoint redirects back with (RFC 6749 §4.1.2.1, OpenID Connect
# Core §3.1.2.6), as a log error_class. The redirect is unauthenticated input, so nothing here is
# an ERROR class: a query parameter must not be able to write ERROR lines.
_AUTHORIZATION_ERROR_CLASS = {
    "access_denied": "cancelled",  # the user pressed Cancel (or a policy said no)
    "server_error": "upstream_5xx",
    "temporarily_unavailable": "unavailable",
    **dict.fromkeys((
        "invalid_request", "unauthorized_client", "unsupported_response_type", "invalid_scope",
        "interaction_required", "login_required", "account_selection_required", "consent_required",
        "invalid_request_uri", "invalid_request_object", "request_not_supported", "request_uri_not_supported",
        "registration_not_supported",
    ), "bad_request"),
}


def authorization_error_class(code: Optional[str]) -> str:
    """The error_class of an authorization response's `error`; `forbidden` for a code no spec defines."""
    return _AUTHORIZATION_ERROR_CLASS.get(code or "", "forbidden")


def authorization_error_code(code: Optional[str]) -> str:
    """`code` if a spec defines it, else `other`: safe to log, unlike the raw parameter."""
    return code if code in _AUTHORIZATION_ERROR_CLASS else "other"


def oauth_error_code(body: Any) -> Optional[str]:  # noqa: ANN401
    """The RFC 6749 `error` string of a token endpoint's JSON object body, if it sent one."""
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code else None


def is_transient_status(status: Optional[int]) -> bool:
    """An overloaded or rate-limiting endpoint: a retry can succeed, whatever error code it sent."""
    return status is not None and (
        status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value or status == HttpStatusCode.TOO_MANY_REQUESTS.value
    )


def is_permanent_grant_rejection(
    text: str,
    error_code: Optional[str],
    permanent_codes: frozenset[str] = PERMANENT_GRANT_ERROR_CODES,
    *,
    status: Optional[int] = None,
) -> bool:
    """True for a permanent `error_code`, or a text marker even when the code itself is not permanent."""
    # An overloaded or rate-limiting endpoint can answer with any code; only a 4xx (or 200) refusal is final.
    if is_transient_status(status):
        return False
    if error_code and error_code.lower() in permanent_codes:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in PERMANENT_REFRESH_TEXT_MARKERS)
