"""Unit tests for app.utils.oauth_errors."""
import logging

import pytest

from app.utils.error_class import ERROR_CLASSES, level_for
from app.utils.oauth_errors import (
    PERMANENT_GRANT_ERROR_CODES,
    authorization_error_class,
    authorization_error_code,
    is_permanent_grant_rejection,
    is_transient_status,
    oauth_error_code,
)


@pytest.mark.parametrize(("status", "transient"), [
    (None, False), (200, False), (400, False), (428, False), (429, True), (499, False), (500, True), (503, True), (599, True),
])
def test_only_an_overloaded_or_rate_limiting_status_is_transient(status: int | None, transient: bool) -> None:
    assert is_transient_status(status) is transient


class TestIsPermanentGrantRejection:
    @pytest.mark.parametrize("code", sorted(PERMANENT_GRANT_ERROR_CODES))
    def test_every_permanent_code_is_permanent(self, code: str) -> None:
        assert is_permanent_grant_rejection("", code)

    def test_the_code_is_matched_case_insensitively(self) -> None:
        assert is_permanent_grant_rejection("", "Invalid_Client")

    @pytest.mark.parametrize("code", [
        "server_error", "temporarily_unavailable", "access_denied",
        "invalid_request", "invalid_scope", "unsupported_grant_type",
    ])
    def test_an_outage_or_a_refused_request_shape_is_not_permanent(self, code: str) -> None:
        assert not is_permanent_grant_rejection(f'{{"error":"{code}"}}', code)

    def test_a_caller_can_widen_the_permanent_codes(self) -> None:
        assert is_permanent_grant_rejection("", "invalid_scope", PERMANENT_GRANT_ERROR_CODES | {"invalid_scope"})

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_an_overloaded_or_rate_limiting_answer_is_never_permanent(self, status: int) -> None:
        assert not is_permanent_grant_rejection("invalid_grant", "invalid_grant", status=status)

    @pytest.mark.parametrize("status", [200, 400, 401])
    def test_a_refusal_with_a_4xx_or_200_status_stays_permanent(self, status: int) -> None:
        assert is_permanent_grant_rejection("", "invalid_grant", status=status)

    def test_a_marker_in_the_text_is_permanent_without_a_code(self) -> None:
        assert is_permanent_grant_rejection("status 400. Response: The Refresh Token has expired", None)

    def test_plain_text_without_a_marker_is_not_permanent(self) -> None:
        assert not is_permanent_grant_rejection("status 403. Response: forbidden", None)


class TestOAuthErrorCode:
    @pytest.mark.parametrize(("body", "expected"), [
        ({"error": "invalid_client", "error_description": "AADSTS7000222"}, "invalid_client"),
        ({"error_description": "no code"}, None),
        ({"error": ""}, None),
        ({"error": 42}, None),
        (["invalid_client"], None),
        ("invalid_client", None),
        (None, None),
    ])
    def test_returns_only_a_non_empty_error_string_of_an_object(self, body: object, expected: str | None) -> None:
        assert oauth_error_code(body) == expected


# RFC 6749 §4.1.2.1, then OpenID Connect Core §3.1.2.6.
_AUTHORIZATION_CODES = {
    "invalid_request": "bad_request", "unauthorized_client": "bad_request", "access_denied": "cancelled",
    "unsupported_response_type": "bad_request", "invalid_scope": "bad_request", "server_error": "upstream_5xx",
    "temporarily_unavailable": "unavailable",
    "interaction_required": "bad_request", "login_required": "bad_request", "account_selection_required": "bad_request",
    "consent_required": "bad_request", "invalid_request_uri": "bad_request", "invalid_request_object": "bad_request",
    "request_not_supported": "bad_request", "request_uri_not_supported": "bad_request",
    "registration_not_supported": "bad_request",
}


def test_authorization_errors_map_to_a_class_that_is_never_error() -> None:
    for code, cls in _AUTHORIZATION_CODES.items():
        assert (authorization_error_class(code), authorization_error_code(code)) == (cls, code)
    for code in ("SENTINEL-made-up", "", None, "Access_Denied"):
        assert (authorization_error_class(code), authorization_error_code(code)) == ("forbidden", "other")
    # The redirect is unauthenticated input: no value may write an ERROR line.
    for code in [*_AUTHORIZATION_CODES, "x", None]:
        cls = authorization_error_class(code)
        assert cls in ERROR_CLASSES and level_for(cls) < logging.ERROR
    assert level_for(authorization_error_class("access_denied")) == logging.INFO
