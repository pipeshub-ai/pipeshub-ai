"""E3c MH-1..MH-5 and CC-2: ``/v1/messages`` over real HTTP, with the ``anthropic`` SDK, Claude Code's
recorded requests, real transports, and (when ``HARNESS_CLAUDE_CODE_BIN`` names it) the Claude Code binary.

The upstream provider is E3/E3b's fake HTTP server, so the transports under the
gateway are the real SDK ones.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID, RunBudget
from harness_core.budget.types import Amount
from harness_core.gateway.model_server import REFUSAL_HEADER, create_model_gateway_app
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.azure_openai import (
    AzureOpenAITransport,
    RequestDefaults,
)
from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    reply_events,
)
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_messages_wire import (
    assemble,
    claude_code_request,
    recorded,
)
from tests.unit.harness_core.gateway.test_model_gateway import account as account_ref
from tests.unit.harness_core.gateway.test_model_gateway import prespend
from tests.unit.harness_core.gateway.test_model_server import ORG_KEY
from tests.unit.harness_core.gateway.test_responses_server import ResponsesProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    import httpx

    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.models.transports.base import LLMTransport

TOOL = {"name": "write", "description": "Write a file", "input_schema": {"type": "object", "properties": {"filePath": {"type": "string"}}}}


class Gateway:
    """The gateway app on 127.0.0.1 over a rig, counting the HTTP requests it answered."""

    def __init__(self, rig: GatewayRig, transport: LLMTransport | None = None, **pin: Any) -> None:  # noqa: ANN401
        self.rig = rig
        self.binding = rig.pin(transport=transport, **pin)
        self.token = rig.token(self.binding)
        self.requests = 0
        app = create_model_gateway_app(rig.gateway)

        @web.middleware
        async def count(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
            self.requests += 1
            return await handler(request)

        app.middlewares.append(count)
        self.server = TestServer(app, host="127.0.0.1")
        self.url = ""

    async def __aenter__(self) -> Gateway:
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.server.close()


@pytest.fixture
def no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SDK sends ANTHROPIC_API_KEY as x-api-key, which the gateway refuses as a caller credential.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
async def provider() -> AsyncIterator[tuple[ResponsesProvider, str]]:
    fake = ResponsesProvider()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


def _client(gateway: Gateway, *, retries: int = 0) -> Any:  # noqa: ANN401
    import anthropic

    return anthropic.AsyncAnthropic(auth_token=gateway.token, base_url=gateway.url, max_retries=retries)


async def _events(url: str, token: str, body: dict[str, Any], headers: dict[str, str], path: str = "/v1/messages") -> list[dict[str, Any]]:
    """The SSE frames as sent: each ``event:`` line names its ``data:`` payload's type."""
    sent = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "host", "content-length")}
    async with loopback_client() as client, client.stream(
        "POST", f"{url}{path}", json=body, headers={**sent, "Authorization": f"Bearer {token}"}
    ) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = (await response.aread()).decode()
    events: list[dict[str, Any]] = []
    for frame in (f for f in raw.split("\n\n") if f.strip()):
        name, data = frame.split("\n")
        event = json.loads(data.removeprefix("data: "))
        assert name == f"event: {event['type']}"
        events.append(event)
    return events


