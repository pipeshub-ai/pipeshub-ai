"""E3 PA-6, PA-7: the ``app.model_gateway_main`` wiring, end to end over HTTP, and its process role."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID, ROLE_MODEL_IDS, PinnedRoute
from tests.support.loopback_client import loopback_client
from tests.unit.harness.access.support import start_stack

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

REPO = Path(__file__).resolve().parents[6]
ORG_KEY = "org-compatible-key-not-real-0123456789"


@pytest.fixture
async def provider() -> AsyncIterator[tuple[list[dict[str, Any]], str]]:
    seen: list[dict[str, Any]] = []

    async def chat(request: web.Request) -> web.Response:
        body = await request.json()
        seen.append({"headers": dict(request.headers), "body": body})
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


async def test_pa6_the_role_serves_a_run_end_to_end(provider: tuple[list[dict[str, Any]], str]) -> None:
    seen, upstream = provider
    ai_models = {
        "llm": [
            {
                "provider": "openAICompatible",
                "modelKey": "key-compat",
                "isDefault": False,
                "configuration": {"model": "local-model", "apiKey": ORG_KEY, "endpoint": upstream},
            }
        ]
    }
    route = PinnedRoute("openaicompatible", "local-model", config_key="key-compat", config_model="local-model")
    stack = await start_stack(ai_models, route)
    try:
        async with loopback_client() as client:
            health = await client.get(f"{stack.url}/healthz")
            assert health.status_code == 200
            auth = {"Authorization": f"Bearer {stack.token}", "x-opencode-session-id": "ses_9"}
            models = await client.get(f"{stack.url}/v1/models", headers=auth)
            assert [m["id"] for m in models.json()["data"]] == list(ROLE_MODEL_IDS.values())
            answer = await client.post(
                f"{stack.url}/v1/chat/completions",
                headers=auth,
                json={"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "ping"}]},
            )
            assert answer.status_code == 200, answer.text
            assert answer.json()["choices"][0]["message"]["content"] == "pong"
            refused = await client.post(
                f"{stack.url}/v1/chat/completions",
                headers=auth,
                json={"model": "local-model", "messages": [{"role": "user", "content": "ping"}]},
            )
            assert refused.status_code == 404
    finally:
        await stack.close()
    assert len(seen) == 1
    assert seen[0]["body"]["model"] == "local-model"
    assert seen[0]["headers"]["Authorization"] == f"Bearer {ORG_KEY}"
    assert "ses_9" not in json.dumps(seen[0])
    assert stack.node.asked == 1  # the grant is cached for the run's later calls
    # P6.5: one reservation before the provider call, one commit after; the refused call reserved nothing.
    reserve, commit = stack.node.budget.calls
    assert reserve == "POST budget/reservations"
    assert re.fullmatch(r"POST budget/reservations/chatcmpl_[0-9a-f]+/commit", commit)


async def test_pa9_the_role_binds_the_node_ledger_and_its_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.access.budget import NodeBudgetLedger
    from app.model_gateway_main import (
        ENV_ALLOWANCE,
        ENV_DEFAULT_ALLOWANCE,
        create_gateway,
        output_allowance,
    )
    from harness_core.access.spend import OutputAllowance
    from tests.unit.harness.access.support import StackConfig

    assert output_allowance() == OutputAllowance()
    monkeypatch.setenv(ENV_ALLOWANCE, "8000")
    monkeypatch.setenv(ENV_DEFAULT_ALLOWANCE, "4096")
    assert output_allowance() == OutputAllowance(default_tokens=4096, cap_tokens=8000)
    gateway = await create_gateway(StackConfig({}, {}), node_url="http://127.0.0.1:9")  # type: ignore[arg-type]
    assert isinstance(gateway._budget, NodeBudgetLedger)  # pyright: ignore[reportPrivateUsage]
    assert gateway._legacy is None  # pyright: ignore[reportPrivateUsage]  # no Redis: nothing to carry over
    assert gateway._allowance == OutputAllowance(default_tokens=4096, cap_tokens=8000)  # pyright: ignore[reportPrivateUsage]


def test_pa7_the_process_monitor_starts_the_role_with_the_worker() -> None:
    dockerfile = (REPO / "deployment" / "process_monitor.sh").read_text()
    assert "(use_own_pg_login connectors; PIPESHUB_SERVICE=model_gateway exec python -m app.model_gateway_main) &" in dockerfile
    assert 'env_flag MODEL_GATEWAY_ENABLED "$follows"' in dockerfile  # the parse itself: test_flag_matrix FB-3
    assert 'if model_gateway_enabled; then\n    start_model_gateway' in dockerfile
    assert 'check_process "$MODEL_GATEWAY_PID" "Model gateway"' in dockerfile
    compose = (REPO / "deployment" / "docker-compose" / "docker-compose.yml").read_text()
    assert "MODEL_GATEWAY_BACKEND=http://model-gateway:8096" in compose
    assert "MODEL_GATEWAY_ENABLED=${MODEL_GATEWAY_ENABLED:-}" in compose
    from harness_core.gateway.model_server import MODEL_GATEWAY_PORT

    assert MODEL_GATEWAY_PORT == 8096


FOOTPRINT_PROBE = """
import asyncio, sys
from app.model_gateway_main import create_gateway, skip_unused_tokenizer
from harness_core.access.types import ModelApi, ModelNeed, PinnedRoute
skip_unused_tokenizer()

class Config:
    async def get_config(self, key, default=None, use_cache=False, **_):
        if key.endswith("aiModels"):
            entry = {"provider": "azureOpenAI", "modelKey": "k", "isReasoning": True, "configuration": {
                "model": "gpt-5.4-mini", "apiKey": "x" * 32, "endpoint": "https://example.openai.azure.com",
                "deploymentName": "d"}}
            return {"llm": [entry]}
        return default
    async def get_redis_config(self):
        raise RuntimeError("none")

async def main():
    gateway = await create_gateway(Config(), node_url="http://127.0.0.1:9")
    route = PinnedRoute("azureopenai", "gpt-5.4-mini", config_key="k", config_model="gpt-5.4-mini")
    await gateway._resolver.resolve("org", route, need=ModelNeed("user-1", "opencode", ModelApi.CHAT_COMPLETIONS))

asyncio.run(main())
print(sorted(m for m in ("torch", "transformers") if sys.modules.get(m) is not None))
"""


def test_fb3_the_role_resolves_a_route_without_torch() -> None:
    # The resolver's imports, without a Node to answer the live layer the pin would read (the source of an org's
    # narrowing imports nothing heavy either way).
    done = subprocess.run(
        [sys.executable, "-c", FOOTPRINT_PROBE],
        env={**os.environ, "CONTROL_PLANE_PIN": "shadow"},
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[4],
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "[]"
