"""E3b LV-2: one live streaming Responses call with a function tool, Azure OpenAI through the gateway.

Runs only when the Azure test variables are set (E3's ``live_env``: the
environment, or the dotenv file ``HARNESS_LIVE_ENV_FILE`` names, exported for
this test only). The values are never printed and are asserted absent from the logs.
"""

from __future__ import annotations

import logging
import os
import re

import pytest

from harness_core.access.types import ENGINE_MODEL_ID, PinnedRoute
from tests.unit.harness.access.support import start_stack
from tests.unit.harness.access.test_live_azure_gateway import (
    live_env,  # noqa: F401 - the fixture
)

pytestmark = pytest.mark.integration


@pytest.mark.timeout(180)
@pytest.mark.usefixtures("live_env")
async def test_lv2_live_azure_responses_stream_with_a_tool(caplog: pytest.LogCaptureFixture) -> None:
    import openai

    key = os.environ["TEST_AZURE_OPENAI_API_KEY"]
    deployment = os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"]
    model = os.environ.get("TEST_AZURE_OPENAI_MODEL") or deployment
    ai_models = {
        "llm": [
            {
                "provider": "azureOpenAI",
                "modelKey": "key-azure-live",
                # As the AI Models page writes a reasoning deployment: the transport speaks Responses to Azure.
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
        async with client.responses.stream(
            model=ENGINE_MODEL_ID,
            instructions="Answer in one short sentence, or call the tool if asked to.",
            input=[{"role": "user", "content": "Call get_time for the city Paris."}],
            tools=[
                {
                    "type": "function",
                    "name": "get_time",
                    "description": "Current time in a city",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                    "strict": False,
                }
            ],
            store=False,
            include=["reasoning.encrypted_content"],
        ) as stream:
            kinds = [event.type async for event in stream]
            final = await stream.get_final_response()
        await client.close()
    finally:
        await stack.close()
    calls = [item for item in final.output if item.type == "function_call"]
    usage = final.usage
    print(f"LV-2 status={final.status} events={len(kinds)} calls={[c.name for c in calls]} "
          f"usage={usage.input_tokens if usage else None}/{usage.output_tokens if usage else None}")
    assert kinds[0] == "response.created" and kinds[-1] == "response.completed"
    assert final.output_text or calls, "neither text nor a function call came back"
    if calls:
        assert [c.name for c in calls] == ["get_time"] and "Paris" in calls[0].arguments
    assert usage is not None and usage.input_tokens > 0 and usage.output_tokens > 0
    charged = [int(m) for m in re.findall(r"model_gateway\.call .*?costMicroUsd=(\d+)", caplog.text)]
    assert charged and charged[0] > 0
    assert "api=responses" in caplog.text
    # Both SDK clients log their request path: the engine's to the gateway and the transport's to Azure.
    # Only `/responses` means Azure was asked through the Responses API, not chat completions.
    sent = [r.getMessage() for r in caplog.records if r.name == "openai._base_client" and r.getMessage().startswith("Request options")]
    assert sorted(set(re.findall(r"'url': '([^']+)'", " ".join(sent)))) == ["/responses"]
    assert key not in caplog.text and stack.token not in caplog.text
