"""QA18-6 (VS-8, spec half): a worker and the sandbox broker one release apart, in both orders, over the broker's HTTP API.

A newer worker's spec may carry a field this broker does not know. Every spec field narrows what the sandbox may do
(resources, egress, mounts), so a broker that dropped one would run the sandbox with less isolation than asked: it
refuses the spec instead. A spec an older worker sent, without fields added since, is accepted with their defaults.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.errors import InvalidSandboxRequest
from harness_core.sandbox.remote import wire
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    MountSpec,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeEgressContractEnv, FakeSandboxBroker
from harness_core.testing.sandbox.contract import LEASE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOKEN = "qa18-broker-token"
PLACEHOLDER = "ph-model-0123456789abcdef"
SPEC = SandboxSpec(
    image_digest="sha256:" + "a" * 64,
    mode=RuntimeMode.DOCKER_HARDENED,
    egress=EgressPolicy(
        destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),),
        token_routes=(TokenRoute(PLACEHOLDER, "model-gateway", "gateway:model"),),
        forbidden_patterns=("SECRET",),
    ),
    mounts=(MountSpec("/work"),),
    labels={"runId": "r1"},
)


@pytest.fixture
def fake() -> FakeSandboxBroker:
    clock = ManualClock()
    return FakeSandboxBroker(clock=clock, egress_proxy=FakeEgressContractEnv(clock).proxy)


@pytest.fixture
async def broker_url(fake: FakeSandboxBroker) -> AsyncIterator[str]:
    server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
    await server.start_server()
    yield str(server.make_url(wire.SANDBOXES))
    await server.close()


async def _acquire(url: str, body: dict[str, Any]) -> tuple[int, Any]:
    async with aiohttp.ClientSession() as session, session.post(
        url, json=body, headers={"Authorization": f"Bearer {TOKEN}"}
    ) as response:
        return response.status, await response.json()


def _body(key: str) -> dict[str, Any]:
    return copy.deepcopy(wire.acquire_body(SPEC, LEASE, key))


def _newer(where: str) -> dict[str, Any]:
    body = _body(f"newer-{where}")
    spec = body["spec"]
    target = {
        "spec": spec,
        "resources": spec["resources"],
        "egress": spec["egress"],
        "destination": spec["egress"]["destinations"][0],
        "token_route": spec["egress"]["token_routes"][0],
        "mount": spec["mounts"][0],
    }[where]
    target["added_in_n_plus_1"] = "deny-more"
    return body


@pytest.mark.parametrize("where", ["spec", "resources", "egress", "destination", "token_route", "mount"])
async def test_qa18_6_a_newer_workers_spec_field_is_refused_never_dropped(
    broker_url: str, fake: FakeSandboxBroker, where: str
) -> None:
    status, body = await _acquire(broker_url, _newer(where))
    assert status == 400, body
    error = wire.error_from(body, status=status)
    assert isinstance(error, InvalidSandboxRequest)
    assert fake.sandboxes == {}


async def test_qa18_6_the_same_spec_without_the_newer_field_is_accepted(broker_url: str, fake: FakeSandboxBroker) -> None:
    status, body = await _acquire(broker_url, _body("current"))
    assert status == 200, body
    assert list(fake.sandboxes) == [body["id"]]


async def test_qa18_6_an_older_workers_spec_takes_the_defaults_of_fields_added_since(
    broker_url: str, fake: FakeSandboxBroker
) -> None:
    body = _body("older")
    spec = body["spec"]
    for added_later in ("resources", "mounts", "labels"):
        del spec[added_later]
    del spec["egress"]["forbidden_patterns"]
    status, answer = await _acquire(broker_url, body)
    assert status == 200, answer
    held = fake.sandboxes[answer["id"]].spec
    assert held.resources == SandboxSpec(SPEC.image_digest, SPEC.mode).resources
    assert held.egress.forbidden_patterns == ()
    assert held.egress.token_routes == SPEC.egress.token_routes
