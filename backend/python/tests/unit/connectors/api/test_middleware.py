"""Tests for app.connectors.api.middleware — WebhookAuthVerifier."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.api.middleware import WebhookAuthVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(headers: dict[str, str] | None = None, client_ip: str = "1.2.3.4"):
    """Build a minimal fake Request with configurable headers and client IP."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_ip
    _headers = headers or {}
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: _headers.get(key, default)
    return request


def _make_verifier():
    """Build a WebhookAuthVerifier with a mock logger."""
    logger = MagicMock()
    return WebhookAuthVerifier(logger), logger


# ---------------------------------------------------------------------------
# _is_google_ip
# ---------------------------------------------------------------------------


class TestIsGoogleIp:
    """Tests for WebhookAuthVerifier._is_google_ip."""

    def test_ip_in_first_range(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("64.233.160.1") is True

    def test_ip_in_last_range(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("216.239.32.5") is True

    def test_ip_in_74_125_range(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("74.125.0.1") is True

    def test_ip_outside_all_ranges(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("8.8.8.8") is False

    def test_ip_outside_ranges_private(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("192.168.1.1") is False

    def test_invalid_ip_string(self):
        verifier, logger = _make_verifier()
        result = verifier._is_google_ip("not-an-ip")
        assert result is False
        logger.error.assert_called_once()

    def test_empty_ip_string(self):
        verifier, logger = _make_verifier()
        result = verifier._is_google_ip("")
        assert result is False
        logger.error.assert_called_once()

    def test_none_ip(self):
        verifier, logger = _make_verifier()
        result = verifier._is_google_ip(None)
        assert result is False
        logger.error.assert_called_once()

    def test_ipv6_address(self):
        verifier, _ = _make_verifier()
        result = verifier._is_google_ip("::1")
        assert result is False

    def test_network_start_boundary(self):
        """First IP in a range should be considered Google."""
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("66.102.0.0") is True

    def test_network_end_boundary(self):
        """Last IP in 66.102.0.0/20 = 66.102.15.255."""
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("66.102.15.255") is True

    def test_one_past_network_end(self):
        """66.102.16.0 is outside 66.102.0.0/20."""
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("66.102.16.0") is False

    def test_all_ranges_have_match(self):
        """At least one IP from each listed range should be recognised."""
        verifier, _ = _make_verifier()
        sample_ips = [
            "64.233.160.10",
            "66.102.0.10",
            "66.249.80.10",
            "72.14.192.10",
            "74.125.0.10",
            "108.177.8.10",
            "173.194.0.10",
            "209.85.128.10",
            "216.58.192.10",
            "216.239.32.10",
        ]
        for ip in sample_ips:
            assert verifier._is_google_ip(ip) is True, f"{ip} should be google"


# ---------------------------------------------------------------------------
# _verify_signature
# ---------------------------------------------------------------------------


class TestVerifySignature:
    """Tests for WebhookAuthVerifier._verify_signature."""

    async def test_valid_headers_mixed_case(self):
        verifier, _ = _make_verifier()
        request = _make_request({
            "X-Goog-Channel-ID": "channel-123",
            "X-Goog-Resource-ID": "resource-456",
        })
        assert await verifier._verify_signature(request) is True

    async def test_valid_headers_lowercase(self):
        verifier, _ = _make_verifier()
        request = _make_request({
            "x-goog-channel-id": "ch",
            "x-goog-resource-id": "res",
        })
        assert await verifier._verify_signature(request) is True

    async def test_valid_headers_uppercase(self):
        verifier, _ = _make_verifier()
        request = _make_request({
            "X-GOOG-CHANNEL-ID": "ch",
            "X-GOOG-RESOURCE-ID": "res",
        })
        assert await verifier._verify_signature(request) is True

    async def test_missing_channel_id(self):
        verifier, logger = _make_verifier()
        request = _make_request({
            "X-Goog-Resource-ID": "resource-456",
        })
        result = await verifier._verify_signature(request)
        assert result is False
        logger.warning.assert_called_once()

    async def test_missing_resource_id(self):
        verifier, logger = _make_verifier()
        request = _make_request({
            "X-Goog-Channel-ID": "channel-123",
        })
        result = await verifier._verify_signature(request)
        assert result is False
        logger.warning.assert_called_once()

    async def test_both_headers_missing(self):
        verifier, logger = _make_verifier()
        request = _make_request({})
        result = await verifier._verify_signature(request)
        assert result is False
        logger.warning.assert_called_once()

    async def test_empty_channel_id_value(self):
        """Empty string header values are falsy."""
        verifier, _ = _make_verifier()
        request = _make_request({
            "X-Goog-Channel-ID": "",
            "X-Goog-Resource-ID": "resource-456",
        })
        result = await verifier._verify_signature(request)
        assert result is False

    async def test_empty_resource_id_value(self):
        verifier, _ = _make_verifier()
        request = _make_request({
            "X-Goog-Channel-ID": "channel-123",
            "X-Goog-Resource-ID": "",
        })
        result = await verifier._verify_signature(request)
        assert result is False

    async def test_exception_in_header_access(self):
        """Simulate an unexpected exception during header access."""
        verifier, logger = _make_verifier()
        request = _make_request()
        # Force headers.get to raise a ValueError
        request.headers.get = MagicMock(side_effect=ValueError("boom"))
        result = await verifier._verify_signature(request)
        assert result is False
        logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# verify_request (integration of _is_google_ip + _verify_signature)
# ---------------------------------------------------------------------------


class TestVerifyRequest:
    """Tests for WebhookAuthVerifier.verify_request."""

    async def test_authenticated_with_valid_headers(self):
        verifier, logger = _make_verifier()
        request = _make_request(
            headers={
                "X-Goog-Channel-ID": "channel-1",
                "X-Goog-Resource-ID": "resource-1",
            },
            client_ip="8.8.8.8",
        )
        result = await verifier.verify_request(request)
        assert result is True
        logger.info.assert_called_once()

    async def test_rejected_when_signature_invalid(self):
        verifier, logger = _make_verifier()
        request = _make_request(headers={}, client_ip="74.125.1.1")
        result = await verifier.verify_request(request)
        assert result is False
        # Two warnings: one from _verify_signature and one from verify_request
        assert logger.warning.call_count == 2

    async def test_client_ip_logged_on_success(self):
        verifier, logger = _make_verifier()
        request = _make_request(
            headers={
                "X-Goog-Channel-ID": "ch",
                "X-Goog-Resource-ID": "res",
            },
            client_ip="10.0.0.1",
        )
        await verifier.verify_request(request)
        log_msg = logger.info.call_args[0][0]
        assert "authenticated" in log_msg.lower() or "10.0.0.1" in str(logger.info.call_args)

    async def test_client_ip_logged_on_failure(self):
        verifier, logger = _make_verifier()
        request = _make_request(headers={}, client_ip="192.168.0.5")
        await verifier.verify_request(request)
        # warning about invalid signature should reference the IP
        assert logger.warning.called


# ---------------------------------------------------------------------------
# __init__ / google_ips sanity
# ---------------------------------------------------------------------------


class TestWebhookAuthVerifierInit:
    """Tests for constructor and google_ips list."""

    def test_google_ips_populated(self):
        verifier, _ = _make_verifier()
        assert len(verifier.google_ips) == 10

    def test_google_ips_are_valid_cidrs(self):
        from ipaddress import ip_network
        verifier, _ = _make_verifier()
        for cidr in verifier.google_ips:
            ip_network(cidr)  # will raise if invalid

    def test_logger_stored(self):
        logger = MagicMock()
        verifier = WebhookAuthVerifier(logger)
        assert verifier.logger is logger

# =============================================================================
# Merged from test_middleware_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request_cov(headers=None, client_ip="1.2.3.4"):
    """Build a minimal fake Request."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_ip
    _headers = headers or {}
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: _headers.get(key, default)
    return request


def _make_verifier():
    """Build a WebhookAuthVerifier with a mock logger."""
    logger = MagicMock()
    return WebhookAuthVerifier(logger), logger


# ===================================================================
# _verify_signature - additional header combinations
# ===================================================================

class TestVerifySignatureAdditional:

    @pytest.mark.asyncio
    async def test_channel_from_mixed_case_resource_from_lowercase(self):
        verifier, _ = _make_verifier()
        request = _make_request_cov({
            "X-Goog-Channel-ID": "channel-1",
            "x-goog-resource-id": "resource-1",
        })
        result = await verifier._verify_signature(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_channel_from_uppercase_resource_from_mixed(self):
        verifier, _ = _make_verifier()
        request = _make_request_cov({
            "X-GOOG-CHANNEL-ID": "channel-1",
            "X-Goog-Resource-ID": "resource-1",
        })
        result = await verifier._verify_signature(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_all_header_variants_present(self):
        """When all case variants are present, first match wins."""
        verifier, _ = _make_verifier()
        request = _make_request_cov({
            "X-Goog-Channel-ID": "mixed-ch",
            "x-goog-channel-id": "lower-ch",
            "X-GOOG-CHANNEL-ID": "upper-ch",
            "X-Goog-Resource-ID": "mixed-res",
            "x-goog-resource-id": "lower-res",
            "X-GOOG-RESOURCE-ID": "upper-res",
        })
        result = await verifier._verify_signature(request)
        assert result is True


# ===================================================================
# verify_request - integration
# ===================================================================

class TestVerifyRequestIntegration:

    @pytest.mark.asyncio
    async def test_success_with_google_ip(self):
        verifier, logger = _make_verifier()
        request = _make_request_cov(
            headers={
                "X-Goog-Channel-ID": "ch-1",
                "X-Goog-Resource-ID": "res-1",
            },
            client_ip="74.125.0.1",  # Google IP
        )
        result = await verifier.verify_request(request)
        assert result is True
        logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_missing_both_headers(self):
        verifier, logger = _make_verifier()
        request = _make_request_cov(headers={}, client_ip="1.2.3.4")
        result = await verifier.verify_request(request)
        assert result is False
        # Should have warnings for both invalid signature and verify_request
        assert logger.warning.call_count == 2

    @pytest.mark.asyncio
    async def test_success_with_non_google_ip(self):
        """IP check is commented out, so non-Google IPs should succeed."""
        verifier, logger = _make_verifier()
        request = _make_request_cov(
            headers={
                "X-Goog-Channel-ID": "ch-1",
                "X-Goog-Resource-ID": "res-1",
            },
            client_ip="192.168.1.1",
        )
        result = await verifier.verify_request(request)
        assert result is True


# ===================================================================
# _is_google_ip - additional edge cases
# ===================================================================

class TestIsGoogleIpAdditional:

    def test_ip_at_range_boundary_173_194(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("173.194.0.0") is True
        assert verifier._is_google_ip("173.194.255.255") is True

    def test_ip_at_range_boundary_209_85(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("209.85.128.0") is True
        assert verifier._is_google_ip("209.85.255.255") is True

    def test_ip_just_outside_range(self):
        verifier, _ = _make_verifier()
        # Just outside 64.233.160.0/19 -> 64.233.192.0
        assert verifier._is_google_ip("64.233.192.0") is False

    def test_loopback_address(self):
        verifier, _ = _make_verifier()
        assert verifier._is_google_ip("127.0.0.1") is False