@pytest.mark.usefixtures("no_ambient_key")
async def test_mh1_the_anthropic_sdk_reads_both_answers(provider: tuple[ResponsesProvider, str]) -> None:
    _, upstream = provider
    transport = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    async with Gateway(GatewayRig(), transport) as gateway:
        client = _client(gateway)
        ask: dict[str, Any] = {"model": ENGINE_MODEL_ID, "max_tokens": 256, "tools": [TOOL],
                               "messages": [{"role": "user", "content": "go"}]}
        whole = await client.messages.create(**ask)
        async with client.messages.stream(**ask) as stream:
            kinds = [event.type async for event in stream]
            final = await stream.get_final_message()
        await client.close()
    for message in (whole, final):
        assert message.model == ENGINE_MODEL_ID and message.stop_reason == "tool_use"
        assert [b.type for b in message.content] == ["text", "tool_use"]
        assert message.content[0].text == "Writing files."  # type: ignore[union-attr]
        assert (message.content[1].name, message.content[1].input) == ("write", {"filePath": "a.txt"})  # type: ignore[union-attr]
        assert (message.usage.input_tokens, message.usage.cache_read_input_tokens) == (20, 100)
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop"
    def shape(message: Any) -> dict[str, Any]:  # noqa: ANN401
        dumped: dict[str, Any] = message.model_dump(exclude={"id"}, exclude_none=True)
        for block in dumped["content"]:
            block.pop("parsed_output", None)  # the stream helper's own annotation
        return dumped

    assert shape(whole) == shape(final)


async def test_mh2_claude_codes_requests_replay_over_http() -> None:
    rig = GatewayRig()
    async with Gateway(rig) as gateway:
        for name in ("first_turn", "tool_round_trip"):
            entry = next(r for r in recorded()["requests"] if r["name"] == name)
            body, headers = claude_code_request(name)
            events = await _events(gateway.url, gateway.token, body, headers, path=entry["path"])
            assembled = assemble(events)
            assert assembled["content"] == [{"type": "text", "text": "hello"}]
            assert assembled["stop_reason"] == "end_turn" and assembled["model"] == ENGINE_MODEL_ID
    first, second = rig.transport.calls
    assert first.stream and first.system is not None and first.system.startswith("You are a Claude agent")
    assert [type(m) for m in second.messages][-1] is SystemMessage
    assert len(second.tools or []) == 20 and second.effort == "high"


def _transport(family: str, upstream: str, monkeypatch: pytest.MonkeyPatch) -> LLMTransport:
    if family == "openai_chat":
        return OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    if family == "azure_responses":
        return AzureOpenAITransport(
            api_key=ORG_KEY, azure_endpoint=upstream, api_version="2025-04-01-preview", deployment="dep-1",
            model="gpt-5.4", defaults=RequestDefaults(use_responses_api=True, reasoning={"effort": "low"}, model="gpt-5.4"),
        )
    monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream)
    return AnthropicTransport(api_key=ORG_KEY, model="claude-haiku-4-5")


@pytest.mark.parametrize("family", ["openai_chat", "azure_responses", "anthropic"])
async def test_mh3_real_transports_answer_claude_code_the_same(
    provider: tuple[ResponsesProvider, str], family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, upstream = provider
    body, headers = claude_code_request()
    async with Gateway(GatewayRig(), _transport(family, upstream, monkeypatch)) as gateway:
        events = await _events(gateway.url, gateway.token, body, headers, path="/v1/messages?beta=true")
    assembled = assemble(events)
    assert assembled["content"] == [
        {"type": "text", "text": "Writing files."},
        {"type": "tool_use", "id": "call_a", "name": "write", "input": {"filePath": "a.txt"}},
    ]
    assert (assembled["usage"]["input_tokens"], assembled["usage"]["cache_read_input_tokens"]) == (20, 100)
    path, seen_headers, sent = fake.seen[0]
    expected = {"openai_chat": "/v1/chat/completions", "anthropic": "/v1/messages"}
    assert path == expected.get(family, path) and (family != "azure_responses" or path.endswith("/responses"))
    auth = seen_headers.get("authorization", "") + seen_headers.get("api-key", "") + seen_headers.get("x-api-key", "")
    assert ORG_KEY in auth and gateway.token not in json.dumps(seen_headers)
    for name in ("anthropic-beta", "x-claude-code-session-id", "x-app", "anthropic-dangerous-direct-browser-access"):
        assert name not in seen_headers, name
    assert "claude-cli" not in seen_headers.get("user-agent", "")
    text = json.dumps(sent)
    for marker in ("x-anthropic-billing-header", "device_id", "00000000-0000-4000-8000-000000000000", "clear_thinking"):
        assert marker not in text, marker
    if family == "anthropic":
        # FB-1: the Messages API refuses a system role inside messages; mid-conversation system goes as user text.
        assert [m["role"] for m in sent["messages"]] == ["user", "user", "assistant", "user", "user"]
        assert "metadata" not in sent and "context_management" not in sent


def test_mh3_the_anthropic_transport_sends_a_later_system_message_as_user_text() -> None:
    transport = AnthropicTransport(api_key=ORG_KEY, model="claude-haiku-4-5")
    assert transport._format_message(SystemMessage(content="reminder")) == {  # pyright: ignore[reportPrivateUsage]
        "role": "user", "content": [{"type": "text", "text": "reminder"}]}


JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/2Q=="


def _claude_code_reads_an_image() -> tuple[dict[str, Any], dict[str, str]]:
    """Claude Code's recorded tool round trip, its tool result now the image its Read tool returns (FIX-P3-B1)."""
    body, headers = claude_code_request()
    body = copy.deepcopy(body)
    result = next(b for m in body["messages"] if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result")
    result["content"] = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG}}]
    return body, headers


