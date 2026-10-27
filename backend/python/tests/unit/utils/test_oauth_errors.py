"""Unit tests for app.utils.oauth_errors."""
import pytest

from app.utils.oauth_errors import (
    PERMANENT_GRANT_ERROR_CODES,
    is_permanent_grant_rejection,
    oauth_error_code,
)


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
