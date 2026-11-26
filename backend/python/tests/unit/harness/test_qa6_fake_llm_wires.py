"""QA6-W1, QA6-W2: the e2e lane's scripted model serves every wire of the options matrix to the provider's own client.

The model is the real server (``deployment/e2e/fake_llm/server.py``) on aiohttp's test server; each client is the
LangChain chat model the product builds for that provider (``app/utils/aimodels.py``), pointed at the server the way
the lane points the stack at it. A scripted turn makes one tool call and then answers, whole and streamed; a client
with the wrong credential is refused as the provider refuses it.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp.test_utils import TestServer
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from harness_core.testing.cloud_providers import service_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

SERVER = Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "fake_llm" / "server.py"
# The server reads its key from the environment once; another test module of the same process may have set it.
KEY = os.environ.setdefault("FAKE_LLM_API_KEY", "sk-qa6-test")
AWS = ("AKIAE2EFAKE0000000000", "e2e-fake-secret", "us-east-1")
SCRIPT = 'File it.\ntool create_ticket {"title": "T"}\nsay Filed it.'
# OPS01-FU-1: the lane's Vertex AI service account; the scripted model holds its public key, as Google does.
VERTEX_ACCOUNT = service_account("https://oauth2.googleapis.com/token", project_id="proj-qa6")
OTHER_ACCOUNT = service_account("https://oauth2.googleapis.com/token", project_id="proj-other")
os.environ.setdefault("FAKE_LLM_VERTEX_PUBLIC_KEY", VERTEX_ACCOUNT.public_key_pem.decode())
os.environ.setdefault("FAKE_LLM_VERTEX_CLIENT_EMAIL", VERTEX_ACCOUNT.client_email)


def _load() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "control")
    spec = importlib.util.spec_from_file_location("qa6_fake_llm_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


@tool
def mcp_e2esaas_create_ticket(title: str) -> str:
    """Create a ticket."""
    return "created"


def _chat(base: str, key: str) -> Any:  # noqa: ANN401 - a LangChain chat model
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="e2e-llm", api_key=key, base_url=f"{base}/v1", max_retries=0)


def _azure(base: str, key: str) -> Any:  # noqa: ANN401
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(azure_endpoint=base, api_key=key, api_version="2024-10-21", azure_deployment="e2e-llm",
                           max_retries=0)


def _responses(base: str, key: str) -> Any:  # noqa: ANN401
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-5-mini", api_key=key, base_url=f"{base}/v1", use_responses_api=True, max_retries=0)


def _anthropic(base: str, key: str) -> Any:  # noqa: ANN401
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-sonnet-4-5", api_key=key, base_url=base, max_tokens=1000, max_retries=0)


def _gemini(base: str, key: str) -> Any:  # noqa: ANN401
    from langchain_google_genai import ChatGoogleGenerativeAI

    os.environ["GOOGLE_GEMINI_BASE_URL"] = base
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=key, max_retries=0)


def _bedrock(base: str, key: str) -> Any:  # noqa: ANN401
    import boto3
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse

    secret = AWS[1] if key == KEY else "wrong-secret"
    client = boto3.Session(aws_access_key_id=AWS[0], aws_secret_access_key=secret, region_name=AWS[2]).client(
        "bedrock-runtime", endpoint_url=base, config=Config(retries={"max_attempts": 1}))
    return ChatBedrockConverse(model_id="anthropic.claude-3-5-sonnet-20240620-v1:0", client=client, region_name=AWS[2],
                               provider="anthropic", max_tokens=1000,
                               # Its control-plane client resolves credentials too; without them it asks instance metadata.
                               aws_access_key_id=AWS[0], aws_secret_access_key=secret)


def _vertex(base: str, key: str) -> Any:  # noqa: ANN401
    """The product's Vertex AI client (``aimodels``: Gemini on Vertex with the uploaded service account): its token
    request goes to the key file's own ``token_uri``, here the scripted model's; a wrong key is another account."""
    from google.oauth2 import service_account
    from langchain_google_genai import ChatGoogleGenerativeAI

    account = VERTEX_ACCOUNT if key == KEY else OTHER_ACCOUNT
    info = {**json.loads(account.json), "token_uri": f"{base}/token"}
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    os.environ["GOOGLE_VERTEX_BASE_URL"] = base
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", project="proj-qa6", location="us-central1",
                                  credentials=credentials, max_retries=0)


CLIENTS: dict[str, Callable[[str, str], Any]] = {
    "openai_chat": _chat, "azure_openai": _azure, "openai_responses": _responses,
    "anthropic_messages": _anthropic, "gemini": _gemini, "bedrock": _bedrock, "vertex_ai": _vertex,
}


@pytest.fixture
async def base() -> AsyncIterator[tuple[Any, str]]:
    model = server.FakeModel()
    test_server = TestServer(server.app(model))
    await test_server.start_server()
    previous = {name: os.environ.get(name) for name in ("GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL")}
    try:
        yield model, f"http://127.0.0.1:{test_server.port}"
    finally:
        await test_server.close()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _text(message: BaseMessage) -> str:
    return message.text if isinstance(message.text, str) else message.text()


async def _reply(llm: Any, messages: list[BaseMessage], *, stream: bool) -> AIMessage:  # noqa: ANN401
    if not stream:
        return await llm.ainvoke(messages)
    whole = None
    async for chunk in llm.astream(messages):
        whole = chunk if whole is None else whole + chunk
    assert whole is not None
    return whole


@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize("wire", sorted(CLIENTS))
async def test_qa6_w1_a_scripted_turn_calls_the_tool_then_answers_on_every_wire(
    base: tuple[Any, str], wire: str, stream: bool,
) -> None:
    model, url = base
    llm = CLIENTS[wire](url, KEY).bind_tools([mcp_e2esaas_create_ticket])
    first = await _reply(llm, [HumanMessage(SCRIPT)], stream=stream)
    assert [c["name"] for c in first.tool_calls] == ["mcp_e2esaas_create_ticket"]
    assert first.tool_calls[0]["args"] == {"title": "T"}
    result = ToolMessage("created TCK-1", tool_call_id=first.tool_calls[0]["id"] or "call", name="mcp_e2esaas_create_ticket")
    second = await _reply(llm, [HumanMessage(SCRIPT), first, result], stream=stream)
    assert "Filed it." in _text(second)
    assert {r.get("wire") for r in model.requests} == {wire}
    # Vertex AI signs in first; a sign-in is no model call.
    assert [r["kind"] for r in model.requests if not r.get("signIn")] == ["loop", "loop"]


@pytest.mark.parametrize("wire", sorted(CLIENTS))
async def test_qa6_w2_a_wrong_credential_is_refused_and_never_scripted(base: tuple[Any, str], wire: str) -> None:
    model, url = base
    with pytest.raises(Exception) as refused:
        await CLIENTS[wire](url, "sk-wrong").ainvoke([HumanMessage(SCRIPT)])
    assert any(code in str(refused.value) for code in ("401", "403", "400", "API key", "signature", "nvalid"))
    assert [r["status"] for r in model.requests] and all(r["status"] in (400, 401, 403) for r in model.requests)
    assert not model.brain.holds and all(r.get("kind") is None for r in model.requests)


async def test_qa6_w3_a_rewrite_keeps_the_request_and_other_steps_get_the_fixed_reply(base: tuple[Any, str]) -> None:
    """A goal or plan step offers only its own tool (``task_complete``): it must not spend the turn's directives."""
    model, url = base
    llm = CLIENTS["openai_chat"](url, KEY)

    @tool
    def task_complete(summary: str) -> str:
        """Finish the task."""
        return summary

    @tool
    def fetch_tools(names: str) -> str:
        """Fetch tools."""
        return names

    rewrite = await llm.bind_tools([task_complete]).ainvoke([HumanMessage(SCRIPT)])
    assert not rewrite.tool_calls and _text(rewrite) == f"{SCRIPT}\nquick", "the intent step keeps the request"
    step = await llm.bind_tools([fetch_tools]).ainvoke([HumanMessage(SCRIPT)])
    assert not step.tool_calls and _text(step) == "E2E stack model reply."
    loop = await llm.bind_tools([mcp_e2esaas_create_ticket, task_complete]).ainvoke([HumanMessage(SCRIPT)])
    assert [c["name"] for c in loop.tool_calls] == ["mcp_e2esaas_create_ticket"]
    assert [r["kind"] for r in model.requests] == ["rewrite", "other", "loop"]


