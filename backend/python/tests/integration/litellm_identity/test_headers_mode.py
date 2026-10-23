"""Identity forwarded as LiteLLM headers, against a real proxy.

    pytest tests/integration/litellm_identity -m docker --timeout=900

Set ``LITELLM_TEST_VERSIONS=v1.106.0,v1.103.5`` to run the matrix.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import httpx
import pytest

from app.services.model_identity.call_context import ModelCallContext, bind_call_context
from app.services.model_identity.errors import GatewayPolicyKind, classify_gateway_error
from app.services.model_identity.groups import GroupMembership, GroupsHandle
from app.services.model_identity.principal import service_principal, user_principal
from app.services.model_identity.settings import ConnectionForwardingSettings
from app.services.model_identity.transport.instrument import instrument_http_client
from app.services.model_identity.workload import Workload

from .harness import (
    CALL_COST,
    LiteLLMStack,
    docker_ready,
    litellm_stack,
    litellm_versions,
)

if TYPE_CHECKING:
    from app.services.model_identity.principal import Principal

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.docker, pytest.mark.integration, pytest.mark.timeout(900, method="thread")]

ORG = "org-1"


@pytest.fixture(scope="module", params=litellm_versions())
def stack(request: pytest.FixtureRequest) -> Iterator[LiteLLMStack]:
    if "docker" not in (request.config.option.markexpr or ""):
        pytest.skip("starts a LiteLLM container: run with -m docker")
    ready, reason = docker_ready()
    if not ready:
        pytest.skip(reason)
    with litellm_stack(request.param) as running:
        yield running


@pytest.fixture(scope="module")
def virtual_key(stack: LiteLLMStack) -> str:
    """What PipesHub holds: one key that may call both models."""
    response = stack.admin(
        "POST", "/key/generate", json={"models": ["fake-chat", "fake-restricted"], "key_alias": "pipeshub"}
    )
    return response.json()["key"]


def person(name: str) -> Principal:
    return user_principal({"userId": f"u-{name}-{uuid.uuid4().hex[:6]}", "email": f"{name}@example.com"}, ORG)


def context(principal, workload=Workload.CHAT, groups=()) -> ModelCallContext:
    return ModelCallContext(
        principal=principal,
        workload=workload,
        groups=GroupsHandle.resolved(GroupMembership(ids=tuple(groups), source="test", complete=True)),
    )


def forwarding_client(stack: LiteLLMStack, key: str, **settings) -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        base_url=stack.base_url, headers={"authorization": f"Bearer {key}"}, timeout=60
    )
    assert instrument_http_client(
        client, ConnectionForwardingSettings(enabled=True, mode="headers", preset="litellm", **settings)
    )
    return client


async def chat(client: httpx.AsyncClient, ctx: ModelCallContext, model: str = "fake-chat") -> httpx.Response:
    with bind_call_context(ctx):
        return await client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
        )


async def test_spend_lands_on_the_person_who_asked(stack, virtual_key) -> None:
    alice, bob = person("alice"), person("bob")
    async with forwarding_client(stack, virtual_key) as client:
        assert (await chat(client, context(alice))).status_code == 200
        assert (await chat(client, context(alice))).status_code == 200
        assert (await chat(client, context(bob))).status_code == 200

    assert stack.wait_for(lambda: stack.customer_spend(alice.subject_id) >= 2 * CALL_COST - 1e-9)
    assert stack.customer_spend(alice.subject_id) == pytest.approx(2 * CALL_COST)
    assert stack.customer_spend(bob.subject_id) == pytest.approx(CALL_COST)


async def test_a_person_over_their_budget_is_refused_and_the_refusal_is_classified(stack, virtual_key) -> None:
    capped, other = person("capped"), person("other")
    stack.make_customer(capped.subject_id, max_budget=CALL_COST / 2)
    async with forwarding_client(stack, virtual_key) as client:
        assert (await chat(client, context(capped))).status_code == 200
        assert stack.wait_for(lambda: stack.customer_spend(capped.subject_id) > 0)

        refused = await chat(client, context(capped))
        assert refused.status_code >= 400
        policy = classify_gateway_error(refused.status_code, refused.content, refused.headers)
        assert policy is not None, refused.text
        assert policy.kind is GatewayPolicyKind.BUDGET
        assert policy.retryable is False

        # Another person on the same key is unaffected.
        assert (await chat(client, context(other))).status_code == 200


async def test_a_group_over_its_tag_budget_is_refused(stack, virtual_key) -> None:
    group = f"g-{uuid.uuid4().hex[:6]}"
    stack.admin("POST", "/tag/new", json={"name": f"group:{group}", "max_budget": CALL_COST / 2})
    member, outsider = person("member"), person("outsider")
    async with forwarding_client(stack, virtual_key) as client:
        assert (await chat(client, context(member, groups=[group]))).status_code == 200
        assert stack.wait_for(lambda: _tag_spend(stack, group) > 0)

        refused = await chat(client, context(member, groups=[group]))
        assert refused.status_code >= 400
        policy = classify_gateway_error(refused.status_code, refused.content, refused.headers)
        assert policy is not None and policy.kind is GatewayPolicyKind.BUDGET

        # Someone outside the group is not charged to it.
        assert (await chat(client, context(outsider))).status_code == 200


def _tag_spend(stack: LiteLLMStack, group: str) -> float:
    rows = stack.admin("GET", "/spend/tags").json()
    return sum(float(r.get("total_spend") or 0) for r in rows if r.get("individual_request_tag") == f"group:{group}")


async def test_background_work_is_charged_to_the_service_not_a_person(stack, virtual_key) -> None:
    service = service_principal("indexing", ORG)
    async with forwarding_client(stack, virtual_key) as client:
        assert (await chat(client, context(service, Workload.INDEXING))).status_code == 200

    assert stack.wait_for(lambda: stack.customer_spend("svc:pipeshub:indexing") > 0)


async def test_the_provider_never_sees_the_identity_headers_it_has_no_use_for(stack, virtual_key) -> None:
    alice = person("alice")
    before = len(stack.provider.requests)
    async with forwarding_client(stack, virtual_key) as client:
        assert (await chat(client, context(alice))).status_code == 200
    seen = stack.provider.requests[before:]
    assert seen, "the provider received nothing"
    assert alice.subject_id not in str(seen[0]["headers"].get("authorization", ""))
