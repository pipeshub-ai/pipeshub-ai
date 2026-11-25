"""R31-SECKEY: no credential of the org's or the platform's enters an engine sandbox, no upstream is reachable from one
but through the gateways, and a sandbox's tokens reach only what its run was granted, while it holds them.

    python seckey.py <run dir> [--engines kind,...] [--out FILE]      e.g. /tmp/pxh-sk/e2e-state/rsk

On a human-style stack (run.sh human-up), with the scripted model and the fake upstreams only. The mainsync venv's
Python (it imports harness_core's canary forms). Per release engine (engines.py kinds; dsh too when its image is on the
host), one job on its own agent that uses the model, four MCP servers (no sign-in, an API token, a header, OAuth), web
search (SearXNG) and a toolset (Jira Data Center). The job's engine model holds twice (the scripted ``hold``): before
its first tool call and after its last, and each time the lane reads the live sandbox from inside it:

    SK-1  every file on every filesystem but proc/sys/dev, and every process's environment and command line, hold no
          canary in raw, base64 (any offset, standard and URL-safe), percent-encoded or hex form; a sentinel written to
          /tmp, /state and /workspace is found there (the scan cannot pass by reading nothing)
    SK-2  no credential shape (harness_core token vectors) in the environments, command lines and the run's own files
    SK-3  every credential-named variable holds a placeholder (``pxh-…``) or a plain word, never a value
    SK-4  each upstream the job used received its real canary from PipesHub's services (fake LLM: the org's key; fake
          SaaS: the API token, the header, the OAuth access token, SearXNG's key, Jira's token), so SK-1 is not vacuous;
          and no canary but the model's own key ever reached the model
    SK-5  the job ended with its answer, and each tool's result reached the person's stream
    SK-6  the platform's secrets the config store keeps (the worker credential, Node's attempt- and gateway-token
          signing keys, the secretKeys document; R31SECKEY-FU-2) are among the canaries SK-1 looks for
    SD-1  no upstream's real name resolves in the sandbox: the providers' API hosts, DuckDuckGo, GitHub, the fake LLM and
          SaaS, the gateways and the app by their Compose names
    SD-2  no TCP to their addresses (the real hosts as this host resolves them, the containers' own addresses and ports)
    SD-3  through the egress proxy with the sandbox's own placeholder, each of those is refused; only the two gateways
          answer
    SG-*  (seckey_gateway.py, in the app container, as the proxy asks) the child's token lists only its granted tools,
          ungranted tools, servers, toolsets and knowledge are refused, another model is refused, a stale attempt, another
          org and a forged token are refused, a mint without the broker's proof for exactly that attempt (none, a
          forged one, another attempt's, another org's; R38IDENT-FU-7) is refused, parent and child tokens cross
          neither way; after the run ended both tokens are refused at both gateways and none is minted again; each
          refusal is in the logs with its run id and no secret

Prints one line per check, writes a JSON report and exits non-zero when one fails. A failed SK-1..SK-3 or SD-* is a
security finding: its line names the canary and the place, never the value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend/python"))

import engines
import seckey_inventory as inv
import seckey_sandbox as sb

Json = dict[str, Any]
sh: Any = None
StackError: type[Exception] = RuntimeError

FAKE_LLM, FAKE_SAAS = "http://pxh-fake-llm:8080/v1", "http://pxh-fake-saas:8090"
MCP_SERVERS = {"e2esaas": "none", "e2ekey": "api_token", "e2ehdr": "headers", "e2eoauth": "oauth"}
LOOSE_SERVER = "e2eloose"  # registered, granted to no agent
HEADER_NAME = "X-Api-Key"
JIRA_TOOLSET, JIRA_TYPE, JIRA_NAME = "Jira Data Center", "jiradatacenter", "SECKEY Jira"
NOTE = "Private note on {topic}: the finance review is due on Friday."
SEARCH_TEXT = "Book travel 14 days ahead."
ISSUE_TEXT = "Quarterly access review (restricted)"
# The real hosts an engine's own client would call, and the stack's fake ones by their Compose names.
REAL_HOSTS = ("api.anthropic.com", "api.openai.com", "pxh-seckey.openai.azure.com", "generativelanguage.googleapis.com",
              "html.duckduckgo.com", "duckduckgo.com", "github.com", "api.github.com", "google.serper.dev",
              "api.tavily.com")
STACK_NAMES = ("egress-proxy", "pxh-fake-llm", "pxh-fake-saas", "model-gateway", "tool-gateway", "pipeshub-ai", "harness-state",
               "sandbox-broker", "mongodb", "redis", "neo4j", "qdrant")
GATEWAY_NAMES = ("egress-proxy", "model-gateway", "tool-gateway")
INSTALL_LABEL = "io.pipeshub.sandbox-broker.install"
# Secrets an engine's controller mints inside one sandbox for the engine's own local server (never the org's or the
# platform's): each must differ from every other sandbox's, which SK-3 checks.
ENGINE_LOCAL = ("OPENCODE_SERVER_PASSWORD",)
local_secrets: dict[str, set[str]] = {}

results: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: str = "", *, engine: str = "", phase: str = "") -> bool:
    results.append({"check": name, "ok": ok, "detail": detail, "engine": engine, "phase": phase})
    where = f"[{engine}{'/' + phase if phase else ''}] " if engine else ""
    print(f"  {'ok  ' if ok else 'FAIL'} {where}{name}{': ' + detail if detail else ''}", flush=True)
    return ok


# --- docker ------------------------------------------------------------------------------------------------------------


def docker(*argv: str, stdin: str | None = None, timeout: float = 600) -> str:
    # Bytes, decoded here: text mode would fold the CRLFs an HTTP answer's chunk sizes count.
    out = subprocess.run(["docker", *argv], input=None if stdin is None else stdin.encode(), capture_output=True,
                         timeout=timeout, check=False)
    return out.stdout.decode("utf-8", "replace")


def in_sandbox(container: str, script: str, *, user: str = "0:0", stdin: str | None = None,
               argv: tuple[str, ...] = (), env: dict[str, str] | None = None, timeout: float = 900) -> str:
    flags = [f"--env={k}={v}" for k, v in (env or {}).items()]
    return docker("exec", "-i", "-u", user, *flags, container, "bash", "-c", script, "seckey", *argv, stdin=stdin,
                  timeout=timeout)


def in_app(stack: Any, script: str, action: str, body: Json | None, timeout: float = 300) -> str:
    container = stack.runtime.container("pipeshub-ai")
    run = [stack.runtime.node, f"/e2e/{script}"] if script.endswith(".js") else ["python", "/e2e/pyrun.py", f"/e2e/{script}"]
    return docker("exec", "-i", container, *run, action, stdin=json.dumps(body or {}), timeout=timeout)


def answered(out: str, key: str) -> Json | None:
    """The helper's own JSON line (``{key: …}``) among the services' log lines it also prints."""
    return next((line for line in sb.json_lines(out) if key in line), None)


def inspect(container: str) -> Json:
    found = json.loads(docker("inspect", container) or "[]")
    return found[0] if found else {}


def sandboxes(project: str) -> list[str]:
    return docker("ps", "-q", "--filter", f"label={INSTALL_LABEL}={project}",
                  "--filter", "label=io.pipeshub.sandbox-broker.role=sandbox").split()


def address(container: str, network: str | None = None) -> list[str]:
    nets = (inspect(container).get("NetworkSettings") or {}).get("Networks") or {}
    return [str(v.get("IPAddress")) for k, v in nets.items() if v.get("IPAddress") and (network is None or k == network)]


# --- canaries ----------------------------------------------------------------------------------------------------------


def image_env(image: str) -> dict[str, str]:
    """What an image itself sets in its environment (a base image's public ``GPG_KEY``, its PATH)."""
    out = json.loads(docker("image", "inspect", image) or "[]")
    items = ((out[0].get("Config") or {}).get("Env") or []) if out else []
    return dict(item.partition("=")[::2] for item in items)


def platform_secrets(stack: Any, known: set[str]) -> dict[str, str]:
    """Every secret-named value of the stack's env file and of the app, proxy and broker containers' environments,
    read here and never printed: a credential of the platform is a canary too. A value an engine image sets itself is
    public (the Python base image's release-signing key id)."""
    found: dict[str, str] = {}
    public = {v for kind in dict.fromkeys(engines.kinds()) for v in image_env(engines.image(kind)).values()}

    def keep(origin: str, key: str, value: str) -> None:
        if value in public or value in known:
            return
        if inv.secret_named(key) and len(value) >= 16 and not sb.NOT_A_SECRET.match(value) \
                and not value.startswith(("pxh-ph-", "pxh-sandbox", "pxh-opencode", "pxh-codex")):
            found.setdefault(f"platform.{origin}.{key}", value)

    for key, value in stack.env.items():
        keep("stack", key, value)
    for service in ("pipeshub-ai", "egress-proxy", "sandbox-broker", "docker-socket-proxy"):
        try:
            container = stack.runtime.container(service)
        except Exception:  # noqa: BLE001, S112 - a stack without the service has none of its secrets
            continue
        for item in (inspect(container).get("Config") or {}).get("Env") or []:
            key, _, value = item.partition("=")
            keep(service, key, value)
    return found


def config_secrets(stack: Any) -> dict[str, str]:
    """The platform's secrets the config store keeps (R31SECKEY-FU-2: the worker credential, Node's signing keys, the
    secretKeys document), read in the app container and handed over on a pipe; never printed, never written. A stack
    whose store cannot be read stops the lane: its scan would be missing them."""
    out = in_app(stack, "seckey_config.py", "secrets", {})
    found = answered(out, "configSecrets")
    secrets = found.get("configSecrets") if found is not None else None
    if not isinstance(secrets, dict) or not any(k.startswith("config.workerCredential.") for k in secrets):
        raise SystemExit("seckey: the config store's secrets could not be read (the helper's output is not shown)")
    return {str(k): str(v) for k, v in secrets.items()}


def load_canaries(run_dir: Path, stack: Any) -> dict[str, str]:
    """The planted canaries (made once per stack and kept, mode 600, so a rerun plants the same), the stack's own
    credentials and the platform's, from its environments and its config store."""
    kept = run_dir / "seckey-canaries.json"
    if kept.exists():
        planted = json.loads(kept.read_text())
    else:
        planted = {c.name: c.value for c in inv.plan()}
        fd = os.open(kept, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(planted, handle)
    live = {name: stack.env[env] for name, env in inv.STACK_LIVE.items() if stack.env.get(env)}
    named = {**planted, **live, "toolset.OAUTH.github": inv.GITHUB_TOKEN}
    platform = platform_secrets(stack, set(named.values()))
    stored = {k: v for k, v in config_secrets(stack).items() if v not in {*named.values(), *platform.values()}}
    return {**named, **platform, **stored}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


# --- seed --------------------------------------------------------------------------------------------------------------


def mcp_instances(stack: Any, canaries: dict[str, str]) -> dict[str, str]:
    api = stack.api
    have = {i.get("name"): str(i.get("_id") or i.get("instanceId"))
            for i in (api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers") or {}).get("instances") or []}
    ids: dict[str, str] = {}
    for name, mode in {**MCP_SERVERS, LOOSE_SERVER: "none"}.items():
        if name in have:
            ids[name] = have[name]
            continue
        body: Json = {"name": name, "transport": "streamable_http", "authMode": mode,
                      "url": f"{FAKE_SAAS}/mcp" + ("" if name == "e2esaas" else f"?server={name}")}
        if mode == "headers":
            body["headerName"] = HEADER_NAME
        if mode == "oauth":
            body.update(authorizationUrl=f"{FAKE_SAAS}/oauth/authorize", tokenUrl=f"{FAKE_SAAS}/oauth/token")
        answer = api.ok("POST", "/api/v1/mcp-servers/instances", body) or {}
        instance = answer.get("instance") or answer
        ids[name] = str(instance.get("_id") or instance.get("instanceId") or "")
    api.ok("POST", f"/api/v1/mcp-servers/instances/{ids['e2ekey']}/authenticate",
           {"apiToken": canaries["mcp.api_token.apiToken"]})
    api.ok("POST", f"/api/v1/mcp-servers/instances/{ids['e2ehdr']}/authenticate",
           {"headerValue": canaries["mcp.headers.headerValue"]})
    api.ok("PUT", f"/api/v1/mcp-servers/instances/{ids['e2eoauth']}/oauth-config",
           {"clientId": f"{inv.TAG}-client", "clientSecret": canaries["mcp.oauth.clientSecret"]})
    return ids


def jira_instance(stack: Any) -> str:
    api = stack.api
    existing = api.ok("GET", "/api/v1/toolsets/instances") or {}
    found = next((i for i in existing.get("instances") or [] if i.get("instanceName") == JIRA_NAME), None)
    if found is None:
        answer = api.ok("POST", "/api/v1/toolsets/instances", {
            "instanceName": JIRA_NAME, "toolsetType": JIRA_TYPE, "authType": "API_TOKEN",
            "authConfig": {"baseUrl": FAKE_SAAS}, "baseUrl": FAKE_SAAS})
        found = (answer or {}).get("instance") or answer or {}
    instance_id = str(found.get("_id") or found.get("instanceId") or found.get("id") or "")
    api.ok("POST", f"/api/v1/toolsets/instances/{instance_id}/authenticate",
           {"auth": {"apiToken": stack.env["E2E_JIRA_TOKEN"]}})
    return instance_id


def agent(stack: Any, kind: str, mcp: dict[str, str], jira: str, state: Json) -> str:
    key = state.get(f"agent:{kind}")
    if key:
        return str(key)
    api = stack.api
    answer = api.ok("POST", "/api/v1/agents/create", {
        "name": f"SECKEY job {kind}", "description": "Works in a coding engine with every kind of credential.",
        "systemPrompt": "You are an analyst working in a sandboxed workspace.", "engineProfile": {"engines": [kind]}})
    created = (answer or {}).get("agent") or answer or {}
    key = str(created.get("_key") or created.get("agentKey") or "")
    api.ok("PUT", f"/api/v1/agents/{key}", {
        "mcpServers": [{"instanceId": mcp[name], "name": name,
                        "tools": [{"name": "lookup_note", "fullName": f"mcp_{name}_lookup_note"}]}
                       for name in MCP_SERVERS],
        "toolsets": [{"name": JIRA_TYPE, "displayName": JIRA_TOOLSET, "type": "app", "instanceId": jira,
                      "instanceName": JIRA_NAME, "tools": [{"name": "get_issue", "fullName": f"{JIRA_TYPE}.get_issue"}]}],
        "webSearch": "searxng"})
    state[f"agent:{kind}"] = key
    return key


def seed(stack: Any, run_dir: Path, canaries: dict[str, str], kinds: list[str]) -> Json:
    state_file = run_dir / "seckey-state.json"
    state: Json = json.loads(state_file.read_text()) if state_file.exists() else {}
    mcp = mcp_instances(stack, canaries)
    jira = jira_instance(stack)
    documents = answered(in_app(stack, "seckey_documents.js", "plant", {
        "aiModels": inv.model_entries(canaries, FAKE_LLM), "webSearch": inv.search_entries(canaries, FAKE_SAAS)}),
        "documents")
    stores = answered(in_app(stack, "seckey_config.py", "plant", {
        "orgId": stack.org_id, "userId": admin_id(stack), "canaries": canaries, "mcpInstanceId": mcp["e2eoauth"]}),
        "plant")
    check("seed: every canary planted where the product keeps it (models, web search, toolsets, MCP sign-ins)",
          documents is not None and stores is not None, json.dumps([documents, stores]))
    engines.prepare_org(stack.api.ok, kinds)
    for kind in kinds:
        agent(stack, kind, mcp, jira, state)
    state.update(mcp=mcp, jira=jira)
    state_file.write_text(json.dumps(state))
    return state


def admin_id(stack: Any) -> str:
    """The signed-in admin's user id, from the claims of the session's own token."""
    import base64

    claims = stack.api.token().split(".")[1]
    body = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    return str(body.get("userId") or body.get("_id") or body.get("sub") or "")


def restore(stack: Any, state: Json) -> None:
    documents = answered(in_app(stack, "seckey_documents.js", "restore", None), "documents")
    stores = answered(in_app(stack, "seckey_config.py", "restore", {
        "orgId": stack.org_id, "userId": admin_id(stack), "mcpInstanceId": (state.get("mcp") or {}).get("e2eoauth")}),
        "restore")
    check("restore: the planted models, search providers, toolsets and sign-in taken out",
          documents is not None and stores is not None, json.dumps([documents, stores]))
    # The lane's own agents, MCP servers and Jira toolset go too: a person's servers are counted by the journeys
    # (an owner shape's job holds exactly its two), so ones left here turn their mcp.first_use lines red.
    api = stack.api
    gone = [api.request("DELETE", f"/api/v1/agents/{key}").status
            for name, key in state.items() if name.startswith("agent:")]
    gone += [api.request("DELETE", f"/api/v1/mcp-servers/instances/{instance}").status
             for name, instance in (state.get("mcp") or {}).items() if name != "e2esaas" and instance]
    if state.get("jira"):
        gone.append(api.request("DELETE", f"/api/v1/toolsets/instances/{state['jira']}").status)
    check("restore: the lane's agents, MCP servers and toolset removed", all(s in (200, 204, 404) for s in gone),
          f"statuses {gone}")
    (stack.run_dir / "seckey-state.json").unlink(missing_ok=True)


# --- one engine's job --------------------------------------------------------------------------------------------------


@dataclass
class Held:
    container: str
    run_id: str
    attempt_id: str
    session_id: str
    network: str


def wait_holding(stack: Any, timeout: float) -> bool:
    try:
        sh.wait_until(lambda: (stack.model.ok("GET", "/__control/holding") or {}).get("holding") is True,
                      timeout=timeout, what="the engine's model call to hold", every=1)
        return True
    except StackError:
        return False


def held_sandbox(stack: Any, before: set[str]) -> Held | None:
    fresh = [c for c in sandboxes(stack.runtime.project) if c not in before]
    if not fresh:
        return None
    found = inspect(fresh[-1])
    labels = (found.get("Config") or {}).get("Labels") or {}
    (network,) = list((found.get("NetworkSettings") or {}).get("Networks") or {})[:1] or [""]
    return Held(fresh[-1], labels.get("runId", ""), labels.get("attemptId", ""), labels.get("sessionId", ""), network)


def script(marker: str, phase: str) -> str:
    """One job: every MCP server's read, the toolset's and web search, with one hold. ``start`` holds before the first
    call (the sandbox as the engine finds it); ``end`` after every credential-bearing call but web search, which asks
    first (the release default for an external read) and goes last. One hold per job: the scripted model's next turn
    after a hold's release does not hold again on an engine, so the two views are two jobs."""
    topic = f"seckey-{marker}"
    calls = [f'tool mcp_{name}_lookup_note {json.dumps({"topic": topic})}' for name in MCP_SERVERS]
    calls.append(f'tool get_issue {json.dumps({"issue_key": "SEC-7"})}')
    search = f'tool web_search {json.dumps({"query": f"travel policy {marker}"})}'
    lines = ["hold", *calls, search] if phase == "start" else [*calls, "hold", search]
    return "\n".join([*lines, f"say done-{marker}"])


def extract(container: str, script: str, user: str, into: Path) -> int:
    """The sandbox's tar stream (``script`` as ``user``) unpacked under ``into``; the bytes read."""
    into.mkdir(parents=True)
    reader = subprocess.Popen(["docker", "exec", "-u", user, container, "sh", "-c", script], stdout=subprocess.PIPE)
    unpack = subprocess.run(["tar", "-x", "-C", str(into), "--no-same-owner", "--no-same-permissions",
                             "--exclude=dev/*", "--warning=none"], stdin=reader.stdout, capture_output=True,
                            check=False, timeout=900)
    reader.wait(timeout=60)
    del unpack
    subprocess.run(["chmod", "-R", "u+rwX", str(into)], check=False, capture_output=True)
    return sum(f.stat().st_size for f in into.rglob("*") if f.is_file() and not f.is_symlink())


def own_text(work: Path) -> str:
    """The run's own files (OWN_PLACES), as text, each at most 512 KB: what the shape scan reads."""
    parts: list[str] = []
    for who in ("root", "user"):
        for place in sb.OWN_PLACES:
            for f in sorted((work / who / place).rglob("*")) if (work / who / place).is_dir() else []:
                if f.is_file() and not f.is_symlink() and f.stat().st_size <= 512 * 1024:
                    parts.append(f.read_text(errors="replace"))
    return "\n".join(parts)


def scan_sandbox(engine: str, phase: str, held: Held, canaries: dict[str, str]) -> None:
    import shutil
    import tempfile

    from harness_core.security.token_patterns import find_forbidden

    sentinel = inv.canary()
    planted = in_sandbox(held.container, sb.sentinel_plant(sentinel), user="1000:1000")
    pattern_text, table = sb.patterns(canaries, sentinel)
    work = Path(tempfile.mkdtemp(prefix="seckey-scan-"))
    try:
        size = extract(held.container, sb.ROOT_TAR, "0:0", work / "root")
        size += extract(held.container, sb.USER_TAR, "1000:1000", work / "user")
        # The readers' own environments hold the sentinel: a scan that skips the processes' environments fails.
        procs = "".join(in_sandbox(held.container, sb.PROCS, user=user, env={"SECKEY_SENTINEL": sentinel})
                        for user in ("1000:1000", "0:0"))
        (work / "proc").mkdir()
        for name, text in sb.proc_sections(procs).items():
            (work / "proc" / name).write_text(text)
        grep = subprocess.run(["grep", "-rHaoF", "-f", "-", str(work)], input=pattern_text, capture_output=True,
                              text=True, errors="replace", check=False, timeout=900)
        scan = sb.read_scan(grep.stdout + f"\nSCAN_DONE {grep.returncode}\n", table, str(work))
        own = own_text(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        in_sandbox(held.container, sb.sentinel_clean(), user="1000:1000")
    want = {p for p in sb.SENTINEL_PLACES if f"PLANTED {p}" in planted} | {"environ"}
    leaks = scan.leaks()
    check("SK-1 no canary in any file, environment or command line (raw, base64, url, hex)",
          scan.complete and "== PROCS_DONE" in procs and size > 10_000_000 and not leaks,
          f"{len(canaries)} canaries, {len(table)} patterns, {size // 1_000_000} MB of files and "
          f"{procs.count('== ENV ')} environments read; found "
          + json.dumps(sorted({(f.name, f.form, f.path) for f in leaks})[:20]), engine=engine, phase=phase)
    check("SK-1 the scan read the sentinel in /tmp, /state, /workspace and the processes' environments",
          want <= scan.sentinel_seen and len(want) == len(sb.SENTINEL_PLACES) + 1,
          f"planted {sorted(want)}, seen {sorted(scan.sentinel_seen)}", engine=engine, phase=phase)
    shapes = sorted(set(find_forbidden(procs + own)))
    check("SK-2 no credential shape in environments, command lines or the run's own files",
          not shapes, f"{len(procs) + len(own)} bytes read; shapes {shapes}", engine=engine, phase=phase)
    envs = sb.environments(procs)
    image = (inspect(held.container).get("Config") or {}).get("Image") or ""
    placeholders, other = sb.credential_variables(envs, image_env(image), ignore=("SECKEY_SENTINEL", *ENGINE_LOCAL))
    check("SK-3 credential-named variables hold only placeholders",
          bool(envs) and {"HARNESS_TOOLS_TOKEN", "HARNESS_GATEWAY_TOKEN"} & set(placeholders) != set() and not other,
          f"{len(envs)} processes; placeholders {placeholders}; other values in {other}", engine=engine, phase=phase)
    mine = {digest(v) for env in envs.values() for k, v in env.items() if k in ENGINE_LOCAL and v}
    reused = sorted(d for d in mine if any(d in seen for seen in local_secrets.values()))
    local_secrets[held.container] = mine
    check("SK-3 an engine's own local-server secret is this sandbox's alone", not reused,
          f"{len(mine)} local secrets; shared with another sandbox {len(reused)}", engine=engine, phase=phase)


def proxy_of(held: Held, stack: Any) -> str:
    proxy = stack.runtime.container("egress-proxy")
    (ip,) = address(proxy, held.network)[:1] or [""]
    return f"{ip}:8097"


def placeholder(dump_envs: dict[str, dict[str, str]], name: str) -> str:
    return next((env[name] for env in dump_envs.values() if env.get(name, "").startswith("pxh-")), "")


def direct_paths(engine: str, held: Held, stack: Any) -> None:
    resolved_real: list[tuple[str, int]] = []
    for host in REAL_HOSTS:
        try:
            for info in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)[:1]:
                resolved_real.append((str(info[4][0]), 443))
        except OSError:
            continue
    stack_addresses: list[tuple[str, int]] = []
    for service, ports in (("pxh-fake-llm", (8080,)), ("pxh-fake-saas", (8090,)),
                           ("pipeshub-ai", (8096, 8098, 3000, 3002, 8000, 8088, 8091)),
                           ("egress-proxy", (8098,)), ("sandbox-broker", (8095,))):
        try:
            for ip in address(stack.runtime.container(service)):
                stack_addresses += [(ip, p) for p in ports]
        except Exception:  # noqa: BLE001, S112 - a service the stack does not run has no address to try
            continue
    names = [*REAL_HOSTS, *STACK_NAMES]
    out = in_sandbox(held.container, sb.direct_script(names, [*resolved_real, *stack_addresses]), user="1000:1000",
                     timeout=300)
    resolved, ports, done = sb.read_direct(out)
    proxy_ip = proxy_of(held, stack).split(":")[0]
    # The gateways' names are the egress proxy's own aliases on the sandbox's network (its /etc/hosts): they resolve,
    # to the proxy and nothing else.
    elsewhere = {n: a for n, a in resolved.items() if a and not (n in GATEWAY_NAMES and set(a) == {proxy_ip})}
    check("SD-1 no upstream's name resolves in the sandbox (providers, search, git, fake LLM and SaaS, the app); the "
          "gateways' names resolve to the egress proxy only",
          done and set(resolved) == set(names) and not elsewhere,
          f"{len(resolved)} names asked; resolved elsewhere {elsewhere}; gateways "
          f"{ {n: resolved.get(n) for n in GATEWAY_NAMES} } (proxy {proxy_ip})", engine=engine)
    opened = [f"{h}:{p}" for (h, p), is_open in ports.items() if is_open]
    wanted = set(resolved_real) | set(stack_addresses)
    check("SD-2 no TCP to any upstream's address (the real hosts, the containers' own addresses and ports)",
          done and set(ports) == wanted and not opened,
          f"{len(ports)} addresses tried; open {opened}", engine=engine)
    envs = sb.environments(in_sandbox(held.container, sb.PROCS, user="1000:1000", timeout=120))
    tools_token = placeholder(envs, "HARNESS_TOOLS_TOKEN")
    # Each engine reads its model placeholder under its own client's name (HARNESS_GATEWAY_TOKEN, ANTHROPIC_AUTH_TOKEN,
    # OPENAI_API_KEY...): the credential-named placeholder that is not the tools one.
    model_token = next((v for env in envs.values() for k, v in sorted(env.items())
                        if k != "HARNESS_TOOLS_TOKEN" and sb.CREDENTIAL_NAME.search(k) and sb.PLACEHOLDER.match(v)), "")
    proxy = proxy_of(held, stack)
    asks: dict[str, str] = {}
    for host in REAL_HOSTS[:5]:
        asks[f"absolute:{host}"] = sb.absolute(f"http://{host}/v1/models", host)
        asks[f"connect:{host}"] = sb.connect(host, 443)
    for name, port, path in (("pxh-fake-llm", 8080, "/v1/models"), ("pxh-fake-saas", 8090, "/mcp"),
                             ("pipeshub-ai", 3000, "/api/v1/users/me"), ("harness-state", 3002, "/")):
        asks[f"absolute:{name}"] = sb.absolute(f"http://{name}:{port}{path}", f"{name}:{port}")
    for ip, port in stack_addresses[:6]:
        asks[f"absolute:{ip}:{port}"] = sb.absolute(f"http://{ip}:{port}/", f"{ip}:{port}")
    # As the engine calls its tools: the proxy's own port under the Tool Gateway's name, the placeholder as the bearer.
    asks["gateway:tools"] = ("POST /mcp HTTP/1.1\\r\\nHost: tool-gateway:8097\\r\\nAuthorization: Bearer $TOK"
                             "\\r\\nContent-Type: application/json\\r\\nAccept: application/json, text/event-stream"
                             "\\r\\nContent-Length: 46\\r\\nConnection: close\\r\\n\\r\\n"
                             '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    out = in_sandbox(held.container, sb.via_proxy_script(asks), user="1000:1000", argv=(proxy, tools_token, model_token),
                     timeout=600)
    asks_model = "GET /v1/models HTTP/1.1\\r\\nHost: model-gateway:8097\\r\\nAuthorization: Bearer $MTOK\\r\\nConnection: close\\r\\n\\r\\n"
    out += in_sandbox(held.container, sb.via_proxy_script({"gateway:model": asks_model}), user="1000:1000",
                      argv=(proxy, tools_token, model_token), timeout=120)
    answers = sb.read_proxy(out)
    let_through = {k: v for k, v in answers.items() if not k.startswith("gateway:") and not sb.refused(v)}
    check("SD-3 through the egress proxy with the sandbox's placeholder, every upstream but the gateways is refused",
          bool(tools_token) and "PROXY_DONE" in out and len(answers) == len(asks) + 1 and not let_through,
          f"{len(answers)} asked; let through {json.dumps(let_through)[:600]}", engine=engine)
    gateway = answers.get("gateway:tools", (None, ""))
    model = answers.get("gateway:model", (None, ""))
    check("SD-3 both gateways answer through the proxy with the sandbox's placeholders (the probe can tell open)",
          gateway[0] == 200 and model[0] == 200,
          f"tools {gateway[0]} {gateway[1][:120]}; model {model[0]} {model[1][:120]}", engine=engine)


def tool_grants(engine: str, held: Held, stack: Any, earlier: list[str]) -> None:
    """SG through the real path, from inside the sandbox: its tools placeholder lists only the run's granted tools, and
    calls of ungranted ones are refused; a placeholder of an earlier, finished job's sandbox reaches neither gateway."""
    envs = sb.environments(in_sandbox(held.container, sb.PROCS, user="1000:1000", timeout=120))
    token = placeholder(envs, "HARNESS_TOOLS_TOKEN")
    proxy = proxy_of(held, stack)

    def rpc(method: str, params: Json, bearer: str = "") -> tuple[int | None, Json]:
        out = in_sandbox(held.container, sb.RPC, user="1000:1000", argv=(proxy, bearer or token,
                                                                          sb.rpc_body(method, params)), timeout=60)
        return sb.read_rpc(out)

    status, answer = rpc("tools/list", {})
    listed = sorted(str(t.get("name")) for t in (answer.get("result") or {}).get("tools") or [])
    # The run's own grants (its pinned spec) bound what may be listed; the lane's ungranted ones must not be there.
    offered = [t for t in listed if any(t == u or t.endswith(f"__{u}") for u in UNGRANTED)]
    granted = [t for t in listed if t.endswith(("__get_issue", "__web_search"))]
    check("SG the sandbox's tools token lists the run's tools and none of the ungranted ones",
          status == 200 and len(granted) == 2 and not offered,
          f"status {status}; {len(listed)} listed {listed[:40]}; ungranted listed {offered}", engine=engine)
    for name in UNGRANTED:
        status, answer = rpc("tools/call", {"name": name, "arguments": {"topic": "seckey"}})
        error = answer.get("error") or {}
        check("SG an ungranted tool is refused at the Tool Gateway", not sb.rpc_ran(status, answer),
              f"{name}: status {status} {str(error.get('message') or (answer.get('result') or {}).get('content'))[:160]}",
              engine=engine)
    for old in earlier:
        status, answer = rpc("tools/list", {}, bearer=old)
        check("SG a finished job's placeholder reaches no gateway from another sandbox",
              status not in (200, None) and not (answer.get("result") or {}).get("tools"),
              f"status {status}", engine=engine)


UNGRANTED = ("mcp_e2esaas_create_ticket", f"mcp_{LOOSE_SERVER}_lookup_note", "jira_data_center__create_issue",
             "create_issue", "search_internal_knowledge", "code_task")


def upstream_hits(stack: Any, canaries: dict[str, str], engine: str) -> None:
    by_digest = {digest(v): n for n, v in canaries.items()}
    model_hits = (stack.model.ok("GET", "/__control/canaries") or {}).get("hits") or []
    saas_hits = (stack.saas.ok("GET", "/__control/canaries") or {}).get("hits") or []
    model_names = sorted({by_digest.get(h.get("canaryDigest"), "?") for h in model_hits})
    saas_names = sorted({by_digest.get(h.get("canaryDigest"), "?") for h in saas_hits})
    want_saas = {"mcp.api_token.apiToken", "mcp.headers.headerValue", "mcp.oauth.accessToken",
                 "search.searxng.apiKey", "toolset.API_TOKEN.apiToken"}
    check("SK-4 the scripted model received the org's model key from PipesHub (the job really used it)",
          "llm.openAICompatible.live" in model_names, f"canaries seen at the model: {model_names}", engine=engine)
    # A model provider's key at the model endpoint is that provider's own (the planted embedding is the default while
    # it is planted, so PipesHub's own embedding calls carry its key); any other credential there is a leak to a model.
    stray = [n for n in model_names if not n.startswith(("llm.", "embedding."))]
    check("SK-4 no other credential ever reached the model", not stray, f"{stray}", engine=engine)
    check("SK-4 the fake SaaS received the MCP API token, header, OAuth token, SearXNG's key and Jira's token",
          want_saas <= set(saas_names), f"seen {saas_names}; missing {sorted(want_saas - set(saas_names))}",
          engine=engine)
    sandbox_peers = set()
    for container in sandboxes(stack.runtime.project):
        sandbox_peers.update(address(container))
    from_sandbox = [h for h in [*model_hits, *saas_hits] if h.get("peer") in sandbox_peers]
    check("SK-4 no canary reached an upstream from a sandbox's address", not from_sandbox, f"{len(from_sandbox)} hits",
          engine=engine)


def watch(stack: Any, canaries: dict[str, str]) -> None:
    values = sorted(set(canaries.values()))
    stack.model.ok("POST", "/__control/canaries", {"values": values})
    stack.saas.ok("POST", "/__control/canaries", {"values": values})


def parent_of(stack: Any, session: str, child: str) -> tuple[str, str]:
    """The child's parent run and its attempt, from the session's journal."""
    events = list(stack.journal(session)["events"]) if session else []
    parent = next((str((e.get("data") or {}).get("parentRunId") or "") for e in events
                   if e.get("runId") == child and (e.get("data") or {}).get("parentRunId")), "")
    if not parent:
        parent = next((str(e.get("runId")) for e in events if e.get("runId") and e.get("runId") != child), "")
    attempt = next((str((e.get("data") or {}).get("attemptId") or e.get("attemptId") or "") for e in reversed(events)
                    if e.get("runId") == parent and ((e.get("data") or {}).get("attemptId") or e.get("attemptId"))), "")
    return parent, attempt


def binding_asks(org: str, other_org: str, child: Json, parent: Json,
                 stale_attempt: str) -> dict[str, Json]:
    """The attempts ``attest`` signs. Besides the held child's own, each refusal ``live`` checks for a reason other than
    the proof gets a genuine proof, so it is refused for that reason: the parent (parked, not leased), a stale attempt
    of the child's run (not leased) and the child's run named under another org (not that org's)."""
    return {
        "child": {"orgId": org, "runId": child["runId"], "attemptId": child["attemptId"]},
        "parent": {"orgId": org, "runId": parent["runId"], "attemptId": parent["attemptId"]},
        "stale": {"orgId": org, "runId": child["runId"], "attemptId": stale_attempt},
        "otherOrg": {"orgId": other_org, "runId": child["runId"], "attemptId": child["attemptId"]},
    }


def in_broker(stack: Any, body: Json, timeout: float = 120) -> Json:
    """The proofs the sandbox broker signs (seckey_gateway.py attest): handed on to ``live`` and never printed."""
    container = stack.runtime.container("sandbox-broker")
    out = docker("exec", "-i", container, "python", "/e2e/pyrun.py", "/e2e/seckey_gateway.py", "attest",
                 stdin=json.dumps(body), timeout=timeout)
    return (answered(out, "proofs") or {}).get("proofs") or {}


def gateway_checks(stack: Any, engine: str, held: Held, marker: str, phase: str) -> list[Json]:
    if phase == "live":
        parent, parent_attempt = parent_of(stack, held.session_id, held.run_id)
        child = {"runId": held.run_id, "attemptId": held.attempt_id}
        body = {"orgId": stack.org_id, "otherOrgId": secrets.token_hex(12), "marker": marker, "child": child,
                "parent": {"runId": parent, "attemptId": parent_attempt}, "staleAttemptId": secrets.token_hex(12),
                "allowedModel": "engine-model", "otherModel": "e2e-llm"}
        body["proofs"] = in_broker(stack, {"bindings": binding_asks(
            stack.org_id, body["otherOrgId"], child, body["parent"], body["staleAttemptId"])})
    else:
        body = {"marker": marker}
    lines = sb.json_lines(in_app(stack, "seckey_gateway.py", phase, body))
    for line in lines:
        fields = {k: v for k, v in line.items() if k not in ("check", "ok")}
        check(f"SG {line['check']}", bool(line.get("ok")), json.dumps(fields)[:500], engine=engine, phase=phase)
    if not lines:
        check(f"SG {phase} ran", False, "seckey_gateway.py printed nothing", engine=engine, phase=phase)
    return lines


TOKEN_SHAPE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")


def refusal_logs(stack: Any, engine: str, run_id: str, canaries: dict[str, str]) -> None:
    lines: list[str] = []
    for name in ("worker.log", "model-gateway.log", "node.log"):
        log = stack.run_dir / "logs" / name
        if log.exists():
            lines += [line for line in log.read_text(errors="replace").splitlines() if run_id in line]
    refusals = [line for line in lines if re.search(r"refus|unauthori|denied|not_granted|forbidden", line, re.IGNORECASE)]
    leaked = [line[:80] for line in refusals if TOKEN_SHAPE.search(line) or any(v in line for v in canaries.values())]
    kinds: dict[str, int] = {}
    for line in refusals:
        event = re.search(r"\b([a-z_]+\.[a-z_.]+refus[a-z_]*|[a-z_.]*denied[a-z_]*)\b", line)
        kinds[event.group(1) if event else "other"] = kinds.get(event.group(1) if event else "other", 0) + 1
    check("SG refusals are logged with the run id and hold no token or credential", bool(refusals) and not leaked,
          f"{len(refusals)} refusal lines by event {kinds}; with a secret {len(leaked)}", engine=engine)


def run_job(stack: Any, kind: str, state: Json, canaries: dict[str, str], phase: str,
            earlier: list[str]) -> None:
    marker = secrets.token_hex(3)
    stack.model.request("POST", "/__control/reset", {})
    # The hold lasts while the lane reads the sandbox, not the scripted model's default two minutes.
    stack.model.ok("POST", "/__control/hold-limit", {"seconds": 900})
    before = set(sandboxes(stack.runtime.project))
    out: Json = {}

    def go() -> None:
        try:
            out["turn"] = sh.turn(stack, state[f"agent:{kind}"], script(marker, phase), decide="approved")
        except Exception as error:  # noqa: BLE001 - the turn's failure is a check line
            out["error"] = f"{type(error).__name__}: {str(error)[:300]}"

    thread = threading.Thread(target=go, daemon=True)
    started = time.monotonic()
    thread.start()
    held: Held | None = None
    if not wait_holding(stack, 600):
        check("the job's engine held", False, out.get("error", "no hold"), engine=kind, phase=phase)
    else:
        held = held_sandbox(stack, before)
        if held is None:
            check("the job's sandbox found", False, "no new sandbox of this stack", engine=kind, phase=phase)
        else:
            runtime = (inspect(held.container).get("HostConfig") or {}).get("Runtime") or "runc"
            check("the job's sandbox found", True, f"{held.container[:12]} run {held.run_id} on {runtime}",
                  engine=kind, phase=phase)
            scan_sandbox(kind, phase, held, canaries)
            envs = sb.environments(in_sandbox(held.container, sb.PROCS, user="1000:1000", timeout=120))
            if phase == "start":
                direct_paths(kind, held, stack)
                earlier.append(placeholder(envs, "HARNESS_TOOLS_TOKEN"))
            else:
                tool_grants(kind, held, stack, [e for e in earlier if e])
                gateway_checks(stack, kind, held, marker, "live")
    stack.model.request("POST", "/__control/release", {})
    thread.join(timeout=600)
    frames, session, run = out.get("turn") or ([], "", "")
    seen = json.dumps([f.data for f in frames])
    note = NOTE.format(topic=f"seckey-{marker}")
    check("SK-5 the job ended with its answer and every tool's result",
          bool(frames) and frames[-1].type == "RUN_FINISHED" and f"done-{marker}" in sh.answer(frames)
          and note in seen and SEARCH_TEXT in seen and ISSUE_TEXT in seen,
          f"conversation {session} run {run} ended {frames[-1].type if frames else out.get('error')}; note "
          f"{note in seen}, search {SEARCH_TEXT in seen}, issue {ISSUE_TEXT in seen}; "
          f"{round(time.monotonic() - started)} s", engine=kind, phase=phase)
    if held is not None and phase == "end":
        time.sleep(3)
        gateway_checks(stack, kind, held, marker, "after")
        refusal_logs(stack, kind, held.run_id, canaries)


def run_engine(stack: Any, kind: str, state: Json, canaries: dict[str, str]) -> None:
    watch(stack, canaries)
    earlier: list[str] = []
    for phase in ("start", "end"):
        run_job(stack, kind, state, canaries, phase, earlier)
    upstream_hits(stack, canaries, kind)


def release_kinds() -> list[str]:
    kinds: list[str] = []
    for kind in dict.fromkeys(engines.kinds()):
        image = engines.image(kind)
        if docker("image", "inspect", "--format", "{{.Id}}", image).strip():
            kinds.append(kind)
        else:
            print(f"  skip {kind}: its image {image} is not on this host", flush=True)
    return kinds


def main(argv: list[str] | None = None) -> int:
    global sh, StackError
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--engines", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="leave the planted canaries on the stack")
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
    import smoke_human

    smoke_human.RUN_DIR = run_dir
    sh, StackError = smoke_human, smoke_human.StackError
    stack = sh.Stack.attach(run_dir.name, "ST-1")
    kinds = [k for k in args.engines.split(",") if k] or release_kinds()
    canaries = load_canaries(run_dir, stack)
    print(f"seckey: {len(canaries)} canaries, engines {kinds}", flush=True)
    stored = sorted({name.split(".")[1] for name in canaries if name.startswith("config.")})
    check("SK-6 the config store's own secrets are canaries (every machine role's credential, both signing keyrings, "
          "secretKeys)",
          {"workerCredential", "attemptSigningKeys", "gatewaySigningKeys", "roleCredentialScrape",
           "roleCredentialEgressProxy", "roleCredentialSandboxBroker"} <= set(stored),
          f"{sum(name.startswith('config.') for name in canaries)} values from {stored}")
    state = seed(stack, run_dir, canaries, kinds)
    try:
        for kind in kinds:
            print(f"engine {kind}", flush=True)
            try:
                run_engine(stack, kind, state, canaries)
            except Exception as error:  # noqa: BLE001 - one engine's failure is its line, the others still run
                check("the engine's checks ran", False, f"{type(error).__name__}: {str(error)[:400]}", engine=kind)
    finally:
        stack.model.request("POST", "/__control/release", {})
        if not args.keep:
            restore(stack, state)
    failed = [r for r in results if not r["ok"]]
    report = {"engines": kinds, "canaries": len(canaries), "checks": len(results), "failed": len(failed),
              "results": results}
    (args.out or run_dir / "seckey.json").write_text(json.dumps(report, indent=1))
    print(f"{len(results) - len(failed)} of {len(results)} checks passed"
          + (f"; failed: {sorted({(r['engine'], r['check']) for r in failed})}" if failed else ""), flush=True)
    return 1 if failed else 0


__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
