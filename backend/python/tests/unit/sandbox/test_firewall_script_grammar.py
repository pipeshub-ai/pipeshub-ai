"""The grammar the Docker API proxy holds each egress firewall script to, against the scripts the broker writes.

``sandbox_floor`` imports only the stdlib (the legacy proxy runs on nothing else), so it cannot import the builder in
``egress_firewall``; the two are kept the same here instead. Every script either builder writes must pass its check,
and a script with anything of a caller's own in it must not (R43SOCK-FU-4 for the broker's helper).
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    build_firewall_rules_script,
    build_firewall_script,
    new_firewall_token,
)
from app.sandbox.sandbox_floor import firewall_rules_script_ok, firewall_script_ok

_SHAPES = [
    ([], []),
    (["172.30.0.0/16"], []),
    (["10.212.0.0/22", "10.212.4.0/22"], ["10.250.0.7/32", "192.168.1.0/24"]),
    (["10.213.0.8/29"], ["10.213.0.10/32"]),
]


@pytest.mark.parametrize(("networks", "allowed"), _SHAPES)
def test_every_rules_script_the_broker_writes_passes(networks: list[str], allowed: list[str]) -> None:
    for _ in range(3):
        script = build_firewall_rules_script(networks, allowed, token=new_firewall_token())
        assert firewall_rules_script_ok(script), script


@pytest.mark.parametrize(("networks", "allowed"), _SHAPES)
def test_every_legacy_script_passes_its_own_check_only(networks: list[str], allowed: list[str]) -> None:
    token = new_firewall_token()
    legacy = build_firewall_script(networks, allowed, "sandbox", token=token)
    rules = build_firewall_rules_script(networks, allowed, token=token)
    assert firewall_script_ok(legacy)
    assert not firewall_rules_script_ok(legacy), "the legacy script drops to a user and runs the caller's command"
    assert not firewall_script_ok(rules)


_GOOD = build_firewall_rules_script(["10.212.0.0/22"], ["10.250.0.7/32"], token="ab" * 8)


@pytest.mark.parametrize(
    "script",
    [
        _GOOD.replace("exit 0\n", "id\nexit 0\n"),
        _GOOD.replace("exit 0\n", "exit 0\nid\n"),
        _GOOD.replace("exit 0\n", ""),
        _GOOD.replace("exit 0\n", "exit 1\n"),
        _GOOD.replace("r -A OUTPUT -o lo -j ACCEPT", "r -F OUTPUT"),
        _GOOD.replace("-d 10.250.0.7/32 -j ACCEPT", "-d 10.250.0.7/32 -j DROP"),
        _GOOD.replace("10.250.0.7/32", "10.250.0.7/32; id"),
        _GOOD.replace("10.250.0.7/32", "$(id)"),
        _GOOD.replace("10.250.0.7/32", "10.250.0.1/24"),
        _GOOD.replace("10.250.0.7/32", "::/0"),
        _GOOD.replace("set -u", "set -u; id"),
        _GOOD.replace("ab" * 8, "not-a-token"),
        _GOOD.replace("\n", "\r\n"),
        _GOOD + "\n",
        "",
        "exit 0\n",
    ],
)
def test_a_rules_script_with_anything_else_is_refused(script: str) -> None:
    assert not firewall_rules_script_ok(script)


def test_the_good_script_without_a_trailing_newline_still_passes() -> None:
    assert firewall_rules_script_ok(_GOOD.rstrip("\n"))
