"""Wire probes (local sockets only).

1. Gemini through ``route_genai_through_httpx``: a 200 answer and a streamed answer give the same text as an
   un-instrumented model (aiohttp path), and the wire hook sees the 200 (no behaviour change on success).
2. Every httpx wrapper, through the gateway on the fleet breaker: a reset after the write and a refused connect are
   judged FAIL (ProviderUnavailableError, sent=True) and count toward the breaker.
3. A hang: inside a caller budget it is neutral (the breaker is not moved); at the gateway ceiling it is a failure.
4. A hookless chat model outside any indexing context is allowed at construction (refused only on paid indexing work).
"""

import asyncio
import json
import socket

import pytest

from app.services.llm_gateway import gateway as gw
from app.services.llm_gateway import wire
from app.services.llm_gateway.fleet_breaker import (
    FleetBreaker,
    credential_of,
    provider_pk,
    size_class,
)
from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError
from app.services.model_identity.transport.instrument import instrument_model
from tests.integration.spend.test_wire_contract import WRAPPERS
from tests.support.fake_llm_server import FakeLLMServer, Reply

pytestmark = pytest.mark.timeout(120)


def _gemini_payload(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1, "totalTokenCount": 4}}


class GeminiFake:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def start(self) -> "GeminiFake":
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            line = head.split(b"\r\n", 1)[0].decode()
            self.paths.append(line)
            length = 0
            for h in head.split(b"\r\n"):
                if h.lower().startswith(b"content-length:"):
                    length = int(h.split(b":", 1)[1])
            if length:
                await reader.readexactly(length)
            if "streamGenerateContent" in line:
                body = b"".join(b"data: " + json.dumps(_gemini_payload(t)).encode() + b"\r\n\r\n" for t in ("he", "llo"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            else:
                body = json.dumps(_gemini_payload("hello")).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            await writer.drain()
        finally:
            writer.close()

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()


def _gemini(base: str) -> object:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model="gemini-x", api_key="x", base_url=base, max_retries=0, timeout=10)


async def test_gemini_success_and_streaming_unchanged_through_httpx(fleet) -> None:
    fake = await GeminiFake().start()
    try:
        base = f"http://127.0.0.1:{fake.port}"
        plain = _gemini(base)
        hooked = instrument_model(_gemini(base), "gemini", None)
        ref = await plain.ainvoke("DOC:x")
        ref_stream = "".join([c.content async for c in plain.astream("DOC:x")])
        got = await LLMGateway(4).invoke(hooked, "DOC:x", provider="gemini:gemini-x@" + base)
        with wire.wire_scope() as seen:
            await hooked.ainvoke("DOC:x")
        assert seen.status == 200 and seen.sent, seen
        assert got.content == ref.content == "hello"
        with wire.wire_scope() as seen_s:
            chunks = [c.content async for c in hooked.astream("DOC:x")]
        assert "".join(chunks) == ref_stream == "hello" and len(chunks) >= 2, chunks
        assert seen_s.status == 200, "the hook sees the streamed request (httpx path)"
    finally:
        await fake.stop()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


HTTPX = [w for w in WRAPPERS if w != "langchain-aws"]


@pytest.mark.parametrize("fault", ["reset", "refused"])
@pytest.mark.parametrize("wrapper", HTTPX)
async def test_transport_faults_count_as_failures(fleet, monkeypatch, wrapper: str, fault: str) -> None:
    monkeypatch.setattr(gw, "RATE_LIMIT_ATTEMPTS", 1)
    build, provider = WRAPPERS[wrapper]
    server = None
    if fault == "reset":
        server = await FakeLLMServer(lambda body: Reply(reset=True)).start()
        base = f"http://127.0.0.1:{server.port}"
    else:
        base = f"http://127.0.0.1:{_closed_port()}"
    try:
        model = instrument_model(build(base), provider, None)
        key = f"{provider}:m@{base}"
        for _ in range(5):
            with pytest.raises(ProviderUnavailableError) as err:
                await LLMGateway(4).invoke(model, "DOC:x", provider=key)
            assert err.value.sent is True
        admission = await FleetBreaker(fleet).admit(provider_pk(key, credential_of(model)), size_class(1), "probe")
        assert admission.code == 1, "five transport failures must open the fleet breaker"
    finally:
        if server is not None:
            await server.stop()


async def test_hang_inside_a_caller_budget_is_neutral_and_at_the_ceiling_fails(fleet, monkeypatch) -> None:
    monkeypatch.setattr(gw, "RATE_LIMIT_ATTEMPTS", 1)
    build, provider = WRAPPERS["langchain-openai"]
    server = await FakeLLMServer(lambda body: Reply(hang=True)).start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        model = instrument_model(build(base, 60.0), provider, None)
        key = f"{provider}:m@{base}"
        for _ in range(6):
            with pytest.raises(TimeoutError):
                await LLMGateway(4).invoke(model, "DOC:x", provider=key, timeout=0.3)
        assert (await FleetBreaker(fleet).admit(provider_pk(key, credential_of(model)), size_class(1), "p")).code == 0, "caller budget is neutral"
        monkeypatch.setattr(gw, "CALL_TIMEOUT_S", 0.3)
        for _ in range(5):
            with pytest.raises(ProviderUnavailableError):
                await LLMGateway(4).invoke(model, "DOC:x", provider=key)
        assert (await FleetBreaker(fleet).admit(provider_pk(key, credential_of(model)), size_class(1), "p")).code == 1, "ceiling hangs open it"
    finally:
        await server.stop()


def test_a_hookless_chat_model_outside_indexing_is_refused() -> None:
    """Refused only on the indexing workload; anywhere else it is allowed and reported by /health."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from app.services.model_identity.call_context import peek_call_context

    assert peek_call_context() is None
    try:
        instrument_model(FakeListChatModel(responses=["x"]), "openAI", None)
    except wire.WireHookError as exc:
        pytest.fail(f"refused outside the indexing workload: {exc}")
