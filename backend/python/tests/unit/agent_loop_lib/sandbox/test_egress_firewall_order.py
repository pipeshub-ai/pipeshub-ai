"""An operator allowance (``SANDBOX_EGRESS_ALLOW_CIDRS``) can open private
hosts beyond the sandbox bridge, but never the bridge itself, its gateway or
the metadata endpoints (``egress_firewall.build_firewall_script``)."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    EgressNetworkUnsafe,
    build_firewall_script,
    ensure_egress_network_sync,
)

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN = "0123456789abcdef0123456789abcdef"
_ICC = "com.docker.network.bridge.enable_icc"
# A container on 192.168.0.0/16 whose gateway is 192.168.0.1, as the kernel
# lists it (little-endian hex).
_ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t0100A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t0000A8C0\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)


def _run(
    tmp_path: Path, network_cidrs: list[str], allowed: list[str], routes: str | None = _ROUTES,
    *, ipv6: dict[str, str] | None = None, ip6tables: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    """Run the script under ``sh`` with recording ``iptables``/``ip6tables``
    and return the result and the IPv4 and IPv6 rules it installed, in order.

    ``routes`` None leaves the route table unreadable. ``ipv6`` maps an
    interface to its ``disable_ipv6`` value (None: the kernel has no IPv6).
    """
    sh = shutil.which("dash") or shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX shell")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "rules.log"
    log6 = tmp_path / "rules6.log"
    log.touch()
    log6.touch()
    (bin_dir / "iptables").write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    if ip6tables:
        (bin_dir / "ip6tables").write_text(f'#!/bin/sh\necho "$*" >> {log6}\n')
    else:
        # Shadow a host ip6tables with one that does not work.
        (bin_dir / "ip6tables").write_text("#!/bin/sh\nexit 1\n")
        (bin_dir / "ip6tables-legacy").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "setpriv").write_text("#!/bin/sh\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    route_file = tmp_path / "route"
    if routes is not None:
        route_file.write_text(routes)
    conf = tmp_path / "ipv6conf"
    if ipv6 is not None:
        for iface, value in ipv6.items():
            (conf / iface).mkdir(parents=True)
            (conf / iface / "disable_ipv6").write_text(value + "\n")
    script = build_firewall_script(network_cidrs, allowed, token=_TOKEN)
    assert "/proc/net/route" in script and "/proc/sys/net/ipv6/conf" in script
    script = script.replace("/proc/net/route", str(route_file))
    script = script.replace("/proc/sys/net/ipv6/conf", str(conf))
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        [sh, "-c", script, "sandbox-egress", "true"], env=env, capture_output=True, text=True,
    )
    rules = [line.removeprefix("-w ") for line in log.read_text().splitlines()]
    rules6 = [line.removeprefix("-w ") for line in log6.read_text().splitlines()]
    return result, [r for r in rules if r.startswith("-A ")], [r for r in rules6 if not r.startswith("-n ")]


def _rules(tmp_path: Path, network_cidrs: list[str], allowed: list[str], routes: str = _ROUTES) -> list[str]:
    result, rules, _ = _run(tmp_path, network_cidrs, allowed, routes, ipv6={"eth0": "0"})
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, "sh", result.stdout, result.stderr)
    return rules


def _first(rules: list[str], dest: str) -> int:
    """The first rule for ``dest`` other than a DNS (port 53) accept, which
    test_egress_firewall_dns.py covers."""
    return next(i for i, rule in enumerate(rules) if f"-d {dest} " in rule and "--dport 53 " not in rule)


class TestAllowancesCannotOpenTheBridge:
    def test_the_bridge_subnet_is_rejected_before_any_allowance(self) -> None:
        script = build_firewall_script(["192.168.16.0/20"], ["192.168.0.0/16"], token=_TOKEN)
        lines = script.splitlines()
        reject = lines.index("r -A OUTPUT -d 192.168.16.0/20 -j REJECT")
        allow = lines.index("r -A OUTPUT -d 192.168.0.0/16 -j ACCEPT")
        assert reject < allow

    def test_installed_order(self, tmp_path: Path) -> None:
        rules = _rules(tmp_path, ["172.30.0.0/16"], ["0.0.0.0/0", "10.20.0.0/16"])
        allow = _first(rules, "0.0.0.0/0")
        assert rules[0] == "-A OUTPUT -o lo -j ACCEPT"
        for dest in ("169.254.0.0/16", "168.63.129.16/32", "172.30.0.0/16",
                     "192.168.0.1/32", "192.168.0.0/255.255.0.0"):
            assert rules[_first(rules, dest)].endswith("-j REJECT")
            assert _first(rules, dest) < allow, dest
        # Private ranges beyond the bridge stay openable.
        assert _first(rules, "10.20.0.0/16") < _first(rules, "10.0.0.0/8")

    def test_the_gateway_is_rejected_even_when_the_bridge_subnet_is_unknown(self, tmp_path: Path) -> None:
        rules = _rules(tmp_path, [], ["192.168.0.0/16"])
        assert _first(rules, "192.168.0.1/32") < _first(rules, "192.168.0.0/16")

    def test_an_unreadable_route_stops_the_run(self, tmp_path: Path) -> None:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            _rules(tmp_path, [], [], routes=_ROUTES + "eth0\tzz;id\t0\t0\t0\t0\t0\t0\n")
        assert exc.value.returncode == 222


class TestRecreatingANetworkItCannotRemove:
    def test_a_refused_remove_fails_the_networked_run(self) -> None:
        network = MagicMock()
        network.name = "egress"
        network.attrs = {"Options": {}, "Containers": {}, "IPAM": {"Config": [{"Subnet": "172.30.0.0/16"}]}}
        network.remove.side_effect = RuntimeError("403 network was not created by this sandbox proxy")
        client = MagicMock()
        client.networks.list.return_value = [network]
        client.networks.get.return_value = network

        with pytest.raises(EgressNetworkUnsafe, match="could not be recreated"):
            ensure_egress_network_sync(client, "egress", {})
        client.networks.create.assert_not_called()