async def test_qa6_w4_a_loop_that_repeats_the_task_runs_the_script_once(base: tuple[Any, str]) -> None:
    """React's step prompt repeats the task after each tool result; the directives still run once."""
    model, url = base
    llm = CLIENTS["openai_chat"](url, KEY).bind_tools([mcp_e2esaas_create_ticket])
    first = await llm.ainvoke([HumanMessage(SCRIPT)])
    result = ToolMessage("created", tool_call_id=first.tool_calls[0]["id"], name="mcp_e2esaas_create_ticket")
    second = await llm.ainvoke([HumanMessage(SCRIPT), first, result, HumanMessage(f"Continue the task:\n{SCRIPT}")])
    assert not second.tool_calls and _text(second) == "Filed it."
    quoted = HumanMessage(f"{SCRIPT}\n<original_query>{SCRIPT}\n</original_query>")
    third = await llm.ainvoke([quoted, first, result])
    assert not third.tool_calls and _text(third) == "Filed it.", "a goal that quotes the request runs it once"


@pytest.mark.parametrize("wire", ["anthropic_messages", "bedrock", "gemini", "openai_chat"])
async def test_qa6_w5_a_reviewer_step_gets_a_conforming_verdict(base: tuple[Any, str], wire: str) -> None:
    """A plan critique asks for structured output (a tool call or a JSON schema); the answer passes the review."""
    from pydantic import BaseModel

    class Verdict(BaseModel):
        passed: bool
        confidence: str
        issues: list[str]

    _model, url = base
    verdict = await CLIENTS[wire](url, KEY).with_structured_output(Verdict).ainvoke("Review this plan.")
    assert verdict is not None and verdict.passed is True and verdict.issues == []


