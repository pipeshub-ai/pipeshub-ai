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


def oauth_error_code(body: Any) -> Optional[str]:  # noqa: ANN401
    """The RFC 6749 `error` string of a token endpoint's JSON object body, if it sent one."""
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code else None


def is_permanent_grant_rejection(
    text: str,
    error_code: Optional[str],
    permanent_codes: frozenset[str] = PERMANENT_GRANT_ERROR_CODES,
    *,
    status: Optional[int] = None,
) -> bool:
    """True for a permanent `error_code`, or a text marker even when the code itself is not permanent."""
    # An overloaded or rate-limiting endpoint can answer with any code; only a 4xx (or 200) refusal is final.
    if status is not None and (
        status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value or status == HttpStatusCode.TOO_MANY_REQUESTS.value
    ):
        return False
    if error_code and error_code.lower() in permanent_codes:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in PERMANENT_REFRESH_TEXT_MARKERS)
