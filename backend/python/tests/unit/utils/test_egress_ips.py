"""app.utils.egress_ips: the addresses users allow in their firewalls."""

from unittest.mock import AsyncMock, patch

import pytest

from app.utils import egress_ips


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.delenv(egress_ips.EGRESS_IPS_ENV, raising=False)
    monkeypatch.setattr(egress_ips, "_cache", {})


async def test_configured_addresses_win_and_invalid_entries_are_dropped(monkeypatch):
    monkeypatch.setenv(egress_ips.EGRESS_IPS_ENV, " 203.0.113.10, not-an-ip ,2001:db8::1")
    with patch.object(egress_ips, "_look_up", AsyncMock()) as look_up:
        assert await egress_ips.get_egress_ips() == ["203.0.113.10", "2001:db8::1"]
    look_up.assert_not_called()


async def test_none_turns_the_lookup_off(monkeypatch):
    monkeypatch.setenv(egress_ips.EGRESS_IPS_ENV, "none")
    with patch.object(egress_ips, "_look_up", AsyncMock()) as look_up:
        assert await egress_ips.get_egress_ips() == []
    look_up.assert_not_called()


async def test_lookup_runs_once_and_is_cached():
    with patch.object(egress_ips, "_look_up", AsyncMock(return_value=["198.51.100.7"])) as look_up:
        assert await egress_ips.get_egress_ips() == ["198.51.100.7"]
        assert await egress_ips.get_egress_ips() == ["198.51.100.7"]
    look_up.assert_awaited_once()


async def test_failed_lookup_is_cached_briefly(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(egress_ips.time, "monotonic", lambda: clock[0])
    with patch.object(egress_ips, "_look_up", AsyncMock(side_effect=[[], ["198.51.100.7"]])) as look_up:
        assert await egress_ips.get_egress_ips() == []
        assert await egress_ips.get_egress_ips() == []
        clock[0] += egress_ips._FAILURE_TTL_S + 1
        assert await egress_ips.get_egress_ips() == ["198.51.100.7"]
    assert look_up.await_count == 2


async def test_lookup_skips_a_failing_service_and_ignores_garbage():
    import httpx

    responses = {
        egress_ips._LOOKUP_URLS[0]: httpx.ConnectError("down"),
        egress_ips._LOOKUP_URLS[1]: httpx.Response(200, text="198.51.100.7\n"),
    }

    async def fake_get(self, url):
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        result.request = httpx.Request("GET", url)
        return result

    with patch.object(httpx.AsyncClient, "get", fake_get):
        assert await egress_ips._look_up() == ["198.51.100.7"]

    responses[egress_ips._LOOKUP_URLS[0]] = httpx.Response(200, text="<html>blocked</html>")
    responses[egress_ips._LOOKUP_URLS[1]] = httpx.Response(200, text="also not an ip")
    with patch.object(httpx.AsyncClient, "get", fake_get):
        assert await egress_ips._look_up() == []
