"""Seeds the E2E stack through Node's public API, as an operator and an org admin would.

    python3 seed.py seed       org + admin, onboarding done, Labs ENABLE_PROJECTS, the fake LLM and embedding
                               models, an agent
    python3 seed.py durable    (run.sh durable) Labs ENABLE_AGENT_HARNESS and ENABLE_MCP, the scripted SaaS as
                               an MCP server instance, an engine agent (engineProfile {}) with its write tool
    python3 seed.py harness    (run.sh harness, after durable) a chat-loop agent with the same write tool

Reads E2E_API (Node's base URL), E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD, E2E_LLM_KEY, E2E_STATE (a JSON
file it writes the ids to) and E2E_ENGINE (the engine agent's coding engine, engines.py; default the release's). Standard library only: it runs on the host.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import engines

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "integration-tests", "harness", "e2e"))
from stack_signin import org_access_token

API = os.environ["E2E_API"].rstrip("/")
EMAIL = os.environ["E2E_ADMIN_EMAIL"]
PASSWORD = os.environ["E2E_ADMIN_PASSWORD"]
STATE = os.environ["E2E_STATE"]
FAKE_LLM_ENDPOINT = "http://pxh-fake-llm:8080/v1"
AGENT_NAME = "E2E Analyst"
USER_SYNC_WAIT_S = 90
HARNESS_FLAG = "ENABLE_AGENT_HARNESS"
MCP_FLAG = "ENABLE_MCP"
# Off by default since main #4785, and the projects API answers 403 while it is off (R46LANE-FU-2).
PROJECTS_FLAG = "ENABLE_PROJECTS"
ENGINE_AGENT_NAME = "E2E Engine Analyst"
TOOL_AGENT_NAME = "E2E Ticket Agent"
MCP_NAME = "e2esaas"
MCP_TOOL = "create_ticket"
FAKE_SAAS_MCP = "http://pxh-fake-saas:8090/mcp"


class SeedError(RuntimeError):
    pass


def call(method: str, path: str, body: Any = None, token: str | None = None,  # noqa: ANN401
         headers: dict[str, str] | None = None, timeout: float = 120) -> tuple[Any, dict[str, str]]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return (json.loads(raw) if raw else None), dict(response.headers)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:600]
        raise SeedError(f"{method} {path} -> {error.code}: {detail}") from None


def sign_in() -> str:
    _, headers = call("POST", "/api/v1/userAccount/initAuth", {"email": EMAIL})
    session = next((v for k, v in headers.items() if k.lower() == "x-session-token"), None)
    if not session:
        raise SeedError("initAuth returned no x-session-token")
    answer, _ = call("POST", "/api/v1/userAccount/authenticate",
                     {"method": "password", "credentials": {"password": PASSWORD}, "email": EMAIL},
                     headers={"x-session-token": session})
    return org_access_token(answer or {}, lambda method, path, body, token: call(method, path, body, token=token)[0])


def load_state() -> dict[str, Any]:
    try:
        with open(STATE) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def save_state(state: dict[str, Any]) -> None:
    with open(STATE, "w") as handle:
        json.dump(state, handle, indent=2)


def has_default(token: str, model_type: str) -> bool:
    models, _ = call("GET", "/api/v1/configurationManager/ai-models", token=token)
    return any(m.get("isDefault") for m in ((models or {}).get("models") or {}).get(model_type) or [])


def add_model(token: str, model_type: str, model: str) -> str:
    # A re-seed never takes the default from a model a person chose (routefix).
    default = not has_default(token, model_type)
    answer, _ = call("POST", "/api/v1/configurationManager/ai-models/providers", {
        "modelType": model_type,
        "provider": "openAICompatible",
        "configuration": {"endpoint": FAKE_LLM_ENDPOINT, "apiKey": os.environ["E2E_LLM_KEY"], "model": model},
        "isMultimodal": False,
        "isReasoning": False,
        "isDefault": default,
        "contextLength": None,
    }, token=token, timeout=180)
    key = ((answer or {}).get("details") or {}).get("modelKey")
    if not key:
        raise SeedError(f"adding the {model_type} model returned no model key")
    return str(key)


def set_flags(token: str, wanted: dict[str, bool]) -> None:
    current, _ = call("GET", "/api/v1/configurationManager/platform/settings", token=token)
    current = current or {}
    flags = dict(current.get("featureFlags") or {})
    flags.update(wanted)
    body: dict[str, Any] = {"featureFlags": flags}
    if current.get("fileUploadMaxSizeBytes"):
        body["fileUploadMaxSizeBytes"] = current["fileUploadMaxSizeBytes"]
    else:
        body["fileUploadMaxSizeBytes"] = 30 * 1024 * 1024
    if current.get("agentHarness") is not None:
        body["agentHarness"] = current["agentHarness"]
    call("POST", "/api/v1/configurationManager/platform/settings", body, token=token)
    stored, _ = call("GET", "/api/v1/configurationManager/platform/settings", token=token)
    effective, _ = call("GET", "/api/v1/configurationManager/platform/feature-flags/effective", token=token)
    for name, on in wanted.items():
        got = ((effective or {}).get("featureFlags") or {}).get(name, ((stored or {}).get("featureFlags") or {}).get(name))
        if got is not on:
            raise SeedError(f"{name} reads {got!r} after setting it to {on}")


def harness_reason(token: str) -> str:
    effective, _ = call("GET", "/api/v1/configurationManager/platform/feature-flags/effective", token=token)
    harness = (effective or {}).get("agentHarness") or {}
    return "enabled" if harness.get("useHarness") else str(harness.get("reason") or "unknown")


def create_mcp_instance(token: str) -> str:
    answer, _ = call("POST", "/api/v1/mcp-servers/instances", {
        "name": MCP_NAME,
        "transport": "streamable_http",
        "authMode": "none",
        "url": FAKE_SAAS_MCP,
    }, token=token)
    instance = (answer or {}).get("instance") or answer or {}
    instance_id = str(instance.get("_id") or instance.get("instanceId") or "")
    if not instance_id:
        raise SeedError(f"the MCP instance create returned no id: {json.dumps(answer)[:300]}")
    return instance_id


def approve_mcp_tools(token: str, instance_id: str) -> None:
    """With the MCP hub on (run.sh E2E_MCP_HUB=1) an agent takes only tools an admin approved: approve what the
    scripted SaaS lists now, as the admin's MCP server page does. A stack without the hub has nothing to approve, and
    neither has an open-source build, which has no pre-approval: there the route falls through to the web app's page."""
    mine, _ = call("GET", "/api/v1/mcp-servers/my-mcp-servers?includeTools=false", token=token)
    if not (mine or {}).get("mcpHub"):
        return
    try:
        manifest, _ = call("GET", f"/api/v1/mcp-servers/instances/{instance_id}/manifest", token=token)
    except json.JSONDecodeError:
        return
    tools = [{"name": t["name"], "digest": t["digest"]} for t in (manifest or {}).get("tools") or []]
    call("PUT", f"/api/v1/mcp-servers/instances/{instance_id}/manifest", {"tools": tools}, token=token)


