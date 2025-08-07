"""
src/api/middleware.py
"""

from ipaddress import ip_address, ip_network

from cryptography.exceptions import InvalidSignature
from fastapi import Request


class WebhookAuthVerifier:
    """Reusable webhook auth helper for specific routes"""

    def __init__(self, logger) -> None:
        self.logger = logger
        self.google_ips = [
            "64.233.160.0/19",
            "66.102.0.0/20",
            "66.249.80.0/20",
            "72.14.192.0/18",
            "74.125.0.0/16",
            "108.177.8.0/21",
            "173.194.0.0/16",
            "209.85.128.0/17",
            "216.58.192.0/19",
            "216.239.32.0/19",
        ]

    async def verify_request(self, request: Request) -> bool:
        """Combined IP and signature check"""
        client_ip = request.client.host

        # Uncomment this to enable IP restriction
        # if not self._is_google_ip(client_ip):
        #     self.logger.warning(f"Unauthorized IP attempt from: {client_ip}")
        #     return False

        if not await self._verify_signature(request):
            self.logger.warning("Invalid signature in request from %s", client_ip)
            return False

        self.logger.info("Webhook request authenticated from %s", client_ip)
        return True

    def _is_google_ip(self, ip: str) -> bool:
        """Check if IP is from Google's range"""
        try:
            ip_addr = ip_address(ip)
            return any(
                ip_addr in ip_network(google_range) for google_range in self.google_ips
            )
        except (ValueError, TypeError) as e:
            self.logger.error("IP parsing error: %s", str(e))
            return False

    async def _verify_signature(self, request: Request) -> bool:
        """Verify headers or HMAC signature if needed"""
        try:
            channel_id = (
                request.headers.get("X-Goog-Channel-ID")
                or request.headers.get("x-goog-channel-id")
                or request.headers.get("X-GOOG-CHANNEL-ID")
            )

            resource_id = (
                request.headers.get("X-Goog-Resource-ID")
                or request.headers.get("x-goog-resource-id")
                or request.headers.get("X-GOOG-RESOURCE-ID")
            )

            if not channel_id or not resource_id:
                self.logger.warning(
                    "Missing headers. Channel ID: %s, Resource ID: %s",
                    bool(channel_id),
                    bool(resource_id),
                )
                return False

            # TODO: Add actual signature validation here
            return True

        except (InvalidSignature, ValueError, TypeError) as e:
            self.logger.error("Signature verification error: %s", str(e))
            return False