async def test_qa6_w6_a_capability_is_called_under_the_tool_this_call_offers(base: tuple[Any, str]) -> None:
    """Code is ``run_code`` on one loop and the coding sub-agent on another; the rewrite keeps every alternative."""
    _model, url = base
    llm = CLIENTS["openai_chat"](url, KEY)

    @tool
    def coding_agent(goal: str) -> str:
        """Delegate a coding task."""
        return goal

    @tool
    def run_code(code: str) -> str:
        """Run code."""
        return code

    @tool
    def task_complete(summary: str) -> str:
        """Finish the task."""
        return summary

    either = 'Compute.\ntool run_code {"code": "1"} || coding_agent {"goal": "sub"}\nsay Done.'
    composed = await llm.bind_tools([coding_agent, mcp_e2esaas_create_ticket]).ainvoke([HumanMessage(either)])
    assert [(c["name"], c["args"]) for c in composed.tool_calls] == [("coding_agent", {"goal": "sub"})]
    flat = await llm.bind_tools([run_code, coding_agent]).ainvoke([HumanMessage(either)])
    assert [c["name"] for c in flat.tool_calls] == ["run_code"]
    rewrite = await llm.bind_tools([task_complete]).ainvoke([HumanMessage(either)])
    assert " || coding_agent" in _text(rewrite)


@pytest.mark.parametrize("shell", ["bash", "exec_command", "Bash"])
async def test_qa6_w7_an_engines_request_is_known_by_its_own_tools(base: tuple[Any, str], shell: str) -> None:
    """opencode, Codex and Claude Code name their shell differently; each is the engine's turn, not the loop's."""
    model, url = base
    body = {"model": "e2e-llm", "messages": [{"role": "user", "content": "Do it.\nshell echo hi\nsay Done."}],
            "tools": [{"type": "function", "function": {"name": shell, "parameters": {"type": "object"}}}]}
    import aiohttp

    async with aiohttp.ClientSession() as session, session.post(
        f"{url}/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {KEY}"}) as response:
        message = (await response.json())["choices"][0]["message"]
    assert [c["function"]["name"] for c in message["tool_calls"]] == [shell]
    assert [r["kind"] for r in model.requests] == ["engine"]


async def test_mcpe2e_w8_a_named_canary_in_a_model_request_is_reported_and_never_kept(base: tuple[Any, str]) -> None:
    """MCP-E2E CR-2: a test names what must never reach a model; a request carrying it is a hit, by digest only."""
    import aiohttp

    model, url = base
    control = {"X-E2E-Control": os.environ["FAKE_LLM_CONTROL_TOKEN"]}
    canary = "tok-canary-w8-0123456789"

    def body(text: str) -> dict[str, Any]:
        return {"model": "e2e-llm", "messages": [{"role": "user", "content": text}]}

    async with aiohttp.ClientSession() as session:
        assert (await session.post(f"{url}/__control/canaries", json={"values": [canary]})).status == 401
        watching = await session.post(f"{url}/__control/canaries", json={"values": [canary]}, headers=control)
        assert (await watching.json()) == {"watching": 1, "hits": []}
        auth = {"Authorization": f"Bearer {KEY}"}
        assert (await session.post(f"{url}/v1/chat/completions", json=body("nothing here"), headers=auth)).status == 200
        assert (await (await session.get(f"{url}/__control/canaries", headers=control)).json())["hits"] == []
        leaked = await session.post(f"{url}/v1/chat/completions", json=body(f"the tool said {canary}"), headers=auth)
        assert leaked.status == 200
        report = await (await session.get(f"{url}/__control/canaries", headers=control)).json()
    assert [h["path"] for h in report["hits"]] == ["/v1/chat/completions"]
    assert canary not in str(report) and canary not in str(model.requests)