@pytest.mark.parametrize("family", ["openai_chat", "azure_responses", "anthropic"])
async def test_mh6_claude_codes_image_read_reaches_every_provider_family(
    provider: tuple[ResponsesProvider, str], family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, upstream = provider
    body, headers = _claude_code_reads_an_image()
    async with Gateway(GatewayRig(), _transport(family, upstream, monkeypatch)) as gateway:
        events = await _events(gateway.url, gateway.token, body, headers, path="/v1/messages?beta=true")
    assert assemble(events)["content"][0] == {"type": "text", "text": "Writing files."}
    sent = fake.seen[0][2]
    url = f"data:image/jpeg;base64,{JPEG}"
    if family == "azure_responses":
        output = next(i for i in sent["input"] if i.get("type") == "function_call_output")["output"]
        assert output == [{"type": "input_image", "image_url": url}]
    elif family == "openai_chat":
        tool = next(i for i, m in enumerate(sent["messages"]) if m["role"] == "tool")
        assert sent["messages"][tool + 1]["content"][1] == {"type": "image_url", "image_url": {"url": url}}
    else:
        blocks = [b for m in sent["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        assert blocks[0]["content"] == [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG}}]


@pytest.mark.usefixtures("no_ambient_key")
async def test_mh6_a_request_the_gateway_cannot_translate_is_answered_once_and_never_retried() -> None:
    import anthropic

    rig = GatewayRig()
    rig.transport.fail = TypeError('can only concatenate list (not "str") to list')
    async with Gateway(rig) as gateway:
        client = _client(gateway, retries=2).with_options(timeout=30)
        with pytest.raises(anthropic.BadRequestError) as refused:
            await client.messages.create(**_ask())
        await client.close()
        assert gateway.requests == 1
    assert refused.value.response.headers["x-should-retry"] == "false"
    assert "[gateway:request_untranslatable]" in refused.value.message


async def _post(gateway: Gateway, body: dict[str, Any], headers: dict[str, str] | None = None, *, token: bool = True) -> httpx.Response:
    sent = {**(headers or {}), **({"Authorization": f"Bearer {gateway.token}"} if token else {})}
    async with loopback_client() as client:
        return await client.post(f"{gateway.url}/v1/messages", json=body, headers=sent)


def _ask(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": ENGINE_MODEL_ID, "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}], **extra}


async def test_mh4_refusals_are_anthropic_errors() -> None:
    async with Gateway(GatewayRig()) as gateway:
        anonymous = await _post(gateway, _ask(), token=False)
        beta = await _post(gateway, _ask(), {"anthropic-beta": "claude-code-20250219,afk-mode-2026-01-31"})
        invalid = await _post(gateway, {"model": ENGINE_MODEL_ID})
        other_model = await _post(gateway, _ask(model="claude-opus-4-8"))
    assert anonymous.status_code == 401 and anonymous.headers[REFUSAL_HEADER] == "token_missing"
    assert anonymous.headers["WWW-Authenticate"].startswith("Bearer")
    assert anonymous.json()["error"]["type"] == "authentication_error" and anonymous.json()["type"] == "error"
    assert "x-should-retry" not in anonymous.headers
    assert (beta.status_code, beta.headers[REFUSAL_HEADER], beta.headers["x-should-retry"]) == (400, "unsupported_parameter", "false")
    assert beta.json()["error"]["type"] == "invalid_request_error"
    assert "capability_rejected: beta_header:afk-mode-2026-01-31" in beta.json()["error"]["message"]
    assert (invalid.status_code, invalid.headers[REFUSAL_HEADER]) == (400, "invalid_messages_request")
    assert (other_model.status_code, other_model.json()["error"]["type"]) == (404, "not_found_error")
    assert gateway.rig.transport.calls == []


@pytest.mark.usefixtures("no_ambient_key")
async def test_mh4_a_budget_refusal_is_never_retried_a_rate_limit_is() -> None:
    import anthropic

    rig = GatewayRig()
    async with Gateway(rig, budget=RunBudget(max_cost_usd=1.0, max_tokens=None)) as gateway:
        await prespend(rig, Amount(1_000_000, 10))
        raw = await _post(gateway, _ask())
        assert (raw.status_code, raw.json()["error"]["type"], raw.headers["x-should-retry"]) == (402, "billing_error", "false")
        gateway.requests = 0
        client = _client(gateway, retries=2)
        with pytest.raises(anthropic.APIStatusError) as refused:
            await client.messages.create(**_ask())
        assert refused.value.status_code == 402 and gateway.requests == 1
        await client.close()
    limited = GatewayRig()
    limited.transport.fail = TransportError("Rate limit", status_code=429)
    async with Gateway(limited) as gateway:
        client = _client(gateway, retries=1).with_options(timeout=30)
        with pytest.raises(anthropic.RateLimitError):
            await client.messages.create(**_ask())
        # The SDK retried the 429 once: no x-should-retry: false on it.
        assert gateway.requests == 2
        await client.close()


@pytest.mark.usefixtures("no_ambient_key")
async def test_mh5_count_tokens_through_the_sdk() -> None:
    async with Gateway(GatewayRig()) as gateway:
        client = _client(gateway)
        counted = await client.messages.count_tokens(
            model=ENGINE_MODEL_ID, messages=[{"role": "user", "content": "hello " * 40}], tools=[TOOL]  # type: ignore[list-item]
        )
        await client.close()
        assert counted.input_tokens > 40 and gateway.rig.transport.calls == []


class ScriptedTransport(RecordedTransport):
    """Answers each call with the next scripted turn (the last one repeats)."""

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        super().__init__(turns[0])
        self.turns = turns

    async def stream(self, messages: Any, tools: Any = None, system: Any = None, model: Any = None,  # noqa: ANN401
                     thinking_budget: Any = None, effort: Any = None, system_blocks: Any = None, *, tool_choice: object = None) -> AsyncIterator[StreamEvent]:  # noqa: ANN401
        self.events = self.turns[min(len(self.calls), len(self.turns) - 1)]
        async for event in super().stream(messages, tools, system, model, thinking_budget, effort, system_blocks):
            yield event


def _tool_turn() -> list[StreamEvent]:
    message = AssistantMessage(
        tool_calls=[ToolCall(id="toolu_gw_1", name="Bash", arguments={"command": "echo hi", "description": "say hi"})]
    )
    return [StreamCompleteEvent(response=ModelResponse(message=message, usage=TokenUsage(input_tokens=50, output_tokens=9),
                                                       stop_reason=StopReason.TOOL_USE))]


@pytest.mark.skipif(not os.environ.get("HARNESS_CLAUDE_CODE_BIN"), reason="HARNESS_CLAUDE_CODE_BIN names no Claude Code binary")
@pytest.mark.timeout(180)
async def test_cc2_the_claude_code_binary_completes_a_tool_round_trip(tmp_path: Any) -> None:  # noqa: ANN401
    transport = ScriptedTransport([_tool_turn(), [reply_events("done: hi")]])
    rig = GatewayRig(transport=transport)
    async with Gateway(rig) as gateway:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
            "ANTHROPIC_BASE_URL": gateway.url,
            "ANTHROPIC_AUTH_TOKEN": gateway.token,
            "ANTHROPIC_MODEL": ENGINE_MODEL_ID,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": ENGINE_MODEL_ID,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        }
        work = tmp_path / "work"
        work.mkdir()
        process = await asyncio.create_subprocess_exec(
            os.environ["HARNESS_CLAUDE_CODE_BIN"], "-p", "run echo hi", "--permission-mode", "default",
            "--allowedTools", "Bash(echo hi)", "--output-format", "json",
            cwd=work, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(process.communicate(), timeout=150)
    result = json.loads(out.decode().strip().splitlines()[-1])
    assert process.returncode == 0 and result["is_error"] is False and result["result"] == "done: hi"
    assert len(transport.calls) == 2
    tool_result = transport.calls[1].messages
    assert any(getattr(m, "tool_call_id", None) == "toolu_gw_1" and "hi" in str(m.content) for m in tool_result)
    assert isinstance(transport.calls[0].messages[0], UserMessage)


@pytest.mark.parametrize("cloud", ["bedrock", "vertex"])
async def test_bv5_claude_code_reaches_anthropic_on_bedrock_and_vertex(cloud: str) -> None:
    from harness_core.models.transports.anthropic_cloud import (
        anthropic_on_bedrock,
        anthropic_on_vertex,
    )
    from harness_core.testing.cloud_providers import (
        FakeBedrock,
        FakeVertex,
        service_account,
    )

    account = service_account("pending")
    fake: FakeBedrock | FakeVertex = (
        FakeBedrock("AKIA" + "E3CTESTKEYID0000", "us-east-1") if cloud == "bedrock"
        else FakeVertex(account.public_key_pem, account.client_email)
    )
    upstream = TestServer(fake.app(), host="127.0.0.1")
    await upstream.start_server()
    url = str(upstream.make_url("")).rstrip("/")
    transport = (
        anthropic_on_bedrock(region="us-east-1", access_key_id="AKIA" + "E3CTESTKEYID0000", secret_access_key="s" * 40,
                             model="us.anthropic.claude-sonnet-4-6-v1:0", base_url=url)
        if cloud == "bedrock"
        else anthropic_on_vertex(project_id="proj-e3c-test", location="us-east5", service_account_json=account.json,
                                 model="claude-sonnet-4-6", base_url=f"{url}/v1", token_uri=f"{url}/token")
    )
    body, headers = claude_code_request()
    rig = GatewayRig()
    try:
        async with Gateway(rig, transport) as gateway:
            events = await _events(gateway.url, gateway.token, body, headers, path="/v1/messages?beta=true")
    finally:
        await upstream.close()
    assembled = assemble(events)
    assert assembled["content"] == [{"type": "text", "text": "hello from the cloud"}]
    assert (assembled["usage"]["input_tokens"], assembled["usage"]["output_tokens"]) == (12, 7)
    assert len(fake.seen) == 1 and fake.seen[0].path.endswith(("invoke-with-response-stream", ":streamRawPredict"))
    assert [m["role"] for m in fake.seen[0].body["messages"]] == ["user", "user", "assistant", "user", "user"]
    assert (await rig.ledger.remaining(account_ref())).committed.usd_micros > 0
