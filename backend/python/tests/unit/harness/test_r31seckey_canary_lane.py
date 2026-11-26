"""R31-SECKEY: the pieces of the engine-sandbox canary lane (deployment/e2e/seckey.py) that decide a verdict.

The lane itself needs a stack and runs in deployment/e2e; these hold its readers to fixed outputs, so a scan that
misreads grep's lines, a probe reader that takes silence for a refusal, or a canary watch that misses a header
cannot turn a leak into a pass:

* SK-ENC: a canary is found in each form a copy may take (raw, base64 at every offset inside a longer value,
  percent-encoded, hex), and an unrelated value is not.
* SK-SCAN: grep's ``path:match`` lines map to the canary's name and form, never its value; the sentinel's places
  are read off the paths; an unfinished scan is not complete.
* SK-ENV: a credential-named variable holding a value that is not a placeholder is named.
* SD-READ: the direct probe and the proxy probe readers; a proxy that answers 200 for an upstream is let through.
* SK-WATCH: the fake SaaS records a canary it receives in a header, the query or the body, by digest only, and
  serves SearXNG's JSON; its OAuth token endpoint refuses every grant.
* SK-INV: the planted canaries are unique, the vendor-shaped ones have their vendor's shape, and the documents the
  lane writes are tagged so its restore takes out exactly them.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from harness_core.security.token_patterns import find_forbidden
from harness_core.testing.canary import encoded_forms, find_encoded

E2E = Path(__file__).resolve().parents[5] / "deployment" / "e2e"
sys.path.insert(0, str(E2E))
import seckey_inventory as inv  # noqa: E402
import seckey_sandbox as sb  # noqa: E402

CANARY = "pipeshub-canary-5b1f0c2e9d8a4f6b8c7d6e5f4a3b2c1d"
VENDOR = "sk-ant-api03-pxhcanary+/=?&0123456789abcdef"


# --- SK-ENC -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [CANARY, VENDOR])
@pytest.mark.parametrize("before", [b"", b"a", b"ab", b"abc", b'{"k":"'])
@pytest.mark.parametrize("after", [b"", b"x", b"xy", b'"}'])
def test_sk_enc_base64_inside_a_longer_value_is_found(value: str, before: bytes, after: bytes) -> None:
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        blob = encode(before + value.encode() + after).decode()
        assert any(form.startswith("base64") for _, form in find_encoded(f"Authorization: Basic {blob}", [value]))


@pytest.mark.parametrize("value", [CANARY, VENDOR])
def test_sk_enc_percent_and_hex_forms_are_found(value: str) -> None:
    for text, form in ((urllib.parse.quote(value, safe=""), "url"), (value.encode().hex(), "hex"),
                       (value.encode().hex().upper(), "HEX")):
        found = {f for _, f in find_encoded(f"x={text}&y=1", [value])}
        assert form in found or (form == "url" and "raw" in found), (form, found)


def test_sk_enc_an_unrelated_value_is_not_found() -> None:
    other = "pipeshub-canary-ffffffffffffffffffffffffffffffff"
    text = " ".join(encoded_forms(other).values())
    assert find_encoded(text, [CANARY]) == []
    assert {form for _, form in find_encoded(text, [other])} >= {"raw", "hex", "base64.0"}


# --- SK-SCAN ------------------------------------------------------------------------------------------------------


def test_sk_scan_lines_name_the_canary_and_form_never_the_value() -> None:
    sentinel = "pipeshub-canary-0000000000000000000000000000beef"
    _, table = sb.patterns({"mcp.oauth.accessToken": CANARY}, sentinel)
    b64 = encoded_forms(CANARY)["base64.1"]
    base = "/tmp/seckey-scan-x"
    out = "\n".join([
        f"{base}/user/state/opencode/auth.json:{CANARY}",
        f"{base}/proc/71.environ:{b64}",
        f"{base}/root/tmp/.seckey-sentinel:{sentinel}",
        f"{base}/proc/90.environ:{sentinel}",
        f"{base}/user/workspace/.seckey-sentinel:{encoded_forms(sentinel)['hex']}",
        "SCAN_DONE 0",
    ])
    result = sb.read_scan(out, table, base)
    assert result.complete
    leaks = result.leaks()
    assert [(f.name, f.form, f.path) for f in leaks] == [
        ("mcp.oauth.accessToken", "raw", "/state/opencode/auth.json"),
        ("mcp.oauth.accessToken", "base64.1", "/proc/71/environ"),
    ]
    assert CANARY not in json.dumps([f.__dict__ for f in leaks])
    assert result.sentinel_seen == {"/tmp", "environ", "/workspace"}


def test_sk_scan_without_its_end_line_is_incomplete() -> None:
    _, table = sb.patterns({"a": CANARY}, "pipeshub-canary-1111111111111111")
    assert not sb.read_scan("", table).complete
    assert not sb.read_scan("SCAN_DONE 137", table).complete


def test_sk_scan_patterns_go_on_stdin_and_cover_every_form() -> None:
    text, table = sb.patterns({"a": CANARY}, "pipeshub-canary-2222222222222222")
    assert text.endswith("\n") and all(len(p) >= 12 for p in text.split())
    assert {form for name, form in table.values() if name == "a"} >= {"raw", "hex", "HEX", "base64.0", "base64.1",
                                                                       "base64.2"}
    assert "/proc|/sys|/dev" in sb.ROOT_TAR and "tar -cf -" in sb.USER_TAR and "/proc/[0-9]*/environ" in sb.PROCS


def test_sk_scan_proc_sections_become_files_with_their_sandbox_paths() -> None:
    dump = "== ENV /proc/7/environ\nA=1\nB=2\n== CMD /proc/7/cmdline\nopencode serve\n== PROCS_DONE\n"
    files = sb.proc_sections(dump)
    assert files == {"7.environ": "A=1\nB=2\n", "7.cmdline": "opencode serve\n"}
    assert sb.sandbox_path("/w/proc/7.environ", "/w") == "/proc/7/environ"
    assert sb.sandbox_path("/w/root/usr/bin/x", "/w") == "/usr/bin/x"


def test_sk_scan_sentinel_planting_quotes_every_form() -> None:
    planted = sb.sentinel_plant("pipeshub-canary-3333333333333333")
    assert all(f"'{v}'" in planted for v in encoded_forms("pipeshub-canary-3333333333333333").values())
    assert all(f"> {place}/.seckey-sentinel" in planted for place in sb.SENTINEL_PLACES)


# --- SK-ENV -------------------------------------------------------------------------------------------------------


def test_sk_env_credential_variables_hold_placeholders_or_are_named() -> None:
    dump = "\n".join([
        "== ENV /proc/1/environ", "HARNESS_TOOLS_TOKEN=pxh-tools-0123456789abcdef", "OPENAI_API_KEY=pxh-ph-" + "a" * 32,
        "PATH=/usr/bin", "MAX_TOKENS=4096", "HTTPS_PROXY=http://10.0.0.2:8097",
        "== ENV /proc/7/environ", f"ANTHROPIC_API_KEY={VENDOR}", "AUTH_MODE=oauth",
        "== CMD /proc/7/cmdline", "opencode serve",
    ])
    envs = sb.environments(dump)
    assert set(envs) == {"1", "7"}
    placeholders, other = sb.credential_variables(envs)
    assert placeholders == ["HARNESS_TOOLS_TOKEN", "OPENAI_API_KEY"]
    assert other == ["ANTHROPIC_API_KEY"]


# --- SD-READ ------------------------------------------------------------------------------------------------------


def test_sd_read_direct_names_and_ports() -> None:
    out = ("DNS api.openai.com unresolved\nDNS tool-gateway 10.2.0.3,\nTCP 10.1.0.5 8080 closed\n"
           "TCP 1.2.3.4 443 open\n")
    names, ports, done = sb.read_direct(out + "DIRECT_DONE")
    assert names == {"api.openai.com": [], "tool-gateway": ["10.2.0.3"]}
    assert ports == {("10.1.0.5", 8080): False, ("1.2.3.4", 443): True}
    assert done and not sb.read_direct(out)[2]


def test_sd_read_proxy_refusals_and_a_let_through() -> None:
    out = "\n".join([
        "PROXY absolute:api.openai.com HTTP/1.1 403 Forbidden {\"reason\":\"destination_not_allowed\"}",
        "PROXY connect:api.anthropic.com silent",
        "PROXY absolute:pxh-fake-llm HTTP/1.1 200 OK {\"object\":\"list\"}",
        "PROXY gateway:tools HTTP/1.1 200 OK {\"jsonrpc\":\"2.0\"}",
        "PROXY_DONE",
    ])
    answers = sb.read_proxy(out)
    assert sb.refused(answers["absolute:api.openai.com"]) and sb.refused(answers["connect:api.anthropic.com"])
    assert not sb.refused(answers["absolute:pxh-fake-llm"])
    assert answers["gateway:tools"][0] == 200 and "PROXY_DONE" not in answers


def test_sd_scripts_name_every_target() -> None:
    direct = sb.direct_script(["api.openai.com"], [("10.0.0.9", 8096)])
    assert "bg dns api.openai.com" in direct and "bg tcp 10.0.0.9 8096" in direct and "unset HTTP_PROXY" in direct
    proxy = sb.via_proxy_script({"absolute:x": sb.absolute("http://x/", "x"), "connect:y": sb.connect("y", 443)})
    assert "ask absolute:x 'GET http://x/ HTTP/1.1" in proxy and "CONNECT y:443" in proxy


def test_sg_rpc_reads_an_event_stream_answer_and_a_refusal() -> None:
    listed = 'HTTP/1.0 200 OK\ncontent-type: text/event-stream\n\nevent: message\ndata: {"jsonrpc":"2.0","id":1,' \
             '"result":{"tools":[{"name":"mcp_e2ekey_lookup_note"}]}}\nRPC_DONE'
    status, answer = sb.read_rpc(listed)
    assert status == 200 and answer["result"]["tools"][0]["name"] == "mcp_e2ekey_lookup_note"
    refused = 'HTTP/1.0 200 OK\n\n{"jsonrpc":"2.0","id":1,"result":{"isError":true,"content":[{"type":"text",' \
              '"text":"tool x is not granted to this run"}]}}'
    assert not sb.rpc_ran(*sb.read_rpc(refused))
    assert not sb.rpc_ran(*sb.read_rpc('HTTP/1.1 403 Forbidden\n\n{"error":"destination_not_allowed"}'))
    assert sb.rpc_ran(200, {"jsonrpc": "2.0", "result": {"content": []}})
    body = 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"get_issue","description":' \
           '"Fetch an issue \u2014 by key"}]}}\r\n\r\n'
    raw = body.encode()
    cut = 20
    chunked = (f"{cut:x}\r\n".encode() + raw[:cut] + b"\r\n" + f"{len(raw) - cut:x}\r\n".encode() + raw[cut:]
               + b"\r\n0\r\n\r\n")
    answer = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + chunked + b"\nRPC_DONE\n"
    status, parsed = sb.read_rpc(answer.decode())
    assert status == 200 and parsed["result"]["tools"][0]["description"] == "Fetch an issue \u2014 by key"
    assert json.loads(sb.rpc_body("tools/list", {})) == {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


# --- SK-WATCH -----------------------------------------------------------------------------------------------------


def _fake_saas() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ["FAKE_SAAS_CONTROL_TOKEN"] = "control"
    os.environ.setdefault("FAKE_SAAS_JIRA_TOKEN", "pat-canary-r31seckey")
    spec = importlib.util.spec_from_file_location("r31seckey_fake_saas", E2E / "fake_saas" / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTROL = {"X-E2E-Control": "control"}


@pytest.fixture
def saas() -> TestClient:
    with TestClient(_fake_saas().app()) as client:
        yield client


def test_sk_watch_records_header_query_and_body_hits_by_digest(saas: TestClient) -> None:
    header, query, body = (f"pipeshub-canary-{n}" * 2 for n in ("aa11", "bb22", "cc33"))
    assert saas.post("/__control/canaries", json={"values": [header, query, body, "unseen-canary-value"]},
                     headers=CONTROL).json()["watching"] == 4
    saas.get("/searxng/search", params={"q": "x", "format": "json"}, headers={"Authorization": f"Bearer {header}"})
    saas.get("/healthz", params={"key": query, "server": "e2ekey"})
    saas.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": body})
    hits = saas.get("/__control/canaries", headers=CONTROL).json()["hits"]
    digests = {h["canaryDigest"]: h for h in hits}
    for value, path in ((header, "/searxng/search"), (query, "/healthz"), (body, "/oauth/token")):
        assert digests[hashlib.sha256(value.encode()).hexdigest()[:16]]["path"] == path
    assert digests[hashlib.sha256(query.encode()).hexdigest()[:16]]["server"] == "e2ekey"
    assert len(hits) == 3 and not any(v in json.dumps(hits) for v in (header, query, body))


def test_sk_watch_control_routes_and_an_unwatched_stack_record_nothing(saas: TestClient) -> None:
    saas.post("/__control/canaries", json={"values": []}, headers=CONTROL)
    saas.get("/healthz", params={"key": "pipeshub-canary-x"})
    assert saas.get("/__control/canaries", headers=CONTROL).json()["hits"] == []
    assert saas.get("/__control/canaries").status_code == 401


def test_sk_watch_searxng_answers_json_and_oauth_refuses(saas: TestClient) -> None:
    found = saas.get("/searxng/search", params={"q": "travel", "format": "json"}).json()
    assert [r["title"] for r in found["results"]] == ["Travel policy", "Expenses"]
    assert saas.get("/searxng/search", params={"q": "travel"}).status_code == 403
    assert saas.post("/oauth/token", data={"grant_type": "refresh_token"}).status_code == 400


# --- SK-INV -------------------------------------------------------------------------------------------------------


def test_sk_inv_canaries_are_unique_and_vendor_shaped_where_named() -> None:
    planted = inv.plan()
    values = [c.value for c in planted]
    assert len(values) == len(set(values)) and len({c.name for c in planted}) == len(planted)
    shaped = {c.name: c.value for c in planted}
    assert "anthropic_key" in find_forbidden(shaped["llm.anthropic.apiKey"])
    assert "openai_key" in find_forbidden(shaped["llm.openAI.apiKey"])
    assert "google_api_key" in find_forbidden(shaped["llm.gemini.apiKey"])
    assert "aws_access_key" in find_forbidden(shaped["llm.bedrock.awsAccessKeyId"])
    assert "github_token" in find_forbidden(shaped["toolset.OAUTH.access_token"])
    assert all(c.value.startswith(inv.CANARY_PREFIX) for c in planted if c.name.startswith("mcp."))


def test_sk_inv_documents_are_tagged_and_the_live_ones_default() -> None:
    canaries = {c.name: c.value for c in inv.plan()}
    models = inv.model_entries(canaries, "http://fake/v1")
    every = [*models["llm"], *models["embedding"]]
    assert every and all(str(e["modelKey"]).startswith(f"{inv.TAG}-") for e in every)
    assert not any(e["isDefault"] for e in models["llm"])
    first = models["embedding"][0]
    assert first["isDefault"] and first["provider"] == "openAICompatible"
    assert sum(1 for e in models["embedding"] if e["isDefault"]) == 1
    vertex = next(e for e in models["llm"] if e["provider"] == "vertexAI")
    assert canaries["llm.vertexAI.serviceAccountJson"] in str(vertex["configuration"]["serviceAccountJson"])
    search = inv.search_entries(canaries, "http://saas")
    assert [e["provider"] for e in search if e["isDefault"]] == ["searxng"]
    assert next(e for e in search if e["provider"] == "searxng")["configuration"]["baseUrl"] == "http://saas/searxng"
    assert all(str(e["providerKey"]).startswith(f"{inv.TAG}-") for e in search)


# --- SK-HOLD: the scripted model's hold lasts as long as the lane asks, within a bound, until the next reset ---------


async def test_sk_hold_limit_is_bounded_and_reset_puts_it_back() -> None:
    from aiohttp.test_utils import TestClient as AioClient
    from aiohttp.test_utils import TestServer

    os.environ.setdefault("FAKE_LLM_API_KEY", "sk-e2e-r31seckey-test-key-0000")
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "control")
    spec = importlib.util.spec_from_file_location("r31seckey_fake_llm", E2E / "fake_llm" / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    control = {"X-E2E-Control": os.environ["FAKE_LLM_CONTROL_TOKEN"]}
    model = module.FakeModel()
    client = AioClient(TestServer(module.app(model)))
    await client.start_server()
    try:
        assert (await client.post("/__control/hold-limit", json={"seconds": 900})).status == 401
        answer = await (await client.post("/__control/hold-limit", json={"seconds": 900}, headers=control)).json()
        assert answer == {"holdLimitSeconds": 900.0} and model.hold_limit_s == 900.0
        await client.post("/__control/hold-limit", json={"seconds": 10**6}, headers=control)
        assert model.hold_limit_s == module.HOLD_LIMIT_MAX_S
        await client.post("/__control/reset", json={}, headers=control)
        assert model.hold_limit_s == module.HOLD_LIMIT_S
    finally:
        await client.close()
