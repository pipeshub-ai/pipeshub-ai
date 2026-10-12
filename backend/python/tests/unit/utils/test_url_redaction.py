"""Tests for app.utils.url_redaction."""

from urllib.parse import unquote

import pytest
from app.utils.url_redaction import redact_sensitive_query_params, redact_url


class TestRedactUrl:
    def test_strips_userinfo_query_and_fragment(self) -> None:
        raw = "https://user:pass@mcp.example.com:8443/v1/mcp?access_token=secret#frag"
        assert redact_url(raw) == "https://mcp.example.com:8443/v1/mcp"

    def test_preserves_clean_url(self) -> None:
        assert redact_url("https://gitlab.com/api/v4/mcp") == "https://gitlab.com/api/v4/mcp"

    def test_ipv6_host_keeps_brackets_drops_userinfo(self) -> None:
        assert redact_url("https://token@[2001:db8::1]/mcp?key=abc") == "https://[2001:db8::1]/mcp"

    def test_signed_url_loses_its_signature(self) -> None:
        raw = "https://bucket.s3.amazonaws.com/pack.zip?X-Amz-Credential=AKIA&X-Amz-Signature=abc123"
        assert redact_url(raw) == "https://bucket.s3.amazonaws.com/pack.zip"

    @pytest.mark.parametrize("raw", ["not a url", "/relative/path?token=x"])
    def test_non_absolute_urls_are_fully_redacted(self, raw: str) -> None:
        assert redact_url(raw) == "<redacted-url>"

    def test_unparseable_url(self) -> None:
        assert redact_url("https://[::1/x?token=x") == "<unparseable-url>"


class TestRedactSensitiveQueryParams:
    def test_signed_url_token_is_redacted(self) -> None:
        assert (
            redact_sensitive_query_params("/api/v1/index/o/drive/record/r?token=a.b.c&x=1")
            == "/api/v1/index/o/drive/record/r?token=[REDACTED]&x=1"
        )

    def test_param_names_match_case_insensitively(self) -> None:
        assert "secret" not in redact_sensitive_query_params("/cb?Code=secret")

    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/index/o/drive/record/r?safe=1#token=a.b.c",
            "/api/v1/index/o/drive/record/r#token=a.b.c",
            "/cb?code=a.b.c",
        ],
    )
    def test_raw_hash_segments_are_redacted(self, url: str) -> None:
        """uvicorn's h11 protocol keeps a literal ``#`` in the logged path."""
        assert "a.b.c" not in redact_sensitive_query_params(url)

    @pytest.mark.parametrize(
        "url", ["/api/v1/records", "/search?q=token", "/docs#section", "/a?b=1#c", ""]
    )
    def test_urls_without_sensitive_params_are_unchanged(self, url: str) -> None:
        assert redact_sensitive_query_params(url) == url

    def test_connector_callback_code_and_state_are_redacted(self) -> None:
        out = redact_sensitive_query_params(
            "/api/v1/connectors/oauth/callback?code=4/0FAKE-code&state=ZmFrZS1zdGF0ZQ&scope=drive"
        )
        assert "FAKE-code" not in out
        assert "ZmFrZS1zdGF0ZQ" not in out
        assert "scope=drive" in out

    @pytest.mark.parametrize(
        "key",
        ["key", "apiKey", "X-Api-Key", "client-secret", "sessionToken", "user_password"],
    )
    def test_credential_like_names_are_redacted(self, key: str) -> None:
        assert "hidden-value" not in redact_sensitive_query_params(f"/x?{key}=hidden-value&page=2")

    @pytest.mark.parametrize("key", ["keyword", "monkey", "passage", "statement", "codec"])
    def test_names_that_only_contain_those_letters_are_kept(self, key: str) -> None:
        url = f"/x?{key}=visible-value"
        assert redact_sensitive_query_params(url) == url

    def test_percent_encoded_equals_in_the_name_is_redacted(self) -> None:
        assert "encoded-secret" not in redact_sensitive_query_params("/cb?page=1&code%3Dencoded-secret")

    @pytest.mark.parametrize(
        "url",
        [
            "/cb?code%253DSECRET&page=1",
            "/cb?token%253Dya29.SECRET",
            "/cb?page%3D1%26code%3DSECRET",
            "/cb?foo%26code%3DSECRET",
            "/cb?scope%3Dopenid%26code%3DSECRET%26state%3DSECRET",
            "/login?returnTo%3D%2Fcb%3Fcode%3DSECRET",
        ],
    )
    def test_secrets_hidden_in_an_encoded_name_are_redacted(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "SECRET" not in out
        assert "SECRET" not in unquote(unquote(out))

    def test_an_encoded_sensitive_name_is_logged_trimmed(self) -> None:
        assert redact_sensitive_query_params("/cb?code%253DSECRET&page=1") == "/cb?code=[REDACTED]&page=1"

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:hunter2@host.example/x?page=2",
            "https://user:hunter2@host.example/x",
            "mongodb://root:hunter2@mongo1:27017,mongo2:27017/db?replicaSet=rs0",
            "https%3A%2F%2Falice%3Ahunter2%40db.example.com",
            "/debug?db=postgres%3A%2F%2Fapp%3Ahunter2%40pg%2Fdb",
        ],
    )
    def test_userinfo_passwords_are_redacted(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "hunter2" not in out
        assert "hunter2" not in unquote(out)

    def test_userinfo_keeps_user_and_host(self) -> None:
        assert redact_sensitive_query_params("https://user:hunter2@host.example/x?page=2") == (
            "https://user:REDACTED@host.example/x?page=2"
        )

    def test_userinfo_without_a_password_is_unchanged(self) -> None:
        url = "ssh://git@github.com/org/repo"
        assert redact_sensitive_query_params(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "/login?returnTo=%2Fcb%3Fcode%3DSECRET",
            "/login?returnTo=%2Fcb%3Fpage%3D1%26code%3DSECRET&x=1",
            "/login?next=https%253A%252F%252Fapp.example.com%252Fcb%253Fcode%253DSECRET",
            "/login?returnTo=https%3A%2F%2Fapp.example.com%2F%23access_token%3DSECRET",
        ],
    )
    def test_codes_in_nested_url_parameters_are_redacted(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "SECRET" not in out
        assert "returnTo=" in out or "next=" in out

    def test_nested_url_without_secrets_is_unchanged(self) -> None:
        url = "/login?returnTo=%2Fdocs%3Fpage%3D2"
        assert redact_sensitive_query_params(url) == url

    def test_nesting_depth_is_bounded(self) -> None:
        value = "code=SECRET"
        for _ in range(10):
            value = f"/x?next={value.replace('%', '%25').replace('=', '%3D').replace('?', '%3F').replace('&', '%26')}"
        # Must terminate; the innermost layers are past the bound and stay opaque.
        assert isinstance(redact_sensitive_query_params(value), str)
