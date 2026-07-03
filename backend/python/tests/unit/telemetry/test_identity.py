"""Unit tests for app.telemetry.identity."""

from app.telemetry.identity import domain_from_email


class TestDomainFromEmail:
    def test_extracts_lowercased_domain(self):
        assert domain_from_email("user@Example.COM") == "example.com"

    def test_uses_last_at_sign(self):
        assert domain_from_email('"weird@local"@domain.io') == "domain.io"

    def test_strips_whitespace(self):
        assert domain_from_email("user@acme.io ") == "acme.io"

    def test_none_and_empty_return_unknown(self):
        assert domain_from_email(None) == "unknown"
        assert domain_from_email("") == "unknown"

    def test_no_at_sign_returns_unknown(self):
        assert domain_from_email("not-an-email") == "unknown"

    def test_trailing_at_returns_unknown(self):
        assert domain_from_email("user@") == "unknown"
