"""The egress bridge and the in-container firewall fail closed: a network that
allows inter-container traffic or IPv6 is never joined, an unreadable route
table stops the run, and IPv6 output is dropped (``egress_firewall``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    FIREWALL_UNAVAILABLE_EXIT_CODE,
    EgressNetworkUnsafe,
    ensure_egress_network_sync,
)
from tests.unit.agent_loop_lib.sandbox.test_egress_firewall_order import _ROUTES, _run

if TYPE_CHECKING:
    from pathlib import Path

_ICC = "com.docker.network.bridge.enable_icc"


def _network(*, icc_off: bool = True, ipv6: bool = False, containers: dict[str, Any] | None = None,
             subnet: str = "172.30.0.0/16") -> MagicMock:
    network = MagicMock()
    network.name = "egress"
    network.id = "a" * 64
    network.attrs = {
        "Options": {_ICC: "false"} if icc_off else {},
        "EnableIPv6": ipv6,
        "IPAM": {"Config": [{"Subnet": subnet}]},
        "Containers": containers or {},
    }
    return network


def _client(*found: list[MagicMock]) -> MagicMock:
    client = MagicMock()
    client.networks.list.side_effect = list(found)
    gets = [n for batch in found for n in batch]
    client.networks.get.side_effect = gets
    return client


class TestUnsafeNetworks:
    def test_an_ipv6_network_in_use_is_refused(self) -> None:
        net = _network(ipv6=True, containers={"c": {}})
        client = _client([net])
        with pytest.raises(EgressNetworkUnsafe, match="IPv6"):
            ensure_egress_network_sync(client, "egress", {})
        net.remove.assert_not_called()
        client.networks.create.assert_not_called()

    def test_an_idle_ipv6_network_is_recreated(self) -> None:
        old, fresh = _network(ipv6=True), _network(subnet="172.31.0.0/16")
        client = _client([old], [fresh])
        assert ensure_egress_network_sync(client, "egress", {}) == ["172.31.0.0/16"]
        old.remove.assert_called_once()
        assert client.networks.create.call_args.kwargs["enable_ipv6"] is False

    def test_an_ipv6_network_that_cannot_be_removed_is_refused(self) -> None:
        net = _network(ipv6=True)
        net.remove.side_effect = RuntimeError("403 network was not created by this sandbox proxy")
        client = _client([net])
        with pytest.raises(EgressNetworkUnsafe, match="IPv6 enabled and could not be recreated"):
            ensure_egress_network_sync(client, "egress", {})

    def test_a_network_the_daemon_created_with_ipv6_is_refused(self) -> None:
        created = _network(ipv6=True)
        client = _client([], [created])
        with pytest.raises(EgressNetworkUnsafe, match="default-network-opts"):
            ensure_egress_network_sync(client, "egress", {})

    def test_a_raced_network_with_icc_on_is_refused(self) -> None:
        raced = _network(icc_off=False)
        client = _client([], [raced], [raced])
        client.networks.create.side_effect = RuntimeError("409 already exists")
        with pytest.raises(EgressNetworkUnsafe, match="inter-container"):
            ensure_egress_network_sync(client, "egress", {})

    def test_a_safe_network_is_used_as_found(self) -> None:
        net = _network(containers={"c": {}})
        client = _client([net])
        assert ensure_egress_network_sync(client, "egress", {}) == ["172.30.0.0/16"]
        net.remove.assert_not_called()


class TestRouteTable:
    def test_an_unreadable_route_table_stops_the_run(self, tmp_path: Path) -> None:
        result, rules, _ = _run(tmp_path, ["172.30.0.0/16"], ["0.0.0.0/0"], routes=None, ipv6={"eth0": "1"})
        assert result.returncode == FIREWALL_UNAVAILABLE_EXIT_CODE
        assert "cannot read" in result.stderr
        # Nothing was allowed before the run stopped.
        assert not any(rule.endswith("-j ACCEPT") and "-o lo" not in rule for rule in rules)


class TestIPv6:
    def test_ipv6_output_is_dropped_except_loopback(self, tmp_path: Path) -> None:
        result, _, rules6 = _run(tmp_path, [], [], ipv6={"eth0": "0", "lo": "0"})
        assert result.returncode == 0, result.stderr
        assert rules6[0] == "-A OUTPUT -o lo -j ACCEPT"
        assert "-P OUTPUT DROP" in rules6
        assert rules6.index("-A OUTPUT -j REJECT") < rules6.index("-P OUTPUT DROP")

    def test_without_ip6tables_ipv6_on_an_interface_stops_the_run(self, tmp_path: Path) -> None:
        result, _, _ = _run(tmp_path, [], [], ipv6={"eth0": "0", "lo": "0"}, ip6tables=False)
        assert result.returncode == FIREWALL_UNAVAILABLE_EXIT_CODE
        assert "IPv6 is enabled on eth0" in result.stderr

    def test_without_ip6tables_ipv6_disabled_everywhere_is_fine(self, tmp_path: Path) -> None:
        result, _, _ = _run(
            tmp_path, [], [], ipv6={"all": "1", "default": "1", "eth0": "1", "lo": "0"}, ip6tables=False,
        )
        assert result.returncode == 0, result.stderr

    def test_a_kernel_without_ipv6_needs_no_ip6tables(self, tmp_path: Path) -> None:
        result, _, rules6 = _run(tmp_path, [], [], _ROUTES, ipv6=None, ip6tables=False)
        assert result.returncode == 0, result.stderr
        assert rules6 == []
