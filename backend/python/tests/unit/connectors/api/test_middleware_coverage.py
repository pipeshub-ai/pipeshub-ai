"""Additional tests for WebhookAuthVerifier targeting remaining uncovered lines.

Covers:
- verify_request with IP check (currently commented out but _is_google_ip tested)
- _verify_signature with various header combinations and edge cases
- verify_request integration with different scenarios
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.api.middleware import WebhookAuthVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(headers=None, client_ip="1.2.3.4"):
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
        request = _make_request({
            "X-Goog-Channel-ID": "channel-1",
            "x-goog-resource-id": "resource-1",
        })
        result = await verifier._verify_signature(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_channel_from_uppercase_resource_from_mixed(self):
        verifier, _ = _make_verifier()
        request = _make_request({
            "X-GOOG-CHANNEL-ID": "channel-1",
            "X-Goog-Resource-ID": "resource-1",
        })
        result = await verifier._verify_signature(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_all_header_variants_present(self):
        """When all case variants are present, first match wins."""
        verifier, _ = _make_verifier()
        request = _make_request({
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
        request = _make_request(
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
        request = _make_request(headers={}, client_ip="1.2.3.4")
        result = await verifier.verify_request(request)
        assert result is False
        # Should have warnings for both invalid signature and verify_request
        assert logger.warning.call_count == 2

    @pytest.mark.asyncio
    async def test_success_with_non_google_ip(self):
        """IP check is commented out, so non-Google IPs should succeed."""
        verifier, logger = _make_verifier()
        request = _make_request(
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
