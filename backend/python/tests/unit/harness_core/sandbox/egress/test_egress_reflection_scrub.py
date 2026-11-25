"""SEC-C-15: nothing the egress proxy forwards to a sandbox carries the credential it swapped in.

A destination that reflects what it received (status line, a header name or value, a cookie, a redirect, the body,
raw or encoded) hands the sandbox the real token unless every forwarded field is scrubbed the same way.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING
from urllib.parse import quote

import aiohttp
import pytest
from aiohttp import web

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.policy import ResponseScrubber, reflection_variants
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

PH = "ph-model-0123456789abcdef"
SECRET = b"secc15-secret"
TOKEN = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)),),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)


def _b64_embedded(encode: Callable[[bytes], bytes], prefix: bytes) -> str:
    return encode(prefix + TOKEN.encode() + b'"}').decode()


ENCODINGS: dict[str, Callable[[str], str]] = {
    "raw": lambda t: t,
    "url_upper": lambda t: quote(t, safe=""),
    "url_lower": lambda t: quote(t, safe="").lower(),
    "hex": lambda t: t.encode().hex(),
    "hex_upper": lambda t: t.encode().hex().upper(),
    "b64": lambda t: base64.b64encode(t.encode()).decode(),
    "b64_unpadded": lambda t: base64.b64encode(t.encode()).decode().rstrip("="),
    "b64_urlsafe": lambda t: base64.urlsafe_b64encode(t.encode()).decode().rstrip("="),
    "b64_offset1": lambda t: _b64_embedded(base64.b64encode, b"{"),
    "b64_offset2": lambda t: _b64_embedded(base64.b64encode, b'{"'),
    "b64_urlsafe_offset1": lambda t: _b64_embedded(base64.urlsafe_b64encode, b"x"),
    "basic_echo": lambda t: "Basic " + base64.b64encode(f"x-access-token:{t}".encode()).decode(),
}


def _leaks(text: str) -> list[str]:
    """Which encodings of the token still appear in ``text``."""
    leaked = [name for name, encode in ENCODINGS.items() if name != "basic_echo" and encode(TOKEN) in text]
    raw = TOKEN.encode()
    for offset in range(3):
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            encoded = encode(b"\0" * offset + raw).decode()
            core = encoded[-(-4 * offset // 3) : (offset + len(raw)) * 8 // 6]
            if core in text:
                leaked.append(f"b64-core-{offset}")
    return leaked


class TestVariants:
    @pytest.mark.parametrize("name", sorted(ENCODINGS))
    def test_every_encoding_of_the_token_is_scrubbed_from_text(self, name: str) -> None:
        scrubber = ResponseScrubber(((TOKEN, PH),))
        out = scrubber.text(f"before {ENCODINGS[name](TOKEN)} after")
        assert _leaks(out) == [], out

    def test_a_basic_credential_echoed_whole_is_scrubbed(self) -> None:
        credential = f"x-access-token:{TOKEN}"
        echo = "Basic " + base64.b64encode(credential.encode()).decode()
        out = ResponseScrubber(((credential, PH), (TOKEN, PH))).text(echo)
        assert base64.b64encode(credential.encode()).decode()[:-4] not in out and _leaks(out) == []

    def test_short_secrets_are_not_expanded(self) -> None:
        assert reflection_variants((("admin", PH),)) == (("admin", PH),)

    def test_the_reason_is_always_the_standard_phrase(self) -> None:
        assert ResponseScrubber.reason(200) == "OK"
        assert ResponseScrubber.reason(404) == "Not Found"
        assert ResponseScrubber.reason(599) == ""

    def test_header_names_and_values_are_scrubbed(self) -> None:
        out = ResponseScrubber(((TOKEN, PH),)).headers(
            [(f"X-{TOKEN}", "v"), ("Set-Cookie", f"s={quote(TOKEN, safe='')}; Path=/"), ("Content-Length", "3")]
        )
        assert _leaks(repr(out)) == [] and len(out) == 2

    @pytest.mark.parametrize("name", sorted(ENCODINGS))
    @pytest.mark.parametrize("step", [1, 3, 7, 64])
    def test_the_body_is_scrubbed_across_any_chunk_split(self, name: str, step: int) -> None:
        body = ResponseScrubber(((TOKEN, PH),)).body()
        assert body is not None
        stream = f"data: {ENCODINGS[name](TOKEN)}\n\n".encode()
        out = b"".join(body.feed(stream[i : i + step]) for i in range(0, len(stream), step)) + body.finish()
        assert _leaks(out.decode()) == []

    def test_nothing_swapped_means_nothing_scrubbed(self) -> None:
        assert ResponseScrubber(()).body() is None


async def _reflecting(request: web.Request) -> web.StreamResponse:
    """A destination that echoes the credential it received through every field of its answer."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    encoded = {name: encode(token) for name, encode in ENCODINGS.items()}
    response = web.StreamResponse(status=302, reason=f"Found {token} {encoded['b64']}")
    response.headers["Location"] = f"https://elsewhere.example/cb?t={quote(token, safe='')}"
    response.headers[f"X-Echo-{token}"] = "name"
    response.headers["X-Echo"] = encoded["b64_unpadded"]
    response.set_cookie("session", encoded["url_lower"])
    response.headers["WWW-Authenticate"] = encoded["basic_echo"]
    await response.prepare(request)
    payload = json.dumps(encoded).encode()
    for i in range(0, len(payload), 5):
        await response.write(payload[i : i + 5])
    await response.write_eof()
    return response


@pytest.fixture
async def proxy_port() -> AsyncIterator[int]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _reflecting)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    up_port = runner.addresses[0][1]
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": f"http://127.0.0.1:{up_port}"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(max_body_bytes=1024),
    )
    server = EgressServer(proxy, StaticBearerAuth("control"), instance="secc15-test")
    data_port, _ = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    await proxy.bindings.register(EgressBinding("sbx-a", "127.0.0.1", "127.0.0.1", POLICY, {"runId": "r1"}))
    yield data_port
    await server.stop()
    await runner.cleanup()


async def test_no_forwarded_field_carries_the_swapped_token(proxy_port: int) -> None:
    async with aiohttp.ClientSession() as session, session.get(
        f"http://127.0.0.1:{proxy_port}/v1/x",
        headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
        allow_redirects=False,
    ) as response:
        body = await response.text()
        status_line = f"{response.status} {response.reason}"
        headers = repr(list(response.raw_headers))
    assert status_line == "302 Found"
    assert _leaks(headers) == [], headers
    assert _leaks(body) == [], body
    # The answer still reached the sandbox, with the placeholder where the token was.
    assert PH in headers and PH in body
