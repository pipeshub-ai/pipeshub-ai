"""The public IP addresses connectors reach external systems from.

Users add these to a database's or API's firewall allowlist. ``CONNECTOR_EGRESS_IPS``
(comma-separated) wins when set: a deployment behind several NAT gateways knows its
addresses, a lookup from one process only sees one of them. Without it the address is
looked up once per process and cached.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import time

import httpx

EGRESS_IPS_ENV = "CONNECTOR_EGRESS_IPS"
_LOOKUP_URLS = ("https://api.ipify.org", "https://checkip.amazonaws.com")
_LOOKUP_TIMEOUT_S = 3.0
_CACHE_TTL_S = 6 * 3600
# Air-gapped installs fail every lookup; retry rarely so the form does not wait each time.
_FAILURE_TTL_S = 10 * 60

_logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
# (expires at, addresses); one entry per process.
_cache: dict[str, tuple[float, list[str]]] = {}


def _valid_ips(values: list[str]) -> list[str]:
    ips = []
    for value in values:
        try:
            ips.append(str(ipaddress.ip_address(value.strip())))
        except ValueError:
            continue
    return ips


async def _look_up() -> list[str]:
    # No env proxy: a database sees the direct (NAT) address, not an HTTP proxy's.
    async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_S, trust_env=False) as client:
        for url in _LOOKUP_URLS:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as e:
                _logger.info("Egress IP lookup via %s failed: %s", url, e)
                continue
            ips = _valid_ips([response.text])
            if ips:
                return ips
    return []


async def get_egress_ips() -> list[str]:
    configured = os.getenv(EGRESS_IPS_ENV, "").strip()
    if configured:
        return _valid_ips(configured.split(","))

    async with _lock:
        now = time.monotonic()
        cached = _cache.get("ips")
        if cached is not None and cached[0] > now:
            return list(cached[1])
        ips = await _look_up()
        _cache["ips"] = (now + (_CACHE_TTL_S if ips else _FAILURE_TTL_S), ips)
        return list(ips)
