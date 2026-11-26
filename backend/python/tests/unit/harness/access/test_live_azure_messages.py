"""E3c LV-3: one live streaming Messages call with a tool, Azure OpenAI through the gateway.

The ``anthropic`` SDK speaks Messages to the token-authenticated gateway, which
reaches Azure through chat completions (the Messages → chat translation path) or,
with the deployment configured as a reasoning model, through Responses. Runs only
when the Azure test variables are set (E3's ``live_env``: the environment, or the
dotenv file ``HARNESS_LIVE_ENV_FILE`` names, exported for this test only). The
values are never printed and are asserted absent from the logs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID, ModelApi, PinnedRoute
from tests.unit.harness.access.support import start_stack
from tests.unit.harness.access.test_live_azure_gateway import (
    live_env,  # noqa: F401 - the fixture
)

pytestmark = pytest.mark.integration

# The run's spend ceiling for this smoke: one short call costs well under a cent.
MAX_COST_MICRO_USD = 1_000_000

# chat: not marked reasoning, so the transport speaks chat completions. The deployment here is a reasoning
# model, which accepts only the default temperature and refuses function tools on chat completions unless
# reasoning is off; the request asks effort "none" (the wire's output_config.effort). responses: marked
# reasoning as the AI Models page writes it, with Claude Code's own effort.
UPSTREAMS: dict[str, tuple[bool, dict[str, Any], str, str]] = {
    "chat": (False, {"temperature": 1}, "none", "/chat/completions"),
    "responses": (True, {}, "high", "/responses"),
}


@pytest.mark.timeout(180)
@pytest.mark.usefixtures("live_env")
@pytest.mark.parametrize("upstream", sorted(UPSTREAMS))
async def test_lv3_live_azure_messages_stream_with_a_tool(
    upstream: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anthropic

    import app.model_gateway_main as role

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # No released engine speaks Messages before E9's Claude Code adapter, so the guard refuses every
    # Messages call (model_api_not_permitted); here it knows the adapter E9 will release.
    released = role.release_model_apis()
    monkeypatch.setattr(role, "release_model_apis", lambda: {**released, "claude_code": frozenset({ModelApi.ANTHROPIC_MESSAGES})})
    reasoning, configured, effort, path = UPSTREAMS[upstream]
    key = os.environ["TEST_AZURE_OPENAI_API_KEY"]
    deployment = os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"]
    model = os.environ.get("TEST_AZURE_OPENAI_MODEL") or deployment
    ai_models = {
        "llm": [
            {
                "provider": "azureOpenAI",
                "modelKey": "key-azure-live",
                "isReasoning": reasoning,
                "isDefault": True,
                "configuration": {
                    "model": model,
                    **configured,
                    "apiKey": key,
                    "endpoint": os.environ["TEST_AZURE_OPENAI_ENDPOINT"],
                    "deploymentName": deployment,
                },
            }
        ]
    }
    route = PinnedRoute("azureopenai", model, config_key="key-azure-live", config_model=model)
    caplog.set_level(logging.DEBUG)
    stack = await start_stack(ai_models, route, engine="claude_code")
    try:
        client = anthropic.AsyncAnthropic(auth_token=stack.token, base_url=stack.url, max_retries=0, timeout=120)
        async with client.messages.stream(
            model=ENGINE_MODEL_ID,
            max_tokens=256,
            system="Answer in one short sentence, or call the tool if asked to.",
            messages=[{"role": "user", "content": "Call get_time for the city Paris."}],
            tools=[
                {
                    "name": "get_time",
                    "description": "Current time in a city",
                    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                }
            ],
            extra_headers={"anthropic-beta": "claude-code-20250219,effort-2025-11-24"},
            extra_body={"output_config": {"effort": effort}},
        ) as stream:
            kinds = [event.type async for event in stream]
            final = await stream.get_final_message()
        await client.close()
    finally:
        await stack.close()
    calls = [b for b in final.content if b.type == "tool_use"]
    text = "".join(b.text for b in final.content if b.type == "text")
    usage = final.usage
    charged = [int(m) for m in re.findall(r"model_gateway\.call .*?costMicroUsd=(\d+)", caplog.text)]
    print(f"LV-3 {upstream} stop={final.stop_reason} events={len(kinds)} calls={[c.name for c in calls]} "
          f"usage={usage.input_tokens}/{usage.output_tokens} costMicroUsd={charged}")
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop"
    assert final.model == ENGINE_MODEL_ID and (text or calls), "neither text nor a tool call came back"
    if calls:
        assert [c.name for c in calls] == ["get_time"] and "Paris" in str(calls[0].input)
        assert final.stop_reason == "tool_use"
    assert usage.input_tokens > 0 and usage.output_tokens > 0
    assert charged and 0 < charged[0] < MAX_COST_MICRO_USD
    assert "api=anthropic_messages" in caplog.text
    # The transport's own SDK logs its request path: which Azure API the Messages call became.
    sent = [r.getMessage() for r in caplog.records if r.name == "openai._base_client" and r.getMessage().startswith("Request options")]
    assert sorted(set(re.findall(r"'url': '([^']+)'", " ".join(sent)))) == [path]
    assert key not in caplog.text and stack.token not in caplog.text


@pytest.mark.timeout(300)
@pytest.mark.usefixtures("live_env")
@pytest.mark.skipif(not os.environ.get("HARNESS_CLAUDE_CODE_BIN"), reason="HARNESS_CLAUDE_CODE_BIN names no Claude Code binary")
async def test_cc3_the_claude_code_binary_runs_a_tool_on_azure_through_the_gateway(
    tmp_path: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch  # noqa: ANN401
) -> None:
    import asyncio
    import json

    import app.model_gateway_main as role

    released = role.release_model_apis()
    monkeypatch.setattr(role, "release_model_apis", lambda: {**released, "claude_code": frozenset({ModelApi.ANTHROPIC_MESSAGES})})
    key = os.environ["TEST_AZURE_OPENAI_API_KEY"]
    deployment = os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"]
    model = os.environ.get("TEST_AZURE_OPENAI_MODEL") or deployment
    ai_models = {"llm": [{
        "provider": "azureOpenAI", "modelKey": "key-azure-live", "isReasoning": True, "isDefault": True,
        "configuration": {"model": model, "apiKey": key, "endpoint": os.environ["TEST_AZURE_OPENAI_ENDPOINT"],
                          "deploymentName": deployment},
    }]}
    route = PinnedRoute("azureopenai", model, config_key="key-azure-live", config_model=model)
    caplog.set_level(logging.INFO)
    stack = await start_stack(ai_models, route, engine="claude_code")
    work = tmp_path / "work"
    work.mkdir()
    try:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
            "ANTHROPIC_BASE_URL": stack.url,
            "ANTHROPIC_AUTH_TOKEN": stack.token,
            "ANTHROPIC_MODEL": ENGINE_MODEL_ID,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": ENGINE_MODEL_ID,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        }
        process = await asyncio.create_subprocess_exec(
            os.environ["HARNESS_CLAUDE_CODE_BIN"], "-p",
            "Use the Bash tool to run exactly: echo e3c-live-ok. Then reply with only its output.",
            "--permission-mode", "default", "--allowedTools", "Bash(echo e3c-live-ok)", "--output-format", "json",
            "--max-turns", "4",
            cwd=work, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(process.communicate(), timeout=240)
    finally:
        await stack.close()
    result = json.loads(out.decode().strip().splitlines()[-1])
    charged = [int(m) for m in re.findall(r"model_gateway\.call .*?costMicroUsd=(\d+)", caplog.text)]
    refused = re.findall(r"model_gateway\.refused .*?reason=(\w+)", caplog.text)
    print(f"CC-3 exit={process.returncode} is_error={result.get('is_error')} calls={len(charged)} "
          f"costMicroUsd={sum(charged)} refused={refused} result={str(result.get('result'))[:80]!r}")
    assert process.returncode == 0 and result["is_error"] is False
    assert "e3c-live-ok" in str(result["result"])
    assert len(charged) >= 2 and 0 < sum(charged) < MAX_COST_MICRO_USD and refused == []
    assert key not in caplog.text and stack.token not in caplog.text
