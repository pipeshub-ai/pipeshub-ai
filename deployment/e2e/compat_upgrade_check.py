"""R51-COMPAT (ADR-OPS-07): an install of a release without the security profile, upgraded to this tree with nothing
hand-edited, keeps working as before; rolled back, it still works.

    python3 compat_upgrade_check.py seed  <state.json>   on the release's stack: an org, the scripted model, a
                                                           knowledge base with a file, a personal access token, a
                                                           chat, a refresh token, as its clients would hold them
    python3 compat_upgrade_check.py write <state.json>   on the upgraded stack: configuration written after the upgrade
                                                           (a model, encrypted with the platform key), which `check`
                                                           then uses: after a rollback it proves the release reads it
    python3 compat_upgrade_check.py check <state.json>   after the upgrade (or the rollback): what was seeded still
                                                           works, and the client-visible behaviour is the release's

Environment: COMPAT_API (Node's base URL), COMPAT_EMAIL, COMPAT_PASSWORD, COMPAT_LLM_KEY, COMPAT_LLM_ENDPOINT (the
scripted model as the app reaches it), COMPAT_PROFILE (`compat`, the default, or `hardened`: what `check` expects),
COMPAT_HARNESS (`off`, the default: the harness's routes are not served; `any`: not checked, for a rollback to a
release that never had them). Standard library only; never prints a token or a password. Exits non-zero when a
check failed.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "integration-tests/harness/e2e")]

from lane import Api, Http, multipart, read_sse  # noqa: E402

KB_TEXT = "# Compat plan\n\nThe compat51 quarterly plan ships the blue widget on the ninth of March.\n"
# Rotation (ADR-WEB-02 D9) lets the presented token through again only inside this grace; past it, a replayed one
# ends the family. Waiting past it proves the release's behaviour: the same token keeps working.
REFRESH_GRACE_S = 61


class Checks:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.passed = 0

    def check(self, name: str, passed: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}{f': {detail}' if detail and not passed else ''}", flush=True)
        if passed:
            self.passed += 1
        else:
            self.failed.append(name)


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"{name} is required")
    return value


def _api() -> Api:
    return Api(_env("COMPAT_API"), _env("COMPAT_EMAIL"), _env("COMPAT_PASSWORD"))


def _password_answer(base: str) -> dict[str, Any]:
    """The release's password sign-in, whose answer carries the refresh token a scripted client keeps."""
    http = Http(base)
    first = http.request("POST", "/api/v1/userAccount/initAuth", {"email": _env("COMPAT_EMAIL")})
    session = next((v for k, v in first.headers.items() if k.lower() == "x-session-token"), "")
    answer = http.request("POST", "/api/v1/userAccount/authenticate", {
        "method": "password", "credentials": {"password": _env("COMPAT_PASSWORD")}, "email": _env("COMPAT_EMAIL"),
    }, headers={"x-session-token": session})
    if answer.status != 200 or not isinstance(answer.body, dict):
        raise SystemExit(f"password sign-in -> {answer.status}")
    return answer.body


def _refresh(base: str, refresh_token: str) -> Any:  # noqa: ANN401
    return Http(base).request("POST", "/api/v1/userAccount/refresh/token", {},
                              headers={"Authorization": f"Bearer {refresh_token}"})


