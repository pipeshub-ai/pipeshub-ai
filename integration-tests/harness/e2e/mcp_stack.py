"""MCP servers on a running stack as an admin and a user set them up (MCP-E2E), through Node's public API.

The stack is one ``run.sh matrix-up`` brought up with ``E2E_MCP_HUB=1``: the hub and the STDIO runners are on and
the scripted MCP servers (``deployment/e2e/fake_saas/kinds.py``) answer on the stack's own private network, as a
self-hosted customer's do. Everything here is a call a browser makes, plus the scripted servers' control routes;
the OAuth "browser" follows the provider's redirect by hand and hands the code to the product's callback.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from lane import Api, Control, Stack, StackError

Json = dict[str, Any]
# The scripted servers as the product's services reach them (docker-compose.mcp.yml).
FAKE_MCP = "http://pxh-fake-mcp:8091"
MCP_FLAG = "ENABLE_MCP"
STDIO_PROGRAM = "/opt/pxh-echo/server.py"
STDIO_SECRET_ENV = "ECHO_API_TOKEN"
TRANSPORT_PATH = {"streamable_http": "mcp", "sse": "sse"}
WHOAMI: Json = {"whoami": {"description": "Who the server sees calling it.", "readOnly": True}}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def canary(kind: str) -> str:
    """A credential no other value on the stack resembles, so finding it anywhere is a leak."""
    return f"mcpe2e-{kind}-{secrets.token_hex(12)}"


@dataclass
class Server:
    """One MCP server instance of the org and, for an HTTP kind, the scripted server behind it."""

    name: str
    instance_id: str
    fake_id: str | None
    secrets: list[str] = field(default_factory=list[str])
    tools: dict[str, str] = field(default_factory=dict[str, str])
    """Raw tool name -> the name the product exports it under (the model-visible name, before an engine's prefix)."""


def require_tool_approval(stack: Stack) -> None:
    """D-MCP-5: the cases about the approval gate run only where the install reports it on (an edition's org setting);
    OSS uses discovered tools at once (``test_mcpnote_unlisted_servers.py`` holds that)."""
    # Imported here: the journey runner (deployment/e2e/journeys.py) uses McpAdmin under a Python with no pytest.
    import pytest

    listed = stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers?includeTools=false") or {}
    if listed.get("mcpToolApproval") is not True:
        pytest.skip("D-MCP-5: this install requires no admin approval of MCP tools (an edition's setting)")


class McpAdmin:
    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.api = stack.api
        self.fake = Control(f"http://127.0.0.1:{stack.env['E2E_MCP_PORT']}", stack.env["E2E_LLM_CONTROL"])
        self.suffix = secrets.token_hex(3)

    # --- the scripted servers ---------------------------------------------------------

    def fake_server(self, fake_id: str, **spec: Any) -> Json:  # noqa: ANN401
        return self.fake.ok("PUT", f"/__control/servers/{fake_id}", {"tools": WHOAMI, **spec})

    def set_tools(self, fake_id: str, tools: Json) -> Json:
        return self.fake.ok("PUT", f"/__control/servers/{fake_id}", {"tools": tools})

    def seen(self, fake_id: str) -> Json:
        return self.fake.ok("GET", f"/__control/servers/{fake_id}")

    # --- instances --------------------------------------------------------------------

    def enable(self, on: bool = True) -> None:  # noqa: FBT001, FBT002
        current = self.api.ok("GET", "/api/v1/configurationManager/platform/settings") or {}
        body: Json = {"featureFlags": {**(current.get("featureFlags") or {}), MCP_FLAG: on},
                      "fileUploadMaxSizeBytes": current.get("fileUploadMaxSizeBytes") or 30 * 1024 * 1024}
        if current.get("agentHarness") is not None:
            body["agentHarness"] = current["agentHarness"]
        self.api.ok("POST", "/api/v1/configurationManager/platform/settings", body)

    def create(self, body: Json, *, api: Api | None = None) -> Any:  # noqa: ANN401 - the HTTP answer
        return (api or self.api).request("POST", "/api/v1/mcp-servers/instances", body)

    def http_server(self, kind: str, *, transport: str = "streamable_http", auth_mode: str = "none",
                    fake_auth: str = "none", credential: str | None = None, tools: Json | None = None,
                    **extra: Any) -> Server:  # noqa: ANN401
        """A scripted server and the org's instance pointing at it; the name carries the run's suffix."""
        name = f"{kind}{self.suffix}"
        self.fake_server(name, transport=transport, auth=fake_auth, credential=credential,
                         **({"tools": tools} if tools is not None else {}),
                         **{k: v for k, v in extra.items() if k in ("header", "discoverable")})
        body: Json = {"name": name, "transport": transport, "authMode": auth_mode,
                      "url": f"{FAKE_MCP}/s/{name}/{TRANSPORT_PATH[transport]}",
                      **{k: v for k, v in extra.items() if k not in ("header", "discoverable")}}
        answer = self.create(body)
        if answer.status != 201:
            raise StackError(f"creating the {kind} instance -> {answer.status}: {str(answer.body)[:400]}")
        return Server(name, _instance_id(answer.body), name, [credential] if credential else [])

    def stdio_server(self, kind: str, secret: str) -> Server:
        name = f"{kind}{self.suffix}"
        answer = self.create({"name": name, "transport": "stdio", "authMode": "api_token", "command": "python3",
                              "args": [STDIO_PROGRAM], "requiredEnv": [STDIO_SECRET_ENV]})
        if answer.status != 201:
            raise StackError(f"creating the STDIO instance -> {answer.status}: {str(answer.body)[:400]}")
        return Server(name, _instance_id(answer.body), None, [secret])

    def authenticate(self, server: Server, body: Json, *, api: Api | None = None) -> Any:  # noqa: ANN401
        return (api or self.api).request("POST", f"/api/v1/mcp-servers/instances/{server.instance_id}/authenticate", body)

    def disconnect(self, server: Server, *, api: Api | None = None) -> None:
        (api or self.api).ok("DELETE", f"/api/v1/mcp-servers/instances/{server.instance_id}/credentials")

    def delete(self, server: Server) -> None:
        self.api.ok("DELETE", f"/api/v1/mcp-servers/instances/{server.instance_id}")

    def oauth_connect(self, server: Server, *, api: Api | None = None) -> Json:
        """The user's "Connect": the product's authorize route, the provider's redirect, the product's callback."""
        api = api or self.api
        started = api.ok("GET", f"/api/v1/mcp-servers/instances/{server.instance_id}/oauth/authorize")
        url = urllib.parse.urlsplit(str(started["authorizationUrl"]))
        # The browser reaches the provider from outside the stack: the same path on the server's published port.
        location = _redirect(f"{self.fake.base}{url.path}?{url.query}")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        done = api.ok("GET", "/api/v1/mcp-servers/oauth/callback?" + urllib.parse.urlencode(
            {"code": query["code"][0], "state": query["state"][0]}))
        if not done.get("success"):
            raise StackError(f"the OAuth callback answered {done}")
        return done

    # --- the manifest -----------------------------------------------------------------

    def manifest(self, server: Server, *, api: Api | None = None) -> Any:  # noqa: ANN401
        return (api or self.api).request("GET", f"/api/v1/mcp-servers/instances/{server.instance_id}/manifest")

    def listed_manifest(self, server: Server, *, attempts: int = 4) -> Any:  # noqa: ANN401
        """The manifest once the server answers: a cold STDIO runner can outlast one listing (502/504)."""
        for attempt in range(attempts):
            listed = self.manifest(server)
            if listed.status == 200:
                return listed
            if listed.status not in (502, 504) or attempt == attempts - 1:
                break
            time.sleep(5)
        raise StackError(f"the manifest of {server.name} -> {listed.status}: {str(listed.body)[:400]}")

    def put_manifest(self, server: Server, tools: list[Json], *, api: Api | None = None) -> Any:  # noqa: ANN401
        return (api or self.api).request("PUT", f"/api/v1/mcp-servers/instances/{server.instance_id}/manifest",
                                         {"tools": tools})

    def approve(self, server: Server, names: list[str] | None = None) -> Json:
        """Approves ``names`` (every listed tool the name rule admits, by default) at the digests the server lists
        now, and records each one's exported name."""
        listed = self.listed_manifest(server)
        live = {str(t["name"]): t for t in listed.body["tools"] if t.get("status") != "refused"}
        wanted = sorted(live) if names is None else names
        answer = self.put_manifest(server, [{"name": n, "digest": live[n]["digest"]} for n in wanted])
        if answer.status != 200:
            raise StackError(f"approving {wanted} on {server.name} -> {answer.status}: {str(answer.body)[:400]}")
        server.tools = {n: str(live[n]["exportedName"]) for n in wanted}
        return answer.body

    # --- agents -----------------------------------------------------------------------

    def agent(self, name: str, servers: list[Server], *, engine: str = "none", share: bool = False) -> str:
        body: Json = {"name": name, "description": "An MCP-E2E agent.", "systemPrompt": "You are a careful analyst."}
        if engine != "none":
            body["engineProfile"] = {"engines": [engine]}
        if share:
            body["shareWithOrg"] = True
        answer = self.api.ok("POST", "/api/v1/agents/create", body)
        agent = (answer or {}).get("agent") or answer or {}
        key = str(agent.get("_key") or agent.get("agentKey") or "")
        if not key:
            raise StackError(f"agent create returned no key: {str(answer)[:300]}")
        self.grant(key, servers)
        return key

    def grant(self, agent_key: str, servers: list[Server]) -> None:
        self.api.ok("PUT", f"/api/v1/agents/{agent_key}", {"mcpServers": [
            {"instanceId": s.instance_id, "name": s.name,
             "tools": [{"name": raw, "fullName": exported} for raw, exported in sorted(s.tools.items())]}
            for s in servers
        ]})


def _instance_id(body: object) -> str:
    instance = (body.get("instance") if isinstance(body, dict) else None) or body or {}
    found = str(instance.get("_id") or instance.get("instanceId") or "") if isinstance(instance, dict) else ""
    if not found:
        raise StackError(f"the MCP instance create returned no id: {str(body)[:300]}")
    return found


# `mcp_<server>_<tool>` must fit Codex's and Claude Code's 52 characters; the server's name here has 12.
TOOL_NAME_MAX = 35
JSON_TYPES = {"string": "string", "integer": "integer", "int": "integer", "number": "number", "float": "number",
              "boolean": "boolean", "bool": "boolean", "list": "array", "array": "array", "dict": "object",
              "object": "object"}



def _schema(parameters: list[Json]) -> Json:
    properties = {str(p["name"]): {"type": JSON_TYPES.get(str(p.get("type", "string")).lower(), "string"),
                                   "description": str(p.get("description") or "")} for p in parameters}
    return {"type": "object", "properties": properties,
            "required": [str(p["name"]) for p in parameters if p.get("required")]}


def registry_tools(count: int) -> Json:
    """``count`` MCP tools with the product's own connector tools' descriptions and parameters."""
    from app.agents.registry.toolset_registry import get_toolset_registry

    registry = get_toolset_registry()
    registry.auto_discover_toolsets()
    real = [(toolset, declared) for toolset in sorted(registry.get_all_toolsets())
            for declared in (registry.get_toolset_metadata(toolset) or {}).get("tools") or []]
    tools: Json = {}
    for index in range(count):
        toolset, declared = real[index % len(real)]
        base = re.sub(r"[^A-Za-z0-9_]", "_", f"{toolset}_{declared['name']}")
        suffix = f"_{index // len(real)}" if index >= len(real) else ""
        name = base[: TOOL_NAME_MAX - len(suffix)] + suffix
        if name in tools:
            name = name[: TOOL_NAME_MAX - 7] + "_" + hashlib.sha256(f"{base}{index}".encode()).hexdigest()[:6]
        tools[name] = {"description": str(declared.get("description") or name),
                       "inputSchema": _schema(list(declared.get("parameters") or [])), "readOnly": True}
    return tools


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None


def _redirect(url: str) -> str:
    """Where the provider sends the browser back (its ``Location``), without going there."""
    try:
        urllib.request.build_opener(_NoRedirect).open(url, timeout=30)
    except urllib.error.HTTPError as answer:
        location = answer.headers.get("Location")
        if answer.code in (301, 302, 303, 307) and location:
            return location
        raise StackError(f"the OAuth provider answered {answer.code}: {answer.read()[:300]!r}") from None
    raise StackError("the OAuth provider did not redirect")
