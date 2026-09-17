"""Tests for app.utils.url_redaction."""

import pytest

from app.utils.url_redaction import redact_url


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
