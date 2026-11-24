"""Name resolution through the egress firewall.

Docker's embedded DNS server forwards from the sandbox's own network
namespace to resolvers set with the daemon's ``dns`` option (or ``--dns``),
so the firewall must let port 53 through to them. Only port 53: the cloud
resolvers share addresses with the metadata endpoint and Azure's wireserver,
and the gateway is the host. ``egress_firewall.build_firewall_script``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.coding import egress_firewall
from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    CLOUD_DNS_SERVERS,
    build_firewall_script,
    dns_servers_from_env,
)

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN = "0123456789abcdef0123456789abcdef"
# A container on 172.30.0.0/16 whose gateway is 172.30.0.1 (little-endian hex).
_ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t01001EAC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t00001EAC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)
_GATEWAY = "172.30.0.1"
_BRIDGE = "172.30.0.0/16"
# What Docker writes when the daemon has ``"dns": [...]``, plus entries the
# script must skip: a host-namespace server, IPv6 and garbage.
_RESOLV_OVERRIDE = (
    "nameserver 127.0.0.11\noptions ndots:0\n\n"
    "# Based on host file: '/etc/resolv.conf' (internal resolver)\n"
    "# ExtServers: [host(127.0.0.53) 10.1.2.3 fd00::53 1.2.3.4;id $(id)]\n"
    "# Overrides: [nameservers]\n"
)
_RESOLV_HOST = (
    "nameserver 127.0.0.11\n"
    "# ExtServers: [host(127.0.0.53)]\n"
)


def _installed(
    tmp_path: Path, *, allowed: tuple[str, ...] = (), resolv: str | None = _RESOLV_HOST,
    dns_servers: tuple[str, ...] | None = None, iptables_refuses_dns: bool = False,
) -> list[str]:
    """Run the script under ``sh`` with a recording ``iptables`` and return
    the IPv4 rules it installed, in order."""
    sh = shutil.which("dash") or shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX shell")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "rules.log"
    log.touch()
    refuse = 'case "$*" in *"--dport 53"*) exit 1;; esac\n' if iptables_refuses_dns else ""
    (bin_dir / "iptables").write_text(f'#!/bin/sh\n{refuse}echo "$*" >> {log}\n')
    (bin_dir / "ip6tables").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "setpriv").write_text("#!/bin/sh\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    routes = tmp_path / "route"
    routes.write_text(_ROUTES)
    resolv_conf = tmp_path / "resolv.conf"
    if resolv is not None:
        resolv_conf.write_text(resolv)
    script = build_firewall_script([_BRIDGE], allowed, token=_TOKEN, dns_servers=dns_servers)
    assert "/etc/resolv.conf" in script
    script = (
        script.replace("/proc/net/route", str(routes))
        .replace("/etc/resolv.conf", str(resolv_conf))
        .replace("/proc/sys/net/ipv6/conf", str(tmp_path / "no-ipv6"))
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run([sh, "-c", script, "sandbox-egress", "true"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    rules = [line.removeprefix("-w ") for line in log.read_text().splitlines()]
    return [r for r in rules if r.startswith("-A ")]


def _verdict(rules: list[str], dest: str, proto: str, port: int) -> str:
    """First-match verdict of the installed OUTPUT rules for one packet."""
    addr = ipaddress.ip_address(dest)
    for rule in rules:
        words = rule.split()
        opts = dict(zip(words[2::2], words[3::2]))
        if "-o" in opts:
            continue  # loopback only
        if "-d" in opts and addr not in ipaddress.ip_network(opts["-d"], strict=False):
            continue
        if "-p" in opts and (opts["-p"] != proto or int(opts["--dport"]) != port):
            continue
        return opts["-j"]
    return "ACCEPT"  # the chain policy: public addresses


class TestDnsReachesTheResolversAndNothingElse:
    @pytest.mark.parametrize("server", CLOUD_DNS_SERVERS)
    def test_cloud_resolvers_answer_on_53_only(self, tmp_path: Path, server: str) -> None:
        rules = _installed(tmp_path, allowed=("0.0.0.0/0",))
        assert _verdict(rules, server, "udp", 53) == "ACCEPT"
        assert _verdict(rules, server, "tcp", 53) == "ACCEPT"
        for port in (80, 443, 32526, 8080):
            assert _verdict(rules, server, "tcp", port) == "REJECT", port
        assert _verdict(rules, server, "udp", 123) == "REJECT"

    def test_the_rest_of_link_local_stays_closed_on_53(self, tmp_path: Path) -> None:
        rules = _installed(tmp_path)
        assert _verdict(rules, "169.254.170.2", "udp", 53) == "REJECT"
        assert _verdict(rules, "169.254.169.254", "tcp", 80) == "REJECT"

    def test_the_gateway_answers_on_53_only(self, tmp_path: Path) -> None:
        rules = _installed(tmp_path, allowed=("0.0.0.0/0", "172.16.0.0/12"))
        assert _verdict(rules, _GATEWAY, "udp", 53) == "ACCEPT"
        assert _verdict(rules, _GATEWAY, "tcp", 53) == "ACCEPT"
        for port in (22, 80, 2375, 6379, 27017):
            assert _verdict(rules, _GATEWAY, "tcp", port) == "REJECT", port
        # A sibling on the bridge gets nothing, not even DNS.
        assert _verdict(rules, "172.30.0.5", "udp", 53) == "REJECT"

    def test_daemon_set_resolvers_from_resolv_conf_are_opened(self, tmp_path: Path) -> None:
        rules = _installed(tmp_path, resolv=_RESOLV_OVERRIDE)
        assert _verdict(rules, "10.1.2.3", "udp", 53) == "ACCEPT"
        assert _verdict(rules, "10.1.2.3", "tcp", 443) == "REJECT"
        # host(...) is dialled from the host, and malformed entries are skipped.
        assert _verdict(rules, "127.0.0.53", "udp", 53) == "REJECT"
        assert _verdict(rules, "1.2.3.4", "udp", 53) == "ACCEPT"  # public anyway
        assert not any("1.2.3.4;" in r or "$(id)" in r or "fd00" in r for r in rules)

    def test_no_resolv_conf_still_opens_the_fixed_list(self, tmp_path: Path) -> None:
        rules = _installed(tmp_path, resolv=None)
        assert _verdict(rules, "168.63.129.16", "udp", 53) == "ACCEPT"
        assert _verdict(rules, "10.1.2.3", "udp", 53) == "REJECT"

    def test_a_refused_dns_rule_fails_closed_without_stopping_the_run(self, tmp_path: Path) -> None:
        rules = _installed(tmp_path, resolv=_RESOLV_OVERRIDE, dns_servers=(), iptables_refuses_dns=True)
        assert _verdict(rules, _GATEWAY, "udp", 53) == "REJECT"
        assert _verdict(rules, "10.1.2.3", "udp", 53) == "REJECT"

    def test_dns_accepts_come_after_loopback_and_before_every_reject(self, tmp_path: Path) -> None:
        rules = _installed(tmp_path, resolv=_RESOLV_OVERRIDE)
        assert rules[0] == "-A OUTPUT -o lo -j ACCEPT"
        first_reject = next(i for i, r in enumerate(rules) if r.endswith("-j REJECT"))
        dns = [i for i, r in enumerate(rules) if "--dport 53 " in r]
        assert dns and max(dns) < first_reject
        assert all(rules[i].endswith("-j ACCEPT") and "/32 " in rules[i] for i in dns)


class TestResolverList:
    def test_default_is_the_cloud_resolvers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SANDBOX_EGRESS_DNS_SERVERS", raising=False)
        assert dns_servers_from_env() == CLOUD_DNS_SERVERS
        assert set(CLOUD_DNS_SERVERS) == {"168.63.129.16", "169.254.169.253", "169.254.169.254"}

    def test_operator_servers_are_added_and_bad_ones_dropped(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        egress_firewall._parse_dns_servers.cache_clear()
        monkeypatch.setenv("SANDBOX_EGRESS_DNS_SERVERS", " 10.0.0.2, ,10.0.0.0/8,fd00::53,dns.local,10.0.0.2,192.168.1.1;id")
        with caplog.at_level(logging.WARNING):
            servers = dns_servers_from_env()
        assert servers == (*CLOUD_DNS_SERVERS, "10.0.0.2")
        warned = " ".join(r.getMessage() for r in caplog.records)
        for bad in ("10.0.0.0/8", "fd00::53", "dns.local", "192.168.1.1;id"):
            assert bad in warned
        script = build_firewall_script([_BRIDGE], token=_TOKEN)
        assert "r -A OUTPUT -d 10.0.0.2/32 -p udp --dport 53 -j ACCEPT" in script.splitlines()
        assert "10.0.0.0/8 -p" not in script

    @pytest.mark.parametrize("bad", ["10.0.0.0/8", "fd00::1", "x; reboot"])
    def test_an_explicit_non_address_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError):
            build_firewall_script([_BRIDGE], token=_TOKEN, dns_servers=[bad])

    def test_an_explicit_empty_list_opens_only_the_runtime_resolvers(self) -> None:
        script = build_firewall_script([_BRIDGE], token=_TOKEN, dns_servers=())
        assert "168.63.129.16/32 -p" not in script
        assert "--dport 53" in script  # the gateway / resolv.conf helper