def grant_mcp(token: str, key: str, mcp_instance: str) -> None:
    call("PUT", f"/api/v1/agents/{key}", {
        "mcpServers": [{
            "instanceId": mcp_instance,
            "name": MCP_NAME,
            "tools": [{"name": MCP_TOOL, "fullName": f"mcp_{MCP_NAME}_{MCP_TOOL}"}],
        }],
    }, token=token)


def create_tool_agent(token: str, mcp_instance: str, name: str, description: str, *, engine: bool) -> str:
    """An agent with the scripted SaaS's write tool: an engine agent (``engineProfile``: ``{}`` for the release
    default engine, else E2E_ENGINE's alone) or a chat-loop one."""
    body: dict[str, Any] = {"name": name, "description": description,
                            "systemPrompt": "You are an analyst working in a sandboxed workspace."}
    try:
        profile = engines.profile(os.environ.get("E2E_ENGINE")) if engine else None
    except KeyError as error:
        raise SeedError(error.args[0]) from None
    if engine:
        body["engineProfile"] = profile
    answer, _ = call("POST", "/api/v1/agents/create", body, token=token)
    agent = (answer or {}).get("agent") or answer or {}
    key = str(agent.get("_key") or agent.get("agentKey") or "")
    if not key or (engine and agent.get("engineProfile") != profile):
        raise SeedError(f"the agent {name!r} was not stored as asked: {json.dumps(answer)[:300]}")
    grant_mcp(token, key, mcp_instance)
    stored, _ = call("GET", f"/api/v1/agents/{key}", token=token)
    stored_agent = (stored or {}).get("agent") or {}
    if (engine and stored_agent.get("engineProfile") != profile) or not stored_agent.get("mcpServers"):
        raise SeedError(f"the agent {name!r} reads back without its profile or tool: {json.dumps(stored)[:400]}")
    return key


