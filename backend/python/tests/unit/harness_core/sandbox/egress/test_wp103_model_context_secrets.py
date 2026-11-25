"""WP-103 EGFP-1..EGFP-8: a credential shape in an engine's model context is hidden, not a refusal of the call.

A research run: the engine had fetched PipesHub's public personal-access-token page, whose example token
has the ``pipeshub_pat`` shape, and printed it from its notes; the next model call carried it and the egress proxy
refused the call (``forbidden_pattern``), which ended a 17-minute run. The body of a call to the Model Gateway is
the run's own context going to the org's model: the shape is replaced there and the call goes on, the hide is on the
run's audit record by kind. A header or a URL to the gateway, any request elsewhere (the tool gateway's arguments, a
website), a match that cannot be replaced inside a JSON string, and the run's live gateway token are refused as
before; the refusal tells the engine, in words, that PipesHub blocked a request that looked like it held a secret.
"""

from __future__ import annotations

import json

import pytest

from harness_core.gateway.refusal_reports import (
    InMemoryRefusalSink,
    RefusalReporter,
    SecretHidden,
)
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.policy import check_request_content
from harness_core.sandbox.egress.server import refusal_response
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.security.token_patterns import (
    FORBIDDEN_TOKEN_PATTERNS,
    HIDDEN_CREDENTIAL,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_engines.codex.mapper import error_code

R = EgressRefusalReason
SECRET = b"wp103-test-secret"
PH = "ph-model-0123456789abcdef"
PH_TOOLS = "ph-tools-0123456789abcdef"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}
POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination("tool-gateway", (8097,)),
        EgressDestination("docs.example.org", (80,)),
    ),
    token_routes=(
        TokenRoute(PH, "model-gateway", "gateway:model"),
        TokenRoute(PH_TOOLS, "tool-gateway", "gateway:tools"),
    ),
)
BOUND = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, LABELS)
# Built at run time so the source holds no literal credential shape: one documentation example per forbidden kind.
EXAMPLES = {
    "anthropic_key": "sk-ant-" + "api03-" + "Xq7Rm2Tz9Lp4Wv8Kd3Ns6Hb1",
    "openai_key": "sk-" + "proj-" + "Q7mZ2rT9xL4pW8vK3dN6sH1bY5",
    "slack_token": "xox" + "b-" + "1234567890-AbCdEfGhIjKl",
    "github_token": "gh" + "p_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "gitlab_token": "gl" + "pat-" + "AbCdEfGhIjKlMnOpQrSt",
    "aws_access_key": "AK" + "IA" + "Z7Q2M4X8R1T6W3Y9",
    "google_api_key": "AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "pipeshub_pat": "ph" + "pat_" + "Ab3dEf6hIj9lMn2pQr5t",
}
# Run 12's page, as the engine printed it from its notes: a docs paste block with the example token.
PAT_PAGE = (
    "### Step 3: Copy the Token\n\nPipesHub generates the token and shows it to you **exactly once**, along with a "
    "ready-to-paste block for connecting an MCP client:\n\n```\nPIPESHUB_MCP_URL=https://your-pipeshub-instance.com"
    f"/mcp\nPIPESHUB_MCP_TOKEN={EXAMPLES['pipeshub_pat']}...\n```\n"
)


