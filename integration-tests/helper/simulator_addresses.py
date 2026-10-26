"""Let this test process reach the connector simulators, which it calls on loopback.

Every ``HTTPClient`` sends through the connector address guard, which never reaches loopback: a
connector's server is an address a user typed. The suites build the product's own clients (the
Jira and Confluence datasources) to read the simulators on their published ports, so the guard
refuses them. The app container is not affected: it reaches the simulators by compose alias.

Only the exact origins the simulator settings name pass, pinned to the address they were given.
Every other URL gets the real check, so loopback stays refused on any other port and in any other
spelling, and the guard still checks and pins each request.
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

from app.agents.mcp import url_guard
from app.sources.client.http import address_guard
from app.utils import url_fetcher

# The simulator env files' URLs the test process itself calls (connectors/*_sim.env).
SIMULATOR_URL_SETTINGS = (
    "CONFLUENCE_TEST_BASE_URL",
    "JIRA_TEST_BASE_URL",
    "NOTION_API_BASE_URL",
    "NOTION_TEST_RESET_URL",
    "GMAIL_SIM_URL",
)
_LOOPBACK_NAMES = {"localhost": "127.0.0.1"}


def simulator_origins() -> dict[tuple[str, str, int], str]:
    """Each configured simulator origin on loopback, with the address it is pinned to."""
    origins: dict[tuple[str, str, int], str] = {}
    for name in SIMULATOR_URL_SETTINGS:
        parts = urlsplit(os.getenv(name) or "")
        host = (parts.hostname or "").lower()
        address = _LOOPBACK_NAMES.get(host, host)
        try:
            if not ipaddress.ip_address(address).is_loopback:
                continue
        except ValueError:
            continue
        port = parts.port or (443 if parts.scheme == "https" else 80)
        origins[(parts.scheme, host, port)] = address
    return origins


def allow_the_simulators() -> None:
    """Pass the configured simulator origins through the guard; a no-op when none is set."""
    origins = simulator_origins()
    if not origins:
        return
    real = url_fetcher.resolve_public_http_target

    def resolve(url: str, **policy: bool) -> url_fetcher.PublicTarget:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        address = origins.get((parts.scheme, host, port))
        if address is not None:
            return url_fetcher.PublicTarget(parts.scheme, host, port, (ipaddress.ip_address(address),))
        return real(url, **policy)

    # The guard's two lookups: the httpx transport (through url_guard) and the requests adapter.
    address_guard.resolve_public_http_target = resolve
    url_guard.resolve_public_http_target = resolve