def require_live(token: str, state: dict[str, Any], *, model: bool = False, mcp: bool = False) -> None:
    """A refresh reuses the ids in E2E_STATE, but the model and MCP server live in the config store (Redis), which
    can lose them while Mongo keeps the org and agents (routefix): a lost one would surface only as a 403 or a
    RUN_ERROR in the smoke, so it is reported here instead. E2E_SEED_HEAL=1 forgets the lost ids instead, so this
    seed makes them again and the steps that depend on them (an agent's grant, the approval rule) follow."""
    lost: dict[str, str] = {}
    if model and state.get("llmKey"):
        models, _ = call("GET", "/api/v1/configurationManager/ai-models", token=token)
        if state["llmKey"] not in json.dumps(models):
            lost["llmKey"] = f"model e2e-llm ({state['llmKey']})"
    if mcp and state.get("mcpInstanceId"):
        try:
            call("GET", f"/api/v1/mcp-servers/instances/{state['mcpInstanceId']}", token=token)
        except SeedError:
            lost["mcpInstanceId"] = f"MCP server {MCP_NAME} ({state['mcpInstanceId']})"
    if not lost:
        return
    what = ", ".join(lost.values())
    if os.environ.get("E2E_SEED_HEAL") != "1":
        raise SeedError(f"the config store no longer holds the seeded {what}; bring the stack up afresh, "
                        "or seed again with E2E_SEED_HEAL=1 to make them again")
    print(f"E2E seed: re-seeding the lost {what}", file=sys.stderr)
    for key in lost:
        state.pop(key)
    save_state(state)


def seed_durable() -> None:
    """The durable lane (E2E2): Labs durable runs and MCP on, the SaaS attached to an engine agent."""
    state = load_state()
    token = sign_in()
    if os.environ.get("E2E_MONGO") == "standalone":
        # No replica set, no durable runs (04 path standalone_mongo): the install says so, and the Labs flag
        # cannot turn them on. The MCP server and the agents are the same.
        set_flags(token, {MCP_FLAG: True})
        reason = harness_reason(token)
        if reason != "infrastructure_unavailable":
            raise SeedError(f"a standalone MongoDB install reports durable admission as {reason}")
    else:
        set_flags(token, {HARNESS_FLAG: True, MCP_FLAG: True})
        reason = harness_reason(token)
        if reason != "enabled":
            raise SeedError(f"durable admission is off for the org after the Labs flag ({reason})")
    require_live(token, state, mcp=True)
    if not state.get("mcpInstanceId"):
        state["mcpInstanceId"] = create_mcp_instance(token)
        save_state(state)
        approve_mcp_tools(token, state["mcpInstanceId"])
        # A re-seeded server (E2E_SEED_HEAL) has a new id: the agents seeded before it take it.
        for key in ("engineAgentKey", "toolAgentKey"):
            if state.get(key):
                grant_mcp(token, state[key], state["mcpInstanceId"])
    engines.prepare_org(lambda method, path, body: call(method, path, body, token=token)[0],
                        [os.environ.get("E2E_ENGINE") or engines.default_kind()])
    if not state.get("engineAgentKey"):
        state["engineAgentKey"] = create_tool_agent(
            token, state["mcpInstanceId"], ENGINE_AGENT_NAME, "Works in a coding engine and files tickets.", engine=True)
        save_state(state)
    print(json.dumps({k: v for k, v in state.items() if k != "token"}))