def responses_call(output: str) -> bytes:
    """A Codex model call (OpenAI Responses) whose last input is a shell command's output."""
    body = {
        "model": "gpt-5.5",
        "input": [
            {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": '{"command": ["cat", "notes/web/pat.txt"]}'},
            {"type": "function_call_output", "call_id": "call_1", "output": output},
        ],
        "stream": True,
    }
    return json.dumps(body).encode()


def call(
    target: str = "/v1/responses", *, host: str = "model-gateway:8097", auth: str = PH, body: bytes = b"{}",
    headers: tuple[tuple[str, str], ...] = (),
) -> ProxyRequest:
    return ProxyRequest("POST", target, (("Host", host), ("Authorization", f"Bearer {auth}"), *headers), body)


async def _proxy(policy: EgressPolicy = POLICY) -> tuple[EgressProxy, RefusalReporter, InMemoryRefusalSink]:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    await bindings.register(EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", policy, LABELS))
    sink = InMemoryRefusalSink()
    reporter = RefusalReporter(sink)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(SECRET, clock, ttl_s=600),
        resolver=StaticResolver({"docs.example.org": ["93.184.216.34"]}),
        upstreams={"model-gateway": "http://upstream-model:9000/", "tool-gateway": "http://upstream-tools:9001/"},
        clock=clock,
        ids=SeededIds(),
        refusals=reporter,
    )
    return proxy, reporter, sink


async def _refused(proxy: EgressProxy, request: ProxyRequest) -> EgressRefused:
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize(request, local="10.213.0.2", peer="10.213.0.3")
    return caught.value


def test_egfp_examples_cover_every_forbidden_kind() -> None:
    assert set(EXAMPLES) == {p.kind for p in FORBIDDEN_TOKEN_PATTERNS}


@pytest.mark.parametrize("kind", sorted(EXAMPLES))
async def test_egfp1_a_shape_in_a_model_calls_body_is_hidden_and_the_call_goes_on(kind: str) -> None:
    proxy, reporter, sink = await _proxy()
    page = PAT_PAGE.replace(EXAMPLES["pipeshub_pat"], EXAMPLES[kind])
    forward = await proxy.authorize(call(body=responses_call(page)), local="10.213.0.2", peer="10.213.0.3")
    assert EXAMPLES[kind].encode() not in forward.body
    sent = json.loads(forward.body)
    assert sent["input"][1]["output"] == page.replace(EXAMPLES[kind], HIDDEN_CREDENTIAL)
    await reporter.drain()
    assert sink.hidden_secrets == [("r1", SecretHidden("egress", kind))]
    assert sink.records == [], "a hide is no refusal"


async def test_egfp1b_run_12s_page_reaches_the_gateway_and_each_kind_is_reported_once() -> None:
    proxy, reporter, sink = await _proxy()
    for _ in range(5):
        forward = await proxy.authorize(call(body=responses_call(PAT_PAGE)), local="10.213.0.2", peer="10.213.0.3")
        assert HIDDEN_CREDENTIAL.encode() in forward.body
    await reporter.drain()
    assert sink.hidden_secrets == [("r1", SecretHidden("egress", "pipeshub_pat"))]


@pytest.mark.parametrize(
    "request_",
    [
        call(headers=(("X-Api-Key", EXAMPLES["anthropic_key"]),)),
        call(f"/v1/responses?key={EXAMPLES['openai_key']}"),
        call(auth=EXAMPLES["github_token"]),
    ],
    ids=["header", "url", "authorization"],
)
async def test_egfp2_a_shape_in_a_header_or_the_url_to_the_gateway_is_still_refused(request_: ProxyRequest) -> None:
    proxy, reporter, sink = await _proxy()
    refused = await _refused(proxy, request_)
    assert refused.reason in (R.FORBIDDEN_PATTERN, R.CALLER_AUTHORIZATION, R.PLACEHOLDER_MISSING)
    await reporter.drain()
    assert sink.hidden_secrets == []


@pytest.mark.parametrize("where", ["tool-gateway", "website"])
async def test_egfp3_a_shape_in_a_body_anywhere_else_is_still_refused(where: str) -> None:
    proxy, _, _ = await _proxy()
    args = json.dumps({"method": "tools/call", "params": {"arguments": {"note": EXAMPLES["pipeshub_pat"]}}}).encode()
    request_ = (
        call("/mcp", host="tool-gateway:8097", auth=PH_TOOLS, body=args)
        if where == "tool-gateway"
        else ProxyRequest("POST", "http://docs.example.org/form", (("Host", "docs.example.org"),), args)
    )
    assert (await _refused(proxy, request_)).reason == R.FORBIDDEN_PATTERN


async def test_egfp4_the_runs_live_gateway_token_in_a_model_call_is_still_refused() -> None:
    proxy, _, _ = await _proxy()
    first = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    token = dict(first.headers)["Authorization"].removeprefix("Bearer ")
    refused = await _refused(proxy, call(body=responses_call(f"leaked {token}")))
    assert refused.reason == R.TOKEN_REPLAYED


def test_egfp5_a_policy_pattern_is_hidden_when_it_can_be_and_refused_when_it_cannot() -> None:
    plain = EgressPolicy(destinations=POLICY.destinations, forbidden_patterns=(r"ACME-[0-9]{6}",))
    inspected = check_request_content(call(body=b'{"x": "id ACME-123456"}'), plain, (), model_context=True)
    assert inspected.request.body == b'{"x": "id ' + HIDDEN_CREDENTIAL.encode() + b'"}'
    assert inspected.hidden == ("policy_pattern",)
    # A match that spans JSON syntax cannot be replaced without changing the document: refused, never rewritten.
    spanning = EgressPolicy(destinations=POLICY.destinations, forbidden_patterns=(r'ACME": "[0-9]+',))
    with pytest.raises(EgressRefused) as caught:
        _ = check_request_content(call(body=b'{"ACME": "123456"}'), spanning, (), model_context=True)
    assert caught.value.reason == R.FORBIDDEN_PATTERN


def test_egfp6_outside_a_model_context_the_body_is_refused_as_before() -> None:
    with pytest.raises(EgressRefused) as caught:
        _ = check_request_content(call(body=responses_call(PAT_PAGE)), POLICY, ())
    assert caught.value.reason == R.FORBIDDEN_PATTERN


@pytest.mark.parametrize("reason", [R.FORBIDDEN_PATTERN, R.TOKEN_REPLAYED, R.PLACEHOLDER_WRONG_DESTINATION])
def test_egfp7_a_secret_refusal_tells_the_engine_in_words_and_the_engine_reports_its_typed_code(reason: R) -> None:
    refused = EgressRefused(reason, "internal detail")
    refused.request_id = "egr_1"
    body = json.loads(refusal_response(refused).body)  # pyright: ignore[reportArgumentType]
    message = body["error"]["message"]
    assert "looked like it contained a secret" in message and "[gateway:secret_blocked]" in message
    assert body["error"]["reason"] == reason.value, "operators keep the proxy's own reason"
    # Codex keeps the body's message of a refused call (RL-3): the run stops for PipesHub's reason, not "http_403".
    assert error_code({"message": message, "codexErrorInfo": {"other": {"httpStatusCode": 403}}}) == "secret_blocked"


def test_egfp8_other_refusals_keep_their_own_body() -> None:
    refused = EgressRefused(R.DESTINATION_NOT_ALLOWED, "x", host="evil.example")
    body = json.loads(refusal_response(refused).body)  # pyright: ignore[reportArgumentType]
    assert "message" not in body["error"]


# --- WP103-FU-2: the model path's other refusals reach the kernel typed -------------------------------------------


def _refused_for(reason: R) -> EgressRefused:
    refused = EgressRefused(reason, "internal detail")
    refused.request_id = "egr_2"
    return refused


def test_wp103_fu2_a_model_call_over_the_body_limit_is_told_as_a_context_overflow() -> None:
    """A model call over the proxy's body limit was a bare 413 that the engine reported as ``http_413`` and the person
    read as an engine error. To the engine it is its context being too large to send: the overflow code makes the
    kernel compact the session and run the task once more (MG-10), and words the rest as an overflow."""
    from harness_core.engines.overflow import OVERFLOW_CODES

    body = json.loads(refusal_response(_refused_for(R.BODY_TOO_LARGE), model_call=True).body)  # pyright: ignore[reportArgumentType]
    message = body["error"]["message"]
    assert "[gateway:context_length_exceeded]" in message and "larger than" in message
    assert body["error"]["reason"] == "body_too_large", "operators keep the proxy's own reason"
    code = error_code({"message": message, "codexErrorInfo": {"other": {"httpStatusCode": 413}}})
    assert code in OVERFLOW_CODES


def test_wp103_fu2_an_encoded_model_call_stops_as_pipeshubs_own_refusal_not_an_engine_error() -> None:
    """A compressed model call cannot be inspected; only an admin can change how the engine sends it. Its code is one
    of PipesHub's stops, so the run fails at once with it (never "the coding engine ran into an error")."""
    from harness_core.engines.outcome.faults import PIPESHUB_STOP_CODES, engine_stop_cut

    body = json.loads(refusal_response(_refused_for(R.BODY_ENCODED), model_call=True).body)  # pyright: ignore[reportArgumentType]
    message = body["error"]["message"]
    assert "[gateway:model_call_encoded]" in message
    code = error_code({"message": message, "codexErrorInfo": {"other": {"httpStatusCode": 415}}})
    assert code == "model_call_encoded"
    assert code in PIPESHUB_STOP_CODES and engine_stop_cut(code) is None


@pytest.mark.parametrize("reason", [R.BODY_TOO_LARGE, R.BODY_ENCODED])
def test_wp103_fu2_the_same_refusal_elsewhere_keeps_its_own_body(reason: R) -> None:
    body = json.loads(refusal_response(_refused_for(reason)).body)  # pyright: ignore[reportArgumentType]
    assert "message" not in body["error"]


async def test_wp103_fu2_the_data_plane_tags_only_a_model_call() -> None:
    """Over HTTP: an oversized body to the Model Gateway carries the overflow tag; the same to a website does not."""
    import aiohttp

    from harness_core.sandbox.auth import StaticBearerAuth
    from harness_core.sandbox.egress import ProxyLimits
    from harness_core.sandbox.egress.server import EgressServer

    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": "http://127.0.0.1:1"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(max_body_bytes=64),
    )
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="egress-test")
    data_port, _ = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    try:
        await proxy.bindings.register(EgressBinding("sbx-a", "127.0.0.1", "127.0.0.1", POLICY, LABELS))
        seen: dict[str, dict[str, str]] = {}
        async with aiohttp.ClientSession() as session:
            for host in ("model-gateway:8097", "docs.example.org"):
                async with session.post(
                    f"http://127.0.0.1:{data_port}/v1/responses", headers={"Host": host}, data=b"x" * 200
                ) as response:
                    assert response.status == 413
                    seen[host] = (await response.json())["error"]
        assert "[gateway:context_length_exceeded]" in seen["model-gateway:8097"]["message"]
        assert "message" not in seen["docs.example.org"]
    finally:
        await server.stop()
