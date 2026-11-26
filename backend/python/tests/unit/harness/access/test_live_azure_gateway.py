"""E3 LV-1: one live streaming completion with a tool definition, Azure OpenAI through the gateway.

Runs only when the Azure test variables are set: in the environment, or in the
dotenv file ``HARNESS_LIVE_ENV_FILE`` names (``integration-tests/.env.local``),
which is exported into the environment for this test only. The values are never
printed; the test asserts they are not in the captured logs either.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest

from harness_core.access.types import ENGINE_MODEL_ID, PinnedRoute
from tests.unit.harness.access.support import start_stack

REQUIRED = ("TEST_AZURE_OPENAI_API_KEY", "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME")

pytestmark = pytest.mark.integration


def _dotenv(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name and not name.startswith("#"):
            values[name.removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    source = os.environ.get("HARNESS_LIVE_ENV_FILE")
    if source and Path(source).is_file():
        for name, value in _dotenv(source).items():
            if name.startswith("TEST_AZURE_OPENAI_") and value:
                monkeypatch.setenv(name, value)
    if not all(os.environ.get(v) for v in REQUIRED):
        pytest.skip("needs the Azure OpenAI test variables")


@pytest.mark.timeout(180)
@pytest.mark.usefixtures("live_env")
async def test_lv1_live_azure_stream_with_a_tool(caplog: pytest.LogCaptureFixture) -> None:
    import openai

    key = os.environ["TEST_AZURE_OPENAI_API_KEY"]
    deployment = os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"]
    model = os.environ.get("TEST_AZURE_OPENAI_MODEL") or deployment
    ai_models = {
        "llm": [
            {
                "provider": "azureOpenAI",
                "modelKey": "key-azure-live",
                # As the AI Models page writes a reasoning deployment (sampling and API shape follow it).
                "isReasoning": True,
                "isDefault": True,
                "configuration": {
                    "model": model,
                    "apiKey": key,
                    "endpoint": os.environ["TEST_AZURE_OPENAI_ENDPOINT"],
                    "deploymentName": deployment,
                },
            }
        ]
    }
    route = PinnedRoute("azureopenai", model, config_key="key-azure-live", config_model=model)
    caplog.set_level(logging.DEBUG)
    stack = await start_stack(ai_models, route)
    try:
        client = openai.AsyncOpenAI(api_key=stack.token, base_url=f"{stack.url}/v1", max_retries=0, timeout=120)
        stream = await client.chat.completions.create(
            model=ENGINE_MODEL_ID,
            messages=[
                {"role": "system", "content": "Answer in one short sentence, or call the tool if asked to."},
                {"role": "user", "content": "Call get_time for the city Paris."},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_time",
                        "description": "Current time in a city",
                        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                    },
                }
            ],
            stream=True,
            stream_options={"include_usage": True},
        )
        text, names, arguments, finish, usage = "", [], "", None, None
        async for chunk in stream:
            usage = chunk.usage or usage
            for choice in chunk.choices:
                finish = choice.finish_reason or finish
                text += choice.delta.content or ""
                for call in choice.delta.tool_calls or []:
                    if call.function and call.function.name:
                        names.append(call.function.name)
                    arguments += (call.function.arguments or "") if call.function else ""
        await client.close()
    finally:
        await stack.close()
    print(f"LV-1 finish={finish} tools={names} usage={usage.prompt_tokens if usage else None}/{usage.completion_tokens if usage else None}")
    assert text or names, "neither text nor a tool call came back"
    if names:
        assert names == ["get_time"] and "Paris" in arguments and finish == "tool_calls"
    assert usage is not None and usage.prompt_tokens > 0 and usage.completion_tokens > 0
    charged = [int(m) for m in re.findall(r"model_gateway\.call .*?costMicroUsd=(\d+)", caplog.text)]
    assert charged and charged[0] > 0
    assert key not in caplog.text and stack.token not in caplog.text
