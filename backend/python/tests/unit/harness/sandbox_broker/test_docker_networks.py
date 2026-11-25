"""H4 DN-1..DN-3: test rigs create networks on the shared daemon with the broker's own overlap rule.

No daemon: a fake API that answers Docker's refusals.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.harness.sandbox.docker_egress import is_pool_overlap
from tests.cohesion.support import PYTHON_ROOT
from tests.support.docker_networks import create_on_free_subnet

OVERLAP = "Pool overlaps with other one on this address space"


class APIError(Exception):
    """Shaped like ``docker.errors.APIError``: the daemon's explanation is in its text."""


@dataclass
class FakeApi:
    taken: set[str] = field(default_factory=set[str])
    fail_with: Exception | None = None
    created: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def create_network(self, name: str, **kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        subnet = kwargs["ipam"]["Config"][0]["Subnet"]
        if self.fail_with is not None:
            raise self.fail_with
        if subnet in self.taken:
            raise APIError(f'400 Client Error: Bad Request ("invalid pool request: {OVERLAP}")')
        self.created.append((name, subnet))
        return {"Id": name}


@dataclass
class FakeClient:
    api: FakeApi


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (f"invalid pool request: {OVERLAP}", True),
        ("networks have overlapping IPv4", True),
        ("all predefined address pools have been fully subnetted", False),
        ("network with name pxh-x already exists", False),
    ],
)
def test_dn1_overlap_is_recognised_by_its_text(message: str, expected: bool) -> None:
    assert is_pool_overlap(APIError(message)) is expected


def test_dn2_an_overlapping_subnet_is_skipped_for_the_next() -> None:
    client = FakeClient(FakeApi(taken={"10.215.0.0/28"}))
    subnet = create_on_free_subnet(client, "pxh-rig", ["10.215.0.0/28", "10.215.0.16/28"], labels={"o": "x"})
    assert subnet == "10.215.0.16/28"
    assert client.api.created == [("pxh-rig", "10.215.0.16/28")]


def test_dn2_another_refusal_is_raised_at_once() -> None:
    client = FakeClient(FakeApi(fail_with=APIError("network with name pxh-rig already exists")))
    with pytest.raises(APIError, match="already exists"):
        create_on_free_subnet(client, "pxh-rig", ["10.215.0.0/28", "10.215.0.16/28"], labels={})


def test_dn2_every_candidate_taken_names_the_network() -> None:
    client = FakeClient(FakeApi(taken={"10.215.0.0/28", "10.215.0.16/28"}))
    with pytest.raises(RuntimeError, match="pxh-rig"):
        create_on_free_subnet(client, "pxh-rig", ["10.215.0.0/28", "10.215.0.16/28"], labels={})


def test_dn2_candidates_are_drawn_lazily() -> None:
    """DE-4 draws the next free subnet of the broker's pool only after the previous one was refused."""
    client = FakeClient(FakeApi(taken={"10.213.0.0/29"}))
    drawn: list[str] = []

    def candidates() -> Any:  # noqa: ANN401
        for subnet in ("10.213.0.0/29", "10.213.0.8/29", "10.213.0.16/29"):
            drawn.append(subnet)
            yield subnet

    assert create_on_free_subnet(client, "pxh-squat", candidates(), labels={}) == "10.213.0.8/29"
    assert drawn == ["10.213.0.0/29", "10.213.0.8/29"]


def test_dn3_the_rigs_and_the_broker_share_one_overlap_rule() -> None:
    """No copy of the predicate: every create that retries on overlap calls ``is_pool_overlap``."""
    files = [
        "app/harness/sandbox/docker_egress.py",
        "tests/support/docker_networks.py",
        "tests/unit/harness/sandbox_broker/test_egress_docker.py",
    ]
    for name in files:
        source = (PYTHON_ROOT / name).read_text(encoding="utf-8")
        literals = [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and node.value == "overlap"
        ]
        if name.startswith("app/"):
            assert literals == ["overlap"], name
        else:
            assert literals == [], f"{name} decides overlap itself"


# Network creates whose address cannot be read where they are written, each with why that is safe.
_ADDRESS_FROM_ELSEWHERE = {
    # docker-py's request body for kwargs the test passes in, against a fake transport.
    ("tests/unit/sandbox/test_docker_proxy.py", "networks.create"),
}


def _creates_without_an_address(path: str, tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and (
            func.attr == "create_network"
            or (func.attr == "create" and isinstance(func.value, ast.Attribute) and func.value.attr == "networks")
        ):
            kind = "create_network" if func.attr == "create_network" else "networks.create"
            keywords = {k.arg for k in node.keywords}
            if "ipam" not in keywords and (path, kind) not in _ADDRESS_FROM_ELSEWHERE:
                found.append(f"{path}:{node.lineno} {kind} without ipam")
            continue
        words = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if any(words[i : i + 2] == ["network", "create"] for i in range(len(words))) and not any(
            w.startswith("--subnet") for w in words
        ):
            found.append(f"{path}:{node.lineno} docker network create without --subnet")
    return found


def test_dn4_no_network_takes_an_address_from_the_daemons_default_pools() -> None:
    """A network created without an address takes a /16 or /20 from the daemon's ~30 default pools, which a shared
    host has none of left ("all predefined address pools have been fully subnetted", round 7 Docker lane): every
    rig and every product path names its subnet (``tests/support/docker_networks``, the broker's pools)."""
    found: list[str] = []
    for top in ("app", "harness_core", "harness_engines", "tests"):
        for file in sorted((PYTHON_ROOT / top).rglob("*.py")):
            path = file.relative_to(PYTHON_ROOT).as_posix()
            source = file.read_text(encoding="utf-8")
            if "network" not in source:
                continue
            found += _creates_without_an_address(path, ast.parse(source))
    assert found == []


def test_dn4_the_guard_sees_each_way_a_network_is_made() -> None:
    source = (
        "client.networks.create(name='n', driver='bridge')\n"
        "client.api.create_network('n', driver='bridge')\n"
        "_docker('network', 'create', '--internal', 'n')\n"
        "client.networks.create(name='n', ipam=ipam)\n"
        "_docker('network', 'create', '--subnet=10.215.0.0/28', 'n')\n"
        "client.networks.create.assert_called_once_with(name='n')\n"
    )
    assert [f.split(" ", 1)[1] for f in _creates_without_an_address("x.py", ast.parse(source))] == [
        "networks.create without ipam",
        "create_network without ipam",
        "docker network create without --subnet",
    ]
