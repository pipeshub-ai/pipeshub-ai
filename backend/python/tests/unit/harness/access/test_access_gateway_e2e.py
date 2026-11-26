"""A1 GW, OF: the real HTTP model gateway, wired as ``app.model_gateway_main`` wires it, refuses prohibited routes.

The gateway listens on 127.0.0.1 with the shipped signed vendor rules, Node is the
fake that answers model grants for the run's own token (``support.FakeNode``), the
provider is an HTTP server that records every request it receives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.harness.access.offering import (
    AGENT_HARNESS_OFFERING,
    InvalidOffering,
    parse_offering,
)
from harness_core.access.types import (
    ENGINE_MODEL_ID,
    AccountKind,
    AccountRef,
    PinnedRoute,
    user_account_ref,
)
from harness_engines import vendor_rules as release
from tests.support.loopback_client import loopback_client
from tests.unit.harness.access.support import StackConfig, start_stack

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

ORG_KEY = "org-compatible-key-not-real-0123456789"
CHAT = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "ping"}]}


@pytest.fixture
async def provider() -> AsyncIterator[tuple[list[dict[str, Any]], str]]:
    seen: list[dict[str, Any]] = []

    async def chat(request: web.Request) -> web.Response:
        body = await request.json()
        seen.append(body)
        return web.json_response(
            {
                "id": "up",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield seen, str(server.make_url("/v1"))
    await server.close()


def _models(upstream: str) -> dict[str, Any]:
    return {
        "llm": [
            {
                "provider": "openAICompatible",
                "modelKey": "key-compat",
                "isDefault": True,
                "configuration": {"model": "local-model", "apiKey": ORG_KEY, "endpoint": upstream},
            }
        ]
    }


ORG_ROUTE = PinnedRoute("openaicompatible", "local-model", config_key="key-compat", config_model="local-model")


async def _call(url: str, token: str) -> httpx.Response:
    async with loopback_client() as client:
        return await client.post(f"{url}/v1/chat/completions", headers={"Authorization": f"Bearer {token}"}, json=CHAT)


async def test_gw1_an_allowed_route_is_served(provider: tuple[list[dict[str, Any]], str]) -> None:
    seen, upstream = provider
    stack = await start_stack(_models(upstream), ORG_ROUTE)
    try:
        answer = await _call(stack.url, stack.token)
    finally:
        await stack.close()
    assert answer.status_code == 200, answer.text
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("route", "engine", "status", "code"),
    [
        (
            PinnedRoute("openai", "gpt-x", account=user_account_ref("user-2", "openAI")),
            "opencode",
            403,
            "account_not_owned",
        ),
        (ORG_ROUTE, "codex", 403, "model_api_not_permitted"),
        (ORG_ROUTE, "claude_code", 403, "model_api_not_permitted"),
        (ORG_ROUTE, "jcode", 403, "model_api_not_permitted"),
        (
            PinnedRoute("openai", "gpt-x", account=AccountRef(AccountKind.ORG_CLOUD, "cloud/acc-1")),
            "opencode",
            403,
            "vendor_rule_missing",
        ),
    ],
    ids=["another-users-key", "codex-on-chat", "claude-code-on-chat", "unreleased-engine", "no-vendor-row"],
)
async def test_gw2_a_prohibited_route_is_refused_before_the_provider(
    provider: tuple[list[dict[str, Any]], str], route: PinnedRoute, engine: str, status: int, code: str
) -> None:
    seen, upstream = provider
    stack = await start_stack(_models(upstream), route, engine=engine)
    try:
        answer = await _call(stack.url, stack.token)
    finally:
        await stack.close()
    assert (answer.status_code, answer.headers.get("X-Gateway-Refusal")) == (status, code), answer.text
    assert answer.json()["error"]["code"] == code
    assert seen == []


async def test_gw2_an_org_narrowing_applies_to_a_running_runs_next_call(
    provider: tuple[list[dict[str, Any]], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model-access document's path (the pin in shadow), read at every call; under the pin the narrowing comes
    from the live layer within its bound (MAC-G2, ``test_cpd6_gateway_policies.py``)."""
    monkeypatch.setenv("CONTROL_PLANE_PIN", "shadow")
    seen, upstream = provider
    stack = await start_stack(_models(upstream), ORG_ROUTE)
    try:
        first = await _call(stack.url, stack.token)
        stack.config.model_access = {
            "orgs": {
                "org-1": {
                    "disabledRules": ["openaicompatible-api-key-org"],
                    "consumerPlans": False,
                    "updatedAt": 1,
                    "updatedBy": "p_admin",
                }
            }
        }
        second = await _call(stack.url, stack.token)
    finally:
        await stack.close()
    assert first.status_code == 200
    assert (second.status_code, second.headers.get("X-Gateway-Refusal")) == (403, "org_policy_refused")
    assert len(seen) == 1


async def test_gw3_tampered_release_data_refuses_every_call(
    provider: tuple[list[dict[str, Any]], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen, upstream = provider
    shipped = release.packaged

    def tampered(name: str) -> bytes | None:
        raw = shipped(name)
        return raw.replace(b'"prohibited"', b'"allowed"   ', 1) if name == release.RULES_RESOURCE and raw else raw

    monkeypatch.setattr(release, "packaged", tampered)
    release._release.cache_clear()  # pyright: ignore[reportPrivateUsage]
    stack = await start_stack(_models(upstream), ORG_ROUTE)
    try:
        answers = [await _call(stack.url, stack.token) for _ in range(2)]
    finally:
        await stack.close()
        release._release.cache_clear()  # pyright: ignore[reportPrivateUsage]
    for answer in answers:
        assert (answer.status_code, answer.headers.get("X-Gateway-Refusal")) == (503, "vendor_rules_invalid")
    assert seen == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "managed"), ("", "managed"), ("  OSS ", "oss"), (" Managed", "managed")],
)
def test_of1_the_offering_flag(raw: str | None, expected: str) -> None:
    assert parse_offering(raw) == expected


async def test_of1_an_unknown_offering_stops_the_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.model_gateway_main import create_gateway

    monkeypatch.setenv(AGENT_HARNESS_OFFERING, "enterprise")
    with pytest.raises(InvalidOffering):
        await create_gateway(StackConfig({}, {}), node_url="http://127.0.0.1:9")  # type: ignore[arg-type]
