"""Where the connector service listens.

The web app only calls the gateway. A few checks have to reach the connector
service itself: routes the gateway does not serve, and the Python side's own
answer to a token.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

CONNECTOR_URL_ENV = "PIPESHUB_CONNECTOR_URL"


def connector_service_url(gateway_url: str) -> str:
    """``PIPESHUB_CONNECTOR_URL``, or port 8088 beside the gateway, which the integration stack publishes."""
    explicit = os.getenv(CONNECTOR_URL_ENV, "").strip()
    if explicit:
        return explicit.rstrip("/")
    parsed = urlparse(gateway_url)
    return f"{parsed.scheme}://{parsed.hostname}:8088"
