"""MCP-E2E CP, K, CR: MCP servers of every kind, configured as a customer configures them, called by every executor.

    python -m pytest test_mcp_kinds.py                      # bring ST-1 up with the hub on, run, tear down
    python -m pytest test_mcp_kinds.py --attach=ST-1=RUN    # against a stack left up (run.sh matrix-up, E2E_MCP_HUB=1)

The stack is the options-matrix lane's (QA-6) with ``MCP_HUB``, ``MCP_STDIO_RUNNERS`` and ``MCP_ALLOW_CUSTOM_STDIO``
on. An admin adds one server per transport and auth kind through Node's API, connects it (a token, a header, the
OAuth flow), approves its manifest and grants its tools to one agent per executor; a turn on each executor then
calls every server. The model is scripted and the servers are the lane's (``fake_saas/kinds.py``, real MCP servers
with a fake OAuth provider); everything between is the product.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from mcp_rows import EXECUTORS, Chat, discover, engine_sandbox_dump, executors_here, second_user, tool
from mcp_stack import FAKE_MCP, STDIO_SECRET_ENV, McpAdmin, Server, canary, digest
from plan import ProcessEnv

if TYPE_CHECKING:
    from conftest import Stacks
    from lane import Stack
    from mcp_rows import Member, Turn

Json = dict[str, Any]
STACK = "ST-1"
pytestmark = pytest.mark.mcp_hub
HTTP_KINDS = ("none", "token", "header", "oauth", "sse")
KINDS = (*HTTP_KINDS, "stdio")
# The fake provider's tokens and registered secrets, by shape: their values never leave the provider and the product.
ISSUED = re.compile(r"\b(?:oat|ort|dcs)-[A-Za-z0-9_-]{16,}")
# What a runner holds in place of a server's secret (harness_core.sandbox.egress.sealed.PLACEHOLDER_PREFIX, OWD-1).
STAND_IN_PREFIX = "pxh-ph-"
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")


@dataclass
class World:
    stack: Stack
    admin: McpAdmin
    bob: Member
    servers: dict[str, Server] = field(default_factory=dict[str, Server])
    agents: dict[str, str] = field(default_factory=dict[str, str])
    secrets: dict[str, str] = field(default_factory=dict[str, str])
    first_manifest: dict[str, Json] = field(default_factory=dict[str, Json])

    def canaries(self) -> list[str]:
        return sorted(self.secrets.values())


def _oauth_server(admin: McpAdmin, kind: str, *, discoverable: bool, client: tuple[str, str] | None) -> Server:
    endpoints = {} if discoverable else {"authorizationUrl": f"{FAKE_MCP}/oauth/{kind}{admin.suffix}/authorize",
                                         "tokenUrl": f"{FAKE_MCP}/oauth/{kind}{admin.suffix}/token"}
    server = admin.http_server(kind, auth_mode="oauth", fake_auth="oauth", discoverable=discoverable, **endpoints)
    if client is not None:
        client_id, client_secret = client
        admin.fake.ok("POST", f"/__control/servers/{server.fake_id}/oauth/clients",
                      {"clientId": client_id, "clientSecret": client_secret})
        admin.api.ok("PUT", f"/api/v1/mcp-servers/instances/{server.instance_id}/oauth-config",
                     {"clientId": client_id, "clientSecret": client_secret})
        server.secrets.append(client_secret)
    return server


def _make_servers(world: World) -> None:
    admin, secrets = world.admin, world.secrets
    secrets.update({kind: canary(kind) for kind in ("token", "header", "sse", "stdio", "oauthclient")})
    servers = world.servers
    servers["none"] = admin.http_server("knone")
    servers["token"] = admin.http_server("ktoken", auth_mode="api_token", fake_auth="bearer", credential=secrets["token"])
    servers["header"] = admin.http_server("kheader", auth_mode="headers", fake_auth="header",
                                          credential=secrets["header"], header="x-api-key", headerName="X-Api-Key")
    servers["sse"] = admin.http_server("ksse", transport="sse", auth_mode="api_token", fake_auth="bearer",
                                       credential=secrets["sse"])
    servers["oauth"] = _oauth_server(admin, "koauth", discoverable=False,
                                     client=(f"app-{admin.suffix}", secrets["oauthclient"]))
    servers["stdio"] = admin.stdio_server("kstdio", secrets["stdio"])


def _connect(world: World) -> None:
    admin, servers, secrets = world.admin, world.servers, world.secrets
    assert admin.authenticate(servers["token"], {"apiToken": secrets["token"]}).status == 200
    assert admin.authenticate(servers["header"], {"headerName": "X-Api-Key", "headerValue": secrets["header"]}).status == 200
    assert admin.authenticate(servers["sse"], {"apiToken": secrets["sse"]}).status == 200
    assert admin.authenticate(servers["stdio"], {"apiToken": secrets["stdio"]}).status == 200
    assert admin.oauth_connect(servers["oauth"])["success"] is True


@pytest.fixture(scope="module")
def world(stacks: Stacks) -> World:
    stack = stacks.get(STACK, mcp_hub=True)
    stack.set_process_env(ProcessEnv(engine_runs=True).env())
    made = World(stack, McpAdmin(stack), second_user(stack))
    made.admin.enable()
    _make_servers(made)
    _connect(made)
    for kind, server in made.servers.items():
        made.first_manifest[kind] = made.admin.listed_manifest(server).body
    return made


@pytest.fixture(scope="module")
def approved(world: World) -> World:
    """Every server's manifest approved by the admin, and one agent per executor granted every server's tools."""
    for kind, server in world.servers.items():
        world.admin.approve(server, ["echo", "env_digest"] if kind == "stdio" else None)
    for executor in executors_here():
        world.agents[executor] = world.admin.agent(
            f"MCPE2E kinds {executor} {world.admin.suffix}", list(world.servers.values()),
            engine=EXECUTORS[executor][0], share=True,
        )
    return world


def _executor(name: str) -> str:
    if name not in executors_here():
        pytest.skip(f"the {name} engine image is not on this host")
    return name


def whoami_lines(world: World, executor: str) -> list[str]:
    """One call per server: ``whoami`` on the HTTP kinds, the STDIO server's digest of its secret's variable."""
    servers = world.servers
    names = [servers[k].tools["whoami"] for k in HTTP_KINDS]
    stdio = servers["stdio"].tools["env_digest"]
    lines = [tool(name) for name in names] + [tool(stdio, name=STDIO_SECRET_ENV)]
    return [*discover(*names, stdio), *lines] if executor == "loop" else lines


# --- CP: the customer path -------------------------------------------------------------------------------------


def test_cp1_the_install_reports_the_hub_and_the_runners_on(world: World) -> None:
    listed = world.stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers")
    assert listed["mcpHub"] is True and listed["mcpStdioRunners"] is True


def test_cp2_an_instance_record_holds_no_secret_and_reads_connected(world: World) -> None:
    listed = world.stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers")
    mine = {i["_id"]: i for i in listed["instances"]}
    for kind, server in world.servers.items():
        assert mine[server.instance_id]["isAuthenticated"] is True, kind
    everything = str(listed) + str(world.stack.api.ok("GET", "/api/v1/mcp-servers/instances"))
    assert [c[:14] for c in world.canaries() if c in everything] == []
    assert ISSUED.findall(everything) == []


def test_cp3_nothing_is_approved_until_an_admin_approves_it(world: World) -> None:
    for kind, manifest in world.first_manifest.items():
        assert manifest["approved"] is None, kind
        assert {t["status"] for t in manifest["tools"]} == {"unapproved"}, (kind, manifest["tools"])


def test_cp4_a_stale_digest_is_refused_and_the_live_one_approves(world: World) -> None:
    server = world.admin.http_server("kstale")
    stale = world.admin.put_manifest(server, [{"name": "whoami", "digest": "sha256:" + "f" * 64}])
    assert stale.status == 409 and "mcp_manifest_stale" in str(stale.body), (stale.status, stale.body)
    _ = world.admin.approve(server)
    after = world.admin.manifest(server).body
    assert [(t["name"], t["status"]) for t in after["tools"]] == [("whoami", "approved")]
    assert after["approved"]["approvedBy"]


def test_cp5_a_member_cannot_add_a_server_or_approve_a_manifest(world: World) -> None:
    server = world.servers["none"]
    before = world.admin.manifest(server).body
    created = world.admin.create({"name": f"bobs{world.admin.suffix}", "transport": "streamable_http",
                                  "authMode": "none", "url": f"{FAKE_MCP}/s/{server.fake_id}/mcp"}, api=world.bob.api)
    assert created.status == 403, (created.status, created.body)
    live = {t["name"]: t["digest"] for t in before["tools"]}
    put = world.admin.put_manifest(server, [{"name": n, "digest": d} for n, d in live.items()], api=world.bob.api)
    assert put.status == 403, (put.status, put.body)
    assert world.admin.manifest(server, api=world.bob.api).status == 403
    assert world.admin.manifest(server).body["approved"] == before["approved"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8090/mcp", "http://localhost:8090/mcp",
                                 "http://169.254.169.254/latest/meta-data", "http://metadata.google.internal/mcp"])
def test_cp6_a_server_on_a_loopback_or_metadata_address_is_refused(world: World, url: str) -> None:
    refused = world.admin.create({"name": f"refused{world.admin.suffix}", "transport": "streamable_http",
                                  "authMode": "none", "url": url})
    assert refused.status == 400 and "mcp_url_refused" in str(refused.body), (refused.status, refused.body)


def test_cp6_a_server_on_the_installs_own_private_network_is_accepted(world: World) -> None:
    """MCPFIX F2: every scripted server of this module is one (the stack's Compose network); said once here."""
    assert world.stack.env.get("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "") in ("", "false")
    listed = world.stack.api.ok("GET", "/api/v1/mcp-servers/instances")
    urls = [str(i.get("url")) for i in listed["instances"] if i.get("url")]
    assert urls and all(url.startswith(FAKE_MCP) or "pxh-fake-saas" in url for url in urls), urls


# --- K and CP-7: every kind, every executor ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def kind_turns(approved: World) -> dict[str, Turn]:
    """One turn per executor that calls every server once (kept: each case below reads its own facts from it)."""
    turns: dict[str, Turn] = {}
    approved.stack.model.ok("POST", "/__control/canaries", {"values": approved.canaries()})
    for executor in executors_here():
        chat = Chat(approved.stack, executor, approved.agents[executor])
        turns[executor] = chat.send(*whoami_lines(approved, executor))
    return turns


@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_cp7_a_granted_run_is_offered_every_approved_tool_and_completes(
    approved: World, kind_turns: dict[str, Turn], executor: str
) -> None:
    turn = kind_turns[_executor(executor)]
    assert turn.obs.route == "durable" and turn.obs.terminal == "finished", (turn.obs.error_code, turn.obs.notes)
    assert turn.record.script.lines[-1].removeprefix("say ") in turn.obs.answer
    for kind, server in approved.servers.items():
        for raw, exported in server.tools.items():
            assert turn.offers(exported), (kind, raw, sorted(turn.offered()))


@pytest.mark.parametrize("kind", HTTP_KINDS)
@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_k_each_http_kind_is_called_with_its_credential(
    approved: World, kind_turns: dict[str, Turn], executor: str, kind: str
) -> None:
    """K-1..K-5: the server answered this run (its nonce is in what the run read) and saw the configured credential."""
    turn = kind_turns[_executor(executor)]
    server = approved.servers[kind]
    seen = approved.admin.seen(str(server.fake_id))
    assert f"server={server.fake_id} tool=whoami nonce={seen['nonce']}" in "\n".join(turn.tool_results()), kind
    digests = {call["credentialDigest"] for call in seen["calls"]}
    if kind == "none":
        assert digests == {"none"}
    elif kind == "oauth":
        assert digests and digests <= set(seen["oauth"]["live"]), (digests, seen["oauth"])
    else:
        assert digests == {digest(approved.secrets[kind])}, kind
    assert seen["refused"] == [], (kind, seen["refused"])


@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_k6_a_stdio_server_runs_in_a_runner_holding_a_stand_in_never_its_secret(
    approved: World, kind_turns: dict[str, Turn], executor: str
) -> None:
    """K-6 (OWD-1, WP-06; R36COVER-FU-6): the STDIO server runs in a runner sandbox, not the app. Its secret's
    variable holds a stand-in the egress proxy swaps for the secret only on a request to a listed host, so the
    server's digest of that variable is the stand-in's: a digest that is not the secret's, the secret in no runner,
    and neither the secret nor its digest in anything the model reads."""
    turn = kind_turns[_executor(executor)]
    secret = approved.secrets["stdio"]
    read = turn.text() + "\n".join(turn.tool_results())
    digests = set(DIGEST.findall("\n".join(turn.tool_results())))
    assert digests, "the STDIO server's env_digest answered nothing this run read"
    assert secret not in read and hashlib.sha256(secret.encode()).hexdigest() not in read
    runners = _runner_containers(approved.stack)
    assert runners, "no MCP runner sandbox is running for the STDIO server"
    environments = [_environments(c) for c in runners]
    assert all(secret not in env for env in environments), "the secret reached a runner's environment"
    stand_ins = [line.split("=", 1)[1] for env in environments for line in env.splitlines()
                 if line.startswith(f"{STDIO_SECRET_ENV}=")]
    assert stand_ins and all(v.startswith(STAND_IN_PREFIX) for v in stand_ins), "the runner holds no stand-in"
    assert {hashlib.sha256(v.encode()).hexdigest() for v in stand_ins} & digests, "the digest is not the stand-in's"
    app = approved.stack.runtime.container("pipeshub-ai")  # type: ignore[union-attr]
    assert "/opt/pxh-echo/server.py" not in _commands(app), "the STDIO server runs inside the app container"


def _runner_containers(stack: Stack) -> list[str]:
    found = subprocess.run(["docker", "ps", "-q", "--filter", "label=pipeshub.role=mcp-runner",
                            "--filter", f"label=orgId={stack.org_id}"], check=True, capture_output=True, text=True)
    return found.stdout.split()


def _exec(container: str, script: str) -> str:
    done = subprocess.run(["docker", "exec", container, "/bin/sh", "-c", script], check=False, capture_output=True,
                          timeout=60)
    return done.stdout.decode("utf-8", "replace")


def _environments(container: str) -> str:
    return _exec(container, "cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n'")


def _commands(container: str) -> str:
    return _exec(container, "cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\\0' ' '")


# --- CR: the credential stays out --------------------------------------------------------------------------------


@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_cr1_no_credential_in_the_journal_the_frames_or_the_saved_conversation(
    approved: World, kind_turns: dict[str, Turn], executor: str
) -> None:
    turn = kind_turns[_executor(executor)]
    saved = str(approved.stack.api.ok("GET", f"/api/v1/agents/{approved.agents[executor]}/conversations/{turn.obs.session_id}"))
    text = turn.text() + saved
    assert [c[:14] for c in approved.canaries() if c in text] == []
    assert ISSUED.findall(text) == []


def test_cr2_no_credential_reached_a_model_request(approved: World, kind_turns: dict[str, Turn]) -> None:
    watched = approved.stack.model.ok("GET", "/__control/canaries")
    assert watched["watching"] == len(approved.canaries()) and kind_turns
    assert watched["hits"] == []


def test_cr3_no_credential_in_any_service_log(approved: World, kind_turns: dict[str, Turn]) -> None:
    assert kind_turns
    logs = approved.stack.run_dir / "logs"
    proxy = subprocess.run(["docker", "logs", approved.stack.runtime.container("egress-proxy")],  # type: ignore[union-attr]
                           check=False, capture_output=True, text=True)
    surfaces = {path.name: path.read_text(errors="replace") for path in sorted(logs.glob("*.log"))}
    surfaces["egress-proxy"] = proxy.stdout + proxy.stderr
    leaked = [(name, c[:14]) for name, text in surfaces.items() for c in approved.canaries() if c in text]
    assert leaked == []
    assert [(name, found[:8]) for name, text in surfaces.items() for found in ISSUED.findall(text)] == []
    # Positive control: the scan read the logs that record these turns.
    assert "worker" in "".join(surfaces) and len(surfaces["egress-proxy"]) > 0


@pytest.mark.parametrize("executor", [e for e in EXECUTORS if e != "loop"])
def test_cr4_no_credential_in_the_engines_sandbox_after_it_called_every_server(approved: World, executor: str) -> None:
    """The engine's turn is held after its tool calls; its sandbox is read from inside while it waits."""
    chat = Chat(approved.stack, _executor(executor), approved.agents[executor])
    with chat.held(*whoami_lines(approved, executor), after=True) as pending:
        dump = engine_sandbox_dump(pending.ids()[1])
    turn = pending.finish()
    assert turn.obs.terminal == "finished", turn.failure()
    assert len(turn.tool_results()) == len(KINDS), turn.tool_results()
    assert [c[:14] for c in approved.canaries() if c in dump] == []
    assert ISSUED.findall(dump) == []
    # Positive control: the dump read the engine's environment (its own placeholder is there, legitimately).
    assert "HARNESS_TOOLS_TOKEN=" in dump, dump[:300]


def test_k7_oauth_by_dynamic_client_registration(approved: World) -> None:
    """The server publishes RFC 9728 and RFC 8414 metadata and no client is configured: the product registers one."""
    admin = approved.admin
    server = admin.http_server("kdcr", auth_mode="oauth", fake_auth="oauth", discoverable=True)
    assert admin.oauth_connect(server)["success"] is True
    seen = admin.seen(str(server.fake_id))
    assert len(seen["oauth"]["clients"]) == 1 and [i["grant"] for i in seen["oauth"]["issued"]] == ["authorization_code"]
    _ = admin.approve(server)
    agent = admin.agent(f"MCPE2E dcr {admin.suffix}", [server])
    name = server.tools["whoami"]
    turn = Chat(approved.stack, "loop", agent).send(*discover(name), tool(name))
    assert turn.obs.terminal == "finished", turn.failure()
    called = admin.seen(str(server.fake_id))
    assert {c["credentialDigest"] for c in called["calls"]} <= set(called["oauth"]["live"]) and called["calls"]


def _expiring(approved: World, kind: str) -> tuple[Server, Chat, str]:
    admin = approved.admin
    server = _oauth_server(admin, kind, discoverable=False, client=(f"{kind}-{admin.suffix}", canary("oauthclient2")))
    assert admin.oauth_connect(server)["success"] is True
    _ = admin.approve(server)
    agent = admin.agent(f"MCPE2E {kind} {admin.suffix}", [server])
    return server, Chat(approved.stack, "loop", agent), server.tools["whoami"]


def _assert_refreshed(approved: World, server: Server, turn: Turn) -> None:
    assert turn.obs.terminal == "finished", turn.failure()
    seen = approved.admin.seen(str(server.fake_id))
    assert [i["grant"] for i in seen["oauth"]["issued"]] == ["authorization_code", "refresh_token"], seen["oauth"]
    assert seen["refused"], "the expired token was never presented"
    assert seen["calls"][-1]["credentialDigest"] in seen["oauth"]["live"]
    assert f"nonce={seen['nonce']}" in "\n".join(turn.tool_results())
    assert ISSUED.findall(turn.text()) == []


def test_k8_a_token_that_expired_between_turns_is_refreshed_when_the_next_turn_starts(approved: World) -> None:
    """MCPE2E-F5: the run lists the server's tools with a refused token, refreshes once and goes on."""
    server, chat, name = _expiring(approved, "kexp")
    assert chat.send(*discover(name), tool(name)).obs.terminal == "finished"
    approved.admin.fake.ok("POST", f"/__control/servers/{server.fake_id}/oauth/expire", {})
    _assert_refreshed(approved, server, chat.send(*discover(name), tool(name)))


def test_k8_a_token_that_expires_while_a_run_is_going_is_refreshed_at_the_call(approved: World) -> None:
    server, chat, name = _expiring(approved, "kexq")
    with chat.held(*discover(name), tool(name)) as pending:
        approved.admin.fake.ok("POST", f"/__control/servers/{server.fake_id}/oauth/expire", {})
    _assert_refreshed(approved, server, pending.finish())


def test_cr6_each_server_recorded_the_credential_it_was_configured_with(approved: World, kind_turns: dict[str, Turn]) -> None:
    assert kind_turns
    for kind in ("token", "header", "sse"):
        seen = approved.admin.seen(str(approved.servers[kind].fake_id))
        assert seen["credentialDigest"] == digest(approved.secrets[kind])
        assert len(seen["calls"]) >= len(kind_turns), (kind, seen["calls"])


# --- NM: tool names ----------------------------------------------------------------------------------------------

DOTTED = "admin.tools.list"
# Raw names whose plain export (`mcp_<server>_<tool>`, this lane's server names have 12 characters) would be 58 and
# 81 characters: over an engine's limit before MCPFIX F3, exported at 52 with a digest since.
LONG = "generate_quarterly_revenue_report_summary"
LONGER = "x" * 64
UNICODE = "na\u00efve"
EXPORT_MAX = 52


@dataclass
class Names:
    stack: Stack
    server: Server
    manifest: Json
    agents: dict[tuple[str, str], str]


@pytest.fixture(scope="module")
def names(approved: World) -> Names:
    admin = approved.admin
    described = {raw: {"description": f"The {raw} tool.", "readOnly": True} for raw in (DOTTED, LONG, LONGER, UNICODE)}
    server = admin.http_server("knames", tools=described)
    _ = admin.approve(server)
    agents = {}
    for raw in (DOTTED, LONG, LONGER):
        only = Server(server.name, server.instance_id, server.fake_id, tools={raw: server.tools[raw]})
        for executor in executors_here():
            agents[raw, executor] = admin.agent(f"MCPE2E names {raw[:12]} {executor} {admin.suffix}",
                                                [only], engine=EXECUTORS[executor][0])
    return Names(approved.stack, server, admin.manifest(server).body, agents)


def _named(names: Names, executor: str, raw: str) -> Turn:
    exported = names.server.tools[raw]
    chat = Chat(names.stack, executor, names.agents[raw, executor])
    return chat.send(*([*discover(exported), tool(exported)] if executor == "loop" else [tool(exported)]))


def test_nm1_a_real_servers_names_are_exported_refused_or_kept_as_the_contract_says(names: Names) -> None:
    from harness_core.mcp_hub.names import export_name, export_namespace

    listed = {t["name"]: t for t in names.manifest["tools"]}
    assert listed[UNICODE]["status"] == "refused" and listed[UNICODE]["reasonCode"] == "mcp_tool_name_invalid"
    namespace = export_namespace(names.server.name)
    for raw in (DOTTED, LONG, LONGER):
        # NM-3: the name in the product's manifest is the contract function's (mcp-tool-names.json's rule).
        assert listed[raw]["status"] == "approved" and listed[raw]["exportedName"] == export_name(namespace, raw)
        assert len(listed[raw]["exportedName"]) <= EXPORT_MAX
    assert "." not in listed[DOTTED]["exportedName"]
    assert [len(listed[raw]["exportedName"]) for raw in (LONG, LONGER)] == [EXPORT_MAX, EXPORT_MAX]


@pytest.mark.parametrize("raw", [DOTTED, LONG, LONGER], ids=["dotted", "long", "longer"])
@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_nm2_an_approved_tool_is_offered_and_called_on_every_executor(names: Names, executor: str, raw: str) -> None:
    """NM-1, NM-2 (MCPFIX F3): whatever a server names its tool, the name it is exported under is one every
    executor can carry; before, a name over 52 characters refused the whole engine run."""
    turn = _named(names, _executor(executor), raw)
    assert turn.obs.terminal == "finished", turn.failure()
    assert turn.offers(names.server.tools[raw])
    assert f"tool={raw} " in "\n".join(turn.tool_results())