def _mcp(base: str, token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(f"{base}/mcp", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=240) as response:
        raw = response.read().decode()
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    frames = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    return json.loads(frames[-1]) if frames else {}


def _chat(api: Api, model_key: str, marker: str) -> tuple[int, str, str]:
    stream = api.stream("POST", "/api/v1/conversations/stream", {
        "query": f"say {marker}", "chatMode": "internal_search", "recordIds": [], "filters": {},
        "modelKey": model_key, "modelName": "e2e-llm"})
    said = json.dumps([f.data for f in stream.frames])
    conversation = ""
    for frame in stream.frames:
        found = (frame.data.get("conversation") or {}).get("_id") if isinstance(frame.data, dict) else None
        conversation = str(found or conversation)
    return stream.status, said, conversation


def _add_model(api: Api, model_type: str, model: str) -> str:
    answer = api.ok("POST", "/api/v1/configurationManager/ai-models/providers", {
        "modelType": model_type, "provider": "openAICompatible",
        "configuration": {"endpoint": _env("COMPAT_LLM_ENDPOINT"), "apiKey": _env("COMPAT_LLM_KEY"), "model": model},
        "isMultimodal": False, "isReasoning": False, "isDefault": True, "contextLength": None,
    }, timeout=180)
    return str(((answer or {}).get("details") or {}).get("modelKey") or "")


def seed(state_path: Path) -> int:
    base = _env("COMPAT_API").rstrip("/")
    state: dict[str, Any] = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not state.get("orgId"):
        org = Http(base).request("POST", "/api/v1/org", {
            "accountType": "business", "adminFullName": "Compat Admin", "contactEmail": _env("COMPAT_EMAIL"),
            "password": _env("COMPAT_PASSWORD"), "confirmPassword": _env("COMPAT_PASSWORD"),
            "registeredName": "Compat51 Org", "shortName": "C51"})
        if org.status not in (200, 201) or not isinstance(org.body, dict):
            raise SystemExit(f"org create -> {org.status}: {str(org.body)[:200]}")
        state["orgId"] = str(org.body.get("_id") or org.body.get("orgId") or "")
        state["orgName"] = "Compat51 Org"
    api = _api()
    api.request("PUT", "/api/v1/org/onboarding-status", {"status": "configured"})
    if not state.get("llmKey"):
        state["llmKey"] = _add_model(api, "llm", "e2e-llm")
    if not state.get("embeddingKey"):
        state["embeddingKey"] = _add_model(api, "embedding", "e2e-embedding")
    state_path.write_text(json.dumps(state))
    if not state.get("kbId"):
        # The graph learns of the admin from Node's event a moment after the org exists ("User not found" until then).
        deadline = time.monotonic() + 120
        while True:
            made = api.request("POST", "/api/v1/knowledgeBase/", {"kbName": "Compat51 KB"})
            if made.status in (200, 201) or "User not found" not in json.dumps(made.body) or time.monotonic() > deadline:
                break
            time.sleep(3)
        if made.status not in (200, 201):
            raise SystemExit(f"knowledge base create -> {made.status}: {str(made.body)[:200]}")
        kb = made.body
        state["kbId"] = str((kb or {}).get("id") or (kb or {}).get("_key") or "")
        body, content_type = multipart("files", "compat-plan.md", KB_TEXT.encode(), "text/markdown")
        request = urllib.request.Request(f"{base}/api/v1/knowledgeBase/{state['kbId']}/upload", data=body, method="POST")
        request.add_header("Content-Type", content_type)
        request.add_header("Authorization", f"Bearer {api.token()}")
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            state["uploadStatus"] = response.status
            try:
                list(read_sse(iter(raw.splitlines(keepends=True))))
            except ValueError:
                pass
    if not state.get("pat"):
        made = api.ok("POST", "/api/v1/personal-access-tokens", {"name": f"compat-{secrets.token_hex(3)}",
                                                                   "expiryDays": 30})
        state["patId"], state["pat"] = str(made["token"]["id"]), str(made["token"]["accessToken"])
    if not state.get("conversationId"):
        marker = f"seed-{secrets.token_hex(3)}"
        status, said, conversation = _chat(api, state["llmKey"], marker)
        if status != 200 or marker not in said:
            raise SystemExit(f"the seeded chat did not answer ({status})")
        if not conversation:
            listed = api.ok("GET", "/api/v1/conversations") or {}
            conversation = next((str(c.get("_id")) for c in listed.get("conversations") or []
                                 if marker in str(c.get("title"))), "")
        state["conversationId"], state["conversationMarker"] = conversation, marker
    if not state.get("refreshToken"):
        state["refreshToken"] = str(_password_answer(base).get("refreshToken") or "")
    init = _mcp(base, state["pat"], "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                  "clientInfo": {"name": "compat51", "version": "1"}})
    state["mcpBefore"] = "result" in init
    health = Http(base).request("GET", "/api/v1/health")
    state["healthKeysBefore"] = sorted(health.body) if isinstance(health.body, dict) else []
    state_path.write_text(json.dumps(state))
    print(json.dumps({k: v for k, v in state.items() if k not in ("pat", "refreshToken")}))
    return 0


def write(state_path: Path) -> int:
    state = json.loads(state_path.read_text())
    state["writtenAfterUpgradeKey"] = _add_model(_api(), "llm", "e2e-llm")
    state_path.write_text(json.dumps(state))
    print("a model written after the upgrade")
    return 0


def check(state_path: Path) -> int:
    base = _env("COMPAT_API").rstrip("/")
    hardened = _env("COMPAT_PROFILE", "compat") == "hardened"
    harness = _env("COMPAT_HARNESS", "off")
    state = json.loads(state_path.read_text())
    out = Checks()

    first = _refresh(base, state["refreshToken"])
    out.check("the refresh token a client kept from before the upgrade still refreshes", first.status == 200,
              str(first.status))
    out.check("an access token signs in by password", bool(_api().token()))
    api = _api()
    pat = Http(base, {"Authorization": f"Bearer {state['pat']}"})
    read = pat.request("POST", "/api/v1/search", {"query": "compat51 quarterly plan", "limit": 5})
    out.check("the personal access token from before reads the REST API", read.status == 200, str(read.status))
    old = api.request("GET", f"/api/v1/conversations/{state['conversationId']}")
    out.check("the chat from before opens", old.status == 200 and state["conversationMarker"] in json.dumps(old.body),
              str(old.status))
    marker = f"after-{secrets.token_hex(3)}"
    status, said, _ = _chat(api, state["llmKey"], marker)
    out.check("a new chat answers on the classic path", status == 200 and marker in said, str(status))
    if state.get("writtenAfterUpgradeKey"):
        marker = f"written-{secrets.token_hex(3)}"
        status, said, _ = _chat(api, state["writtenAfterUpgradeKey"], marker)
        out.check("a model configured after the upgrade answers (its encrypted configuration is readable)",
                  status == 200 and marker in said, str(status))
    out.check("a new chat admits no durable run", '"durable"' not in said and "run.admitted" not in said)
    # Classic code execution: the scripted model calls run_code; the sandbox's printed number comes back.
    number = str(40000 + secrets.randbelow(9999))
    code = api.stream("POST", "/api/v1/conversations/stream", {
        "query": f'tool run_code {{"code": "print({number})", "language": "python"}}\nsay finished',
        "chatMode": "agent", "recordIds": [], "filters": {}, "modelKey": state["llmKey"], "modelName": "e2e-llm"})
    ran = json.dumps([f.data for f in code.frames])
    results = []
    for frame in code.frames:
        if frame.type == "TOOL_CALL_RESULT":
            try:
                results.append(json.loads(frame.data.get("content") or "{}"))
            except ValueError:
                results.append({})
    out.check("code execution (run_code) runs as before", code.status == 200 and any(
        number in str(r.get("stdout")) and r.get("exit_code") == 0 for r in results),
        f"{code.status} {code.error or ''} {str(results)[:200]}")
    search = api.request("POST", "/api/v1/search", {"query": "compat51 quarterly plan blue widget", "limit": 5})
    out.check("semantic search finds the knowledge base file indexed before", search.status == 200
              and "blue widget" in json.dumps(search.body), str(search.status))
    kb = api.request("GET", f"/api/v1/knowledgeBase/{state['kbId']}")
    out.check("the knowledge base from before opens", kb.status == 200 and "Compat51 KB" in json.dumps(kb.body),
              str(kb.status))
    connectors = api.request("GET", "/api/v1/connectors/registry")
    out.check("the connectors list answers", connectors.status == 200, str(connectors.status))
    try:
        init = _mcp(base, state["pat"], "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                      "clientInfo": {"name": "compat51", "version": "1"}})
        out.check("/mcp initializes for the MCP client from before", "result" in init, json.dumps(init)[:160])
        tools = _mcp(base, state["pat"], "tools/list", {})
        names = {t.get("name") for t in (tools.get("result") or {}).get("tools") or []}
        out.check("/mcp lists pipeshub_chat", "pipeshub_chat" in names, str(sorted(names))[:160])
    except (urllib.error.URLError, ValueError) as error:
        out.check("/mcp with the personal access token from before", False, f"{type(error).__name__}")

    health = Http(base).request("GET", "/api/v1/health")
    keys = sorted(health.body) if isinstance(health.body, dict) else []
    if hardened:
        out.check("health without a token answers the trimmed shape", "services" not in keys, str(keys))
    else:
        out.check("health without a token answers the release's shape (services listed)",
                  health.status == 200 and "services" in keys and set(state.get("healthKeysBefore") or []) <= set(keys),
                  f"{health.status} {keys}")
    page = Http(base).request("GET", "/")
    headers = {k.lower(): v for k, v in page.headers.items()}
    enforced = headers.get("content-security-policy", "")
    framed_off = headers.get("x-frame-options", "").upper() == "DENY" or "frame-ancestors 'none'" in enforced
    if hardened:
        out.check("the app refuses to be framed", framed_off, str({k: headers.get(k) for k in ("x-frame-options",)}))
    else:
        out.check("the app may be framed, as before", not framed_off,
                  f"x-frame-options={headers.get('x-frame-options')!r} csp={enforced[:80]!r}")
    if harness == "off":
        got = api.request("GET", "/api/v1/agent-harness/runs")
        out.check("the harness's routes are not served", got.status == 404, str(got.status))

    if not hardened:
        time.sleep(REFRESH_GRACE_S)
        again = _refresh(base, state["refreshToken"])
        out.check("the same refresh token refreshes again past the rotation grace (no rotation)", again.status == 200,
                  str(again.status))
    print(f"{'passed' if not out.failed else 'FAILED'}: {out.passed} passed, {len(out.failed)} failed", flush=True)
    return 1 if out.failed else 0


def main() -> int:
    commands = {"seed": seed, "write": write, "check": check}
    if len(sys.argv) != 3 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    return commands[sys.argv[1]](Path(sys.argv[2]))


if __name__ == "__main__":
    sys.exit(main())