def seed_harness() -> None:
    """The harness lane (QA-5): a chat-loop agent with the scripted SaaS's write tool, after ``durable``."""
    state = load_state()
    if not state.get("mcpInstanceId"):
        raise SeedError("seed.py durable runs first: no MCP instance in the state")
    token = sign_in()
    if not state.get("toolAgentKey"):
        state["toolAgentKey"] = create_tool_agent(
            token, state["mcpInstanceId"], TOOL_AGENT_NAME, "Files tickets in the team's tracker.", engine=False)
        save_state(state)
    print(json.dumps({k: v for k, v in state.items() if k != "token"}))


def create_agent(token: str) -> Any:  # noqa: ANN401
    # The query service finds the user in the graph, which the connector service writes from Node's
    # entity event a moment after the org is created: until then the route answers "User not found".
    deadline = time.monotonic() + USER_SYNC_WAIT_S
    while True:
        try:
            answer, _ = call("POST", "/api/v1/agents/create", {
                "name": AGENT_NAME,
                "description": "Analyses data with a code task.",
                "systemPrompt": "You are an analyst.",
            }, token=token)
            return answer
        except SeedError as error:
            if "User not found" not in str(error) or time.monotonic() > deadline:
                raise
            time.sleep(2)


def seed() -> None:
    state = load_state()
    if not state.get("orgId"):
        org, _ = call("POST", "/api/v1/org", {
            "accountType": "business",
            "adminFullName": "E2E Admin",
            "contactEmail": EMAIL,
            "password": PASSWORD,
            "confirmPassword": PASSWORD,
            "registeredName": "E2E Org",
            "shortName": "E2E",
        })
        state["orgId"] = str((org or {}).get("_id") or (org or {}).get("orgId") or "")
        save_state(state)
    token = sign_in()
    call("PUT", "/api/v1/org/onboarding-status", {"status": "configured"}, token=token)
    set_flags(token, {PROJECTS_FLAG: True})
    require_live(token, state, model=True)
    if not state.get("llmKey"):
        state["llmKey"] = add_model(token, "llm", "e2e-llm")
        save_state(state)
    # A person's stack (run.sh human-up) keeps the product's default local embedding model.
    if not state.get("embeddingKey") and os.environ.get("E2E_SEED_EMBEDDING", "1") == "1":
        state["embeddingKey"] = add_model(token, "embedding", "e2e-embedding")
        save_state(state)
    if not state.get("agentKey"):
        answer = create_agent(token)
        agent = (answer or {}).get("agent") or answer or {}
        state["agentKey"] = str(agent.get("_key") or agent.get("agentKey") or "")
        if not state["agentKey"]:
            raise SeedError(f"agent create returned no key: {json.dumps(answer)[:300]}")
        save_state(state)
    print(json.dumps({k: v for k, v in state.items() if k != "token"}))


def main() -> None:
    try:
        if sys.argv[1:] == ["seed"]:
            seed()
        elif sys.argv[1:] == ["durable"]:
            seed_durable()
        elif sys.argv[1:] == ["harness"]:
            seed_harness()
        else:
            raise SeedError("usage: seed.py seed | durable | harness")
    except SeedError as error:
        print(f"E2E seed: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
