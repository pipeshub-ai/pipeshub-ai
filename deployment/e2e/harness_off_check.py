"""R50-OFF (ADR-OPS-06): a stack with the agent harness switched off (the opt-out, AGENT_HARNESS_WORKER_ENABLED=false;
the switch is on by default since 2026-11-20) serves what a release without the harness serves, and nothing of it.

    python3 harness_off_check.py <run dir>      e.g. ~/.cache/pxh-e2e/<run>   (run.sh off / off-up)

Checks, on the stack's scripted model and through its public API:

- no harness process in the app container: no worker, no Model Gateway;
- Labs lists, answers and saves none of the harness's flags; a save naming one is a plain 400;
- the effective flags answer no rollout and no harness surface;
- the harness's public routes answer 404, as an unknown path does;
- scheduling is not the harness's (ADR-SCH-02): Labs lists Scheduled runs, and the schedule routes are served and
  answer 404 "not enabled for this organization" while that flag is off (its default);
- the classic paths work: a default chat, a chat with the seeded agent, semantic search, a personal access token
  on the REST API, and PipesHub's own /mcp as an external MCP client calls it (initialize, tools/list,
  pipeshub_chat).

Prints one line per check and exits non-zero when any failed. Never prints a token or a password.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HARNESS_LABS = ("ENABLE_AGENT_HARNESS", "ENABLE_KNOWLEDGE_PUBLICATIONS")
SCHEDULES_LAB = "ENABLE_SCHEDULED_RUNS"
HARNESS_ROUTES = (
    ("GET", "/api/v1/agent-harness/runs"),
    ("GET", "/api/v1/agent-harness/inbox"),
    ("GET", "/api/v1/agent-harness/model-accounts/access"),
    ("GET", "/api/v1/agent-harness/admin/dependencies"),
    ("POST", "/api/v1/conversations/runs"),
)
SCHEDULE_ROUTES = (
    ("GET", "/api/v1/agent-harness/schedules"),
    ("POST", "/api/v1/agent-harness/schedules"),
)


class Checks:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, passed: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}{f': {detail}' if detail and not passed else ''}")
        if not passed:
            self.failed.append(name)


def _mcp(base: str, token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": secrets.token_hex(4), "method": method, "params": params}).encode()
    request = urllib.request.Request(f"{base}/mcp", data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=300) as answer:
        raw = answer.read().decode()
    lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")] or [raw]
    return json.loads(lines[-1])


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()
    os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
    here = Path(__file__).resolve().parent
    sys.path[:0] = [str(here), str(here.parents[1] / "integration-tests/harness/e2e")]
    from lane import Http, Stack

    stack = Stack.attach(run_dir.name, "harness-off")
    api = stack.api
    out = Checks()

    processes = stack.exec_app("ps", "-eo", "args")
    out.check("no durable worker runs (app.harness_main)", "app.harness_main" not in processes)
    out.check("no Model Gateway runs (app.model_gateway_main)", "app.model_gateway_main" not in processes)
    out.check("the classic services run (Node, query, connectors)",
              all(name in processes for name in ("dist/index.js", "app.query_main", "app.connectors_main")))

    available = api.ok("GET", "/api/v1/configurationManager/platform/feature-flags/available") or {}
    keys = {flag.get("key") for flag in available.get("flags") or []}
    out.check("Labs lists no harness flag", not keys & set(HARNESS_LABS), str(sorted(keys & set(HARNESS_LABS))))
    out.check("Labs still lists the platform's flags", "ENABLE_MCP" in keys)
    out.check("Labs lists Scheduled runs (not the harness's)", SCHEDULES_LAB in keys)
    effective = api.ok("GET", "/api/v1/configurationManager/platform/feature-flags/effective") or {}
    flags = effective.get("featureFlags") or {}
    out.check("the effective flags answer no rollout", effective.get("agentHarness") is None)
    out.check("the effective flags carry no harness flag", not set(flags) & set(HARNESS_LABS))
    out.check("no harness surface is offered",
              not any(effective.get(k) for k in ("continuePrivately", "engineRuns", "harnessReconcile")))
    refused = api.request("POST", "/api/v1/configurationManager/platform/settings",
                          {"featureFlags": {"ENABLE_AGENT_HARNESS": True}})
    out.check("a Labs save naming the harness is a plain 400", refused.status == 400
              and "not enabled on this install" in json.dumps(refused.body), f"{refused.status}")

    # The harness's own namespace answers a JSON 404 that says why, for every method; a family nested in a classic
    # prefix reaches the classic router, which answers as on a release without the harness.
    for method, path in HARNESS_ROUTES:
        got = api.request(method, path, {} if method == "POST" else None)
        said = "not enabled on this install" in json.dumps(got.body) or not path.startswith("/api/v1/agent-harness/")
        out.check(f"{method} {path} is not served", got.status == 404 and said, f"{got.status} {str(got.body)[:80]!r}")

    # Scheduling is served on every install; with the org's flag off (its default) it is not found, and says why.
    for method, path in SCHEDULE_ROUTES:
        got = api.request(method, path, {} if method == "POST" else None)
        said = "not enabled for this organization" in json.dumps(got.body)
        out.check(f"{method} {path} is served and off for the org", got.status == 404 and said,
                  f"{got.status} {str(got.body)[:80]!r}")

    for label, base, mode in (("a default chat", "/api/v1/conversations", "internal_search"),
                              ("a chat with an agent", f"/api/v1/agents/{stack.seed['agentKey']}/conversations", "quick")):
        marker = f"off-{secrets.token_hex(3)}"
        stream = api.stream("POST", f"{base}/stream", {
            "query": f"say {marker}", "chatMode": mode, "recordIds": [], "filters": {},
            "modelKey": stack.seed["llmKey"], "modelName": "e2e-llm"})
        said = json.dumps([f.data for f in stream.frames])
        kinds = sorted({f.type for f in stream.frames})
        out.check(f"{label} answers on the classic path", stream.status == 200 and marker in said,
                  f"status {stream.status} {stream.error or ''} frames {kinds}")
        # A durable admission names its run in the first frame; the classic loop's AG-UI frames (as on main) do not.
        out.check(f"{label} admits no durable run", '"durable"' not in said and "run.admitted" not in said)

    search = api.request("POST", "/api/v1/search", {"query": "quarterly plan", "limit": 5})
    out.check("semantic search answers", search.status == 200, str(search.status))

    made = api.ok("POST", "/api/v1/personal-access-tokens", {"name": f"off-{secrets.token_hex(3)}", "expiryDays": 30})
    token_id, token = str(made["token"]["id"]), str(made["token"]["accessToken"])
    try:
        bearer = Http(api.base, {"Authorization": f"Bearer {token}"})
        # A token is granted the install's MCP scopes unless it names others (as on main); read what it may.
        scopes = set(made["token"].get("scopes") or [])
        if "conversation:read" in scopes:
            read = bearer.request("GET", "/api/v1/conversations")
        else:
            read = bearer.request("POST", "/api/v1/search", {"query": "quarterly plan", "limit": 5})
        out.check("a personal access token reads the REST API", read.status == 200,
                  f"{read.status} with scopes {sorted(scopes)}")
        init = _mcp(api.base, token, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                    "clientInfo": {"name": "r50off", "version": "1"}})
        out.check("/mcp initializes for an external client", "result" in init, json.dumps(init)[:200])
        tools = _mcp(api.base, token, "tools/list", {})
        names = {t.get("name") for t in (tools.get("result") or {}).get("tools") or []}
        out.check("/mcp lists its tools", "pipeshub_chat" in names, str(sorted(names))[:200])
        marker = f"mcp-{secrets.token_hex(3)}"
        chat = _mcp(api.base, token, "tools/call", {"name": "pipeshub_chat", "arguments": {
            "query": f"say {marker}", "modelKey": stack.seed["llmKey"], "modelName": "e2e-llm"}})
        out.check("/mcp's pipeshub_chat answers on the classic path", marker in json.dumps(chat), json.dumps(chat)[:200])
    except (urllib.error.URLError, KeyError, ValueError) as error:
        out.check("/mcp with a personal access token", False, f"{type(error).__name__}: {error}")
    finally:
        api.request("DELETE", f"/api/v1/personal-access-tokens/{token_id}")

    print(f"{'passed' if not out.failed else 'FAILED'}: {len(out.failed)} failed")
    return 1 if out.failed else 0


if __name__ == "__main__":
    sys.exit(main())
