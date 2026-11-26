"""WP-61 item 4 (G-45): the provider gate is keyed by the upstream account and endpoint a call reaches, not the
provider alone.

``harness:provider-gate:{provider}`` was one limit for every org and every upstream account of a provider: two orgs
on their own keys queued behind each other for a limit neither upstream set. The key is now the rate-limited unit:
the provider, a digest of the account identity (never the key itself) and a digest of the endpoint or region. The
model gateway (engine calls) and the worker (loop calls) derive it from the same configuration, so one account's
calls still share one limit across processes. Old keys are not migrated: their PEXPIRE ttl lets them go.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.harness.access.configured import ConfiguredModelAccess
from app.harness.assembly.model import ResolvedModel, admitted, resolved_model
from app.harness.provider_gate import RedisProviderGate
from harness_core.access.types import ENGINE_MODEL_ID, PinnedRoute
from harness_core.gateway.model_gateway import ChatCompletion
from harness_core.gateway.priority_gate import PriorityGate, provider_key, upstream_key
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.testing import ManualClock
from harness_core.testing.model_gateway import (
    ROUTE,
    GatewayRig,
    RecordedTransport,
    body_of,
    org_key_route,
)
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.support.settle import settle
from tests.unit.harness.access.test_configured_access import Builds, FakeConfig

if TYPE_CHECKING:
    import pytest

SECRET_A = "sk-test-account-a-not-real-0001"
SECRET_B = "sk-test-account-b-not-real-0002"
_KEY = re.compile(r"^[a-z0-9._-]+(:a([0-9a-f]{16}|-):e([0-9a-f]{12}|-))?$")


def test_r11d_pg_1_the_key_is_deterministic_bounded_and_never_carries_the_secret() -> None:
    config = {"apiKey": SECRET_A, "endpoint": "https://Example.OpenAI.Azure.com/"}
    key = upstream_key("azureOpenAI", config)
    assert key == upstream_key("azureOpenAI", dict(config))
    assert _KEY.match(key) and key.startswith("azureopenai:a")
    assert SECRET_A not in key and "example" not in key
    assert "{" not in key and "}" not in key
    # The endpoint is normalised: case and a trailing slash do not split one upstream in two.
    assert key == upstream_key("azureOpenAI", {"apiKey": SECRET_A, "endpoint": "https://example.openai.azure.com"})


def test_r11d_pg_2_another_account_or_another_endpoint_is_another_key() -> None:
    a = upstream_key("openAI", {"apiKey": SECRET_A})
    assert a != upstream_key("openAI", {"apiKey": SECRET_B})
    bedrock = {"accessKeyId": "AKIATESTNOTREAL", "region": "us-east-1"}
    assert upstream_key("bedrock", bedrock) != upstream_key("bedrock", {**bedrock, "region": "eu-west-1"})
    azure = {"apiKey": SECRET_A, "endpoint": "https://one.openai.azure.com"}
    assert upstream_key("azureOpenAI", azure) != upstream_key(
        "azureOpenAI", {**azure, "endpoint": "https://two.openai.azure.com"}
    )
    # The account half and the endpoint half are separate digests: one account on two endpoints shares its 'a' part.
    one, two = upstream_key("azureOpenAI", azure), upstream_key("azureOpenAI", {**azure, "endpoint": "https://two.x"})
    assert one.split(":")[1] == two.split(":")[1] and one.split(":")[2] != two.split(":")[2]
    # Fields that name neither an account nor an endpoint (the model, a deployment) do not split the key.
    assert a == upstream_key("openAI", {"apiKey": SECRET_A, "model": "gpt-4o", "deploymentName": "d"})


def test_r11d_pg_3_without_an_account_or_endpoint_the_key_is_the_providers_as_before() -> None:
    assert upstream_key("OpenAI", {}) == provider_key("OpenAI") == "openai"
    assert upstream_key("ollama", {"apiKey": "", "model": "llama3"}) == "ollama"
    assert upstream_key("openAI", None) == "openai"


async def _held_then_other(gate: RedisProviderGate | PriorityGate, first: str, second: str) -> bool:
    """Whether a call on ``second`` is granted while a call on ``first`` holds the only slot."""
    held = await gate.acquire(first, "interactive")
    try:
        other = await asyncio.wait_for(gate.acquire(second, "interactive"), timeout=0.5)
    except TimeoutError:
        return False
    finally:
        held.release()
    other.release()
    return True


async def test_r11d_pg_4_two_orgs_on_separate_accounts_do_not_share_a_limit_two_runs_on_one_account_do() -> None:
    redis = FakeRedisConnectionProvider(is_cluster=True, key_namespace="ns")
    gateway = RedisProviderGate(redis, 1, floor_every=0, monotonic=time.monotonic)
    worker = RedisProviderGate(redis, 1, floor_every=0, monotonic=time.monotonic)
    org_a = upstream_key("openAI", {"apiKey": SECRET_A})
    org_b = upstream_key("openAI", {"apiKey": SECRET_B})
    try:
        # Org A's call holds the only slot of its account (at the gateway); org B's own account is free (a worker).
        held = await gateway.acquire(org_a, "interactive")
        other = await settle(worker.acquire(org_b, "interactive"))
        other.release()
        # A second run on org A's account, from another process, waits for the slot.
        second = asyncio.ensure_future(worker.acquire(org_a, "interactive"))
        await asyncio.sleep(0.3)
        assert not second.done()
        held.release()
        (await asyncio.wait_for(second, timeout=2.0)).release()
        keys = [k async for k in redis.scan_keys("*provider-gate*")]
        assert keys and all(SECRET_A not in k and SECRET_B not in k for k in keys)
        assert {f"ns:harness:provider-gate:{{{org_a}}}:state", f"ns:harness:provider-gate:{{{org_b}}}:state"} <= set(keys)
    finally:
        await gateway.aclose()
        await worker.aclose()


async def test_r11d_pg_4_the_process_gate_scopes_alike() -> None:
    gate = PriorityGate(1, floor_every=0, monotonic=ManualClock().monotonic)
    a, b = upstream_key("openAI", {"apiKey": SECRET_A}), upstream_key("openAI", {"apiKey": SECRET_B})
    assert await _held_then_other(gate, a, b)
    assert not await _held_then_other(gate, a, a)


def _entry(**configuration: str) -> dict[str, Any]:
    return {
        "provider": "azureOpenAI",
        "modelKey": "key-azure",
        "isDefault": True,
        "configuration": {"model": "gpt-5.4-mini", "deploymentName": "dep-1", **configuration},
    }


class _Run:
    org_id = "org-1"
    session_id = "s-1"

    class model:
        provider = "azureOpenAI"
        model_name = "gpt-5.4-mini"
        model = "gpt-5.4-mini"
        model_key = "key-azure"


class _Llm:
    model_name = "gpt-5.4-mini"


async def test_r11d_pg_5_the_gateway_and_the_loop_derive_one_key_for_one_account(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _entry(apiKey=SECRET_A, endpoint="https://example.openai.azure.com")
    access = ConfiguredModelAccess(FakeConfig({"llm": [copy.deepcopy(entry)]}), transports=Builds())  # type: ignore[arg-type]
    resolved = await access.resolve("org-1", PinnedRoute("pipeshub", "org-default"))
    assert resolved.admission_key == upstream_key("azureOpenAI", entry["configuration"])
    assert SECRET_A not in resolved.admission_key

    from app.harness.assembly import model as model_module

    def build(_llm: object, **_options: object) -> tuple[RecordedTransport, str]:
        return RecordedTransport(), "direct"

    monkeypatch.setattr(model_module, "build_transport", build)
    loop = resolved_model(_Run(), _Llm(), entry)  # type: ignore[arg-type]
    assert loop.admission_key == resolved.admission_key


async def test_r11d_pg_5_the_loop_admits_on_the_upstream_key() -> None:
    gate = PriorityGate(1, floor_every=0, monotonic=ManualClock().monotonic)
    base = ResolvedModel(
        raw_llm=None, provider="openAI", is_multimodal=False, context_length=None, is_reasoning=False,
        port=None,  # type: ignore[arg-type]
        transport=RecordedTransport(), admission_key="openai:a0123456789abcdef:e-",
    )
    out = admitted(base, gate, "interactive", None)
    assert isinstance(out.transport, AdmittedTransport) and out.transport.key == "openai:a0123456789abcdef:e-"
    unscoped = admitted(
        ResolvedModel(raw_llm=None, provider="openAI", is_multimodal=False, context_length=None, is_reasoning=False,
                      port=None, transport=RecordedTransport()),  # type: ignore[arg-type]
        gate, "interactive", None,
    )
    assert isinstance(unscoped.transport, AdmittedTransport) and unscoped.transport.key == "openai"


async def _queued(gate: PriorityGate, key: str, call: asyncio.Future[object]) -> None:
    """Until ``call`` waits at ``key``'s slot. A fixed count of loop ticks raced the gateway's own awaits before the
    gate on a loaded host (round 17 gate), so this waits for the state itself, bounded."""
    async def reached() -> None:
        while gate.waiting(key) == 0 and not call.done():  # noqa: ASYNC110 - the gate has no event to wait on
            await asyncio.sleep(0.001)

    await asyncio.wait_for(reached(), timeout=5.0)


async def test_r11d_pg_6_the_model_gateway_takes_its_slot_on_the_routes_upstream_key() -> None:
    gate = PriorityGate(1, floor_every=0, monotonic=ManualClock().monotonic)
    rig = GatewayRig(gate=gate)
    token_a = rig.token(rig.pin("run-a", tenant="org-a"))
    token_b = rig.token(rig.pin("run-b", tenant="org-b"))
    for tenant, secret in (("org-a", SECRET_A), ("org-b", SECRET_B)):
        route = org_key_route(ROUTE, rig.transport)
        rig.resolver.add(tenant, ROUTE, replace(route, admission_key=upstream_key("openai", {"apiKey": secret})))
    key_a = upstream_key("openai", {"apiKey": SECRET_A})
    held = await gate.acquire(key_a, "interactive")
    body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "stream": False}
    # Org B's account is free although org A's is full.
    outcome = await asyncio.wait_for(rig.gateway.chat(token_b, {}, body_of(body)), timeout=2.0)
    assert isinstance(outcome, ChatCompletion)
    assert gate.in_flight(key_a) == 1 and gate.in_flight("openai") == 0
    # Org A's own call waits behind the held slot of its account.
    waiting = asyncio.ensure_future(rig.gateway.chat(token_a, {}, body_of(body)))
    await _queued(gate, key_a, waiting)
    assert not waiting.done() and gate.waiting(key_a) == 1
    held.release()
    assert isinstance(await asyncio.wait_for(waiting, timeout=2.0), ChatCompletion)


async def test_r11d_pg_6_a_route_without_an_upstream_key_falls_back_to_the_provider() -> None:
    gate = PriorityGate(1, floor_every=0, monotonic=ManualClock().monotonic)
    rig = GatewayRig(gate=gate)
    token = rig.token(rig.pin("run-1"))
    held = await gate.acquire("openai", "interactive")
    body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "stream": False}
    waiting = asyncio.ensure_future(rig.gateway.chat(token, {}, body_of(body)))
    await _queued(gate, "openai", waiting)
    assert not waiting.done()
    held.release()
    assert isinstance(await asyncio.wait_for(waiting, timeout=2.0), ChatCompletion)
