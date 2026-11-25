"""The smoke check of a stack a person tests by hand (scripts/human-test-stack.sh smoke). No live model call:
the turns use the stack's scripted model (directives in the message, harness_core/testing/engines/script.py).

    python3 smoke_human.py <run dir>      e.g. ~/.cache/pxh-ht1/state/ht1

Prints one line per check and exits non-zero when one fails. Reuses the e2e runner's client (lane.py) and stack
handle; the turn is read as the web app reads it (the agent's chat stream, reattached until a terminal frame).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

# Imported by its unit tests too (tests/unit/harness/test_testinfra_smoke_state.py): only a run reads the argument.
RUN_DIR = Path(sys.argv[1]).resolve() if __name__ == "__main__" else Path()
if __name__ == "__main__":
    os.environ["E2E_STATE_DIR"] = str(RUN_DIR.parent)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integration-tests/harness/e2e"))

from lane import Api, Frame, Stack, StackError, wait_until  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from human_pages import KNOWLEDGE  # noqa: E402

TERMINAL = frozenset({"RUN_FINISHED", "RUN_ERROR", "complete", "error"})
PROCESSES = {"connectors": "http://localhost:8088/health", "query": "http://localhost:8000/health",
             "model-gateway": "http://localhost:8096/healthz", "indexing": "http://localhost:8091/health",
             "embedding": "http://localhost:8002/health"}
Json = dict[str, Any]
OK, FAIL, SKIP = "ok", "FAIL", "skip"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, OK if ok else FAIL, detail))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
    return ok


def skip(name: str, reason: str) -> None:
    """A check this stack's state cannot run without changing a person's own settings: neither passed nor failed."""
    results.append((name, SKIP, reason))
    print(f"  skip {name}: {reason}", flush=True)


def summary() -> tuple[str, bool]:
    """The last line, and whether the smoke passed: a skip is counted apart and does not fail it."""
    failed = [name for name, outcome, _ in results if outcome == FAIL]
    skipped = [name for name, outcome, _ in results if outcome == SKIP]
    passed = len(results) - len(failed) - len(skipped)
    line = f"{passed} of {len(results)} checks passed"
    if skipped:
        line += f", {len(skipped)} skipped: {skipped}"
    if failed:
        line += f"; failed: {failed}"
    return line, not failed


def capabilities_state(stack: Stack) -> str:
    """ADR-CAP-01 D4: `settled` once every capability probe answered; `absent` when the harness is off."""
    try:
        answer = stack.api.request("GET", "/api/v1/health")
    except StackError:
        return "no answer"
    body = answer.body if isinstance(answer.body, dict) else {}
    return str((body.get("agentHarness") or {}).get("capabilities") or "absent")


def wait_capabilities_settled(stack: Stack, timeout: float = 120.0) -> str:
    """A turn admitted before the broker answered Node's probe waits for it (ADR-CAP-01); a check that drives the
    stack waits here instead, so what it sees is the settled deployment, not the race."""
    try:
        wait_until(lambda: capabilities_state(stack) in ("settled", "absent"), timeout=timeout,
                   what="Node's capabilities to settle", every=1.0)
    except StackError:
        pass
    return capabilities_state(stack)


def knowledge_state(stack: Stack) -> str:
    """`searchable` once every seeded page is indexed and the product's own search returns the travel policy's text;
    otherwise what is still missing. A record can read COMPLETED before its chunks answer a search, so the status
    alone is not the signal a knowledge check needs (R31CAPFIX-FU-1)."""
    try:
        for kb in ("kbTeam", "kbAdmin"):
            for record in stack.seed.get(f"{kb}Records") or []:
                found = stack.api.request("GET", f"/api/v1/knowledgeBase/record/{record}").body
                status = str(((found if isinstance(found, dict) else {}).get("record") or {}).get("indexingStatus"))
                if status != "COMPLETED":
                    return f"record {record} indexing {status}"
        # rewriteQuery false: the scripted model is not asked; saveHistory false: no search left in the admin's history.
        answer = stack.api.request("POST", "/api/v1/search", {"query": "travel policy", "limit": 10,
                                                              "rewriteQuery": False, "saveHistory": False})
    except StackError as error:
        return f"no answer: {str(error)[:120]}"
    if answer.status != 200:
        return f"search answered {answer.status}"
    return "searchable" if INSIDE_TEXT in json.dumps(answer.body) else "search does not return the travel policy yet"


def wait_knowledge_searchable(stack: Stack, timeout: float = 300.0) -> str:
    """A knowledge check run before the seeded pages answer a search reads a race, not retrieval: wait for the
    signal, never a fixed sleep. Returns the last state, so a caller can stop with the reason."""
    try:
        wait_until(lambda: knowledge_state(stack) == "searchable", timeout=timeout,
                   what="the seeded knowledge to be searchable", every=2.0)
    except StackError:
        pass
    return knowledge_state(stack)


def one_shot_jobs(depends_on_labels: list[str]) -> frozenset[str]:
    """The services another waits for to complete (Compose's ``com.docker.compose.depends_on`` label,
    ``name:condition:required`` comma-separated): a one-shot job such as etcd-tls, done once it exits 0."""
    jobs = set()
    for label in depends_on_labels:
        for dep in filter(None, label.split(",")):
            name, _, rest = dep.partition(":")
            if rest.startswith("service_completed_successfully"):
                jobs.add(name)
    return frozenset(jobs)


def container_ok(service: str, state: str, status: str, jobs: frozenset[str]) -> bool:
    """A service runs and is not unhealthy; a one-shot job (R46-LANE: ST-4's etcd-tls) exited 0."""
    if service in jobs:
        return state == "exited" and status.startswith("Exited (0)")
    return state == "running" and "unhealthy" not in status


def services(stack: Stack) -> None:
    project = stack.env["E2E_PROJECT"]
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                          "--format", "{{.Label \"com.docker.compose.service\"}}|{{.State}}|{{.Status}}|"
                          "{{.Label \"com.docker.compose.depends_on\"}}"],
                         check=True, capture_output=True, text=True).stdout.splitlines()
    rows = [line.split("|", 3) for line in out]
    jobs = one_shot_jobs([row[3] for row in rows])
    for service, state, status, _depends in sorted(rows):
        check(f"container {service}", container_ok(service, state, status, jobs), status)
    for name, url in PROCESSES.items():
        try:
            body = stack.exec_app("python", "-c", f"import urllib.request; print(urllib.request.urlopen('{url}', timeout=5).read().decode())")
            check(f"process {name}", '"unhealthy"' not in body and '"starting"' not in body, body.strip()[:80])
        except StackError as error:
            check(f"process {name}", False, str(error)[:200])
    worker = stack.runtime.log("worker").read_text(errors="replace")  # type: ignore[union-attr]
    caps = [line for line in worker.splitlines() if "worker.capabilities" in line]
    check("process worker", "worker.started" in worker and bool(caps) and "executor_engine" in caps[-1],
          "started, serves engine runs" if caps and "executor_engine" in caps[-1] else "no engine capability")
    # routefix: the shared pxh-redis is flushed by other jobs; this stack's config must not live there.
    shared = subprocess.run(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}",
                             os.environ.get("E2E_REDIS_CONTAINER", "pxh-redis")],
                            check=False, capture_output=True, text=True).stdout
    check("config in the stack's own Redis, not the shared one",
          stack.env.get("E2E_OWN_REDIS") == "1" and f"{project}_" not in shared,
          f"E2E_OWN_REDIS={stack.env.get('E2E_OWN_REDIS')!r}, shared Redis on the project's networks: {f'{project}_' in shared}")
    node = stack.api.request("GET", "/api/v1/health")
    settled = capabilities_state(stack)
    check("process node", node.status == 200 and settled in ("settled", "absent"),
          f"/api/v1/health {node.status}, capabilities {settled}")
    broker_images(stack)
    socket_holder(stack)
    document_conversion(stack)


# A one-slide flat ODF presentation: LibreOffice's Impress import and PDF export, with no binary fixture.
PROBE_FODP = (
    '<?xml version="1.0" encoding="UTF-8"?><office:document '
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" office:version="1.2" '
    'office:mimetype="application/vnd.oasis.opendocument.presentation"><office:body><office:presentation>'
    '<draw:page draw:name="p1"/></office:presentation></office:body></office:document>'
)


def document_conversion(stack: Stack) -> None:
    """PPTXPREV: the app container converts a presentation to PDF, as PPT/PPTX previews do. The stack once ran on an
    image without LibreOffice, and every presentation preview failed with a 500."""
    script = (
        'd=$(mktemp -d) && printf %s "$1" >"$d/probe.fodp" && '
        'soffice -env:UserInstallation="file://$d/profile" --headless --convert-to pdf --outdir "$d" "$d/probe.fodp" '
        '>/dev/null 2>&1; head -c 5 "$d/probe.pdf" 2>/dev/null; rm -rf "$d"'
    )
    try:
        head = stack.exec_app("sh", "-c", script, "probe", PROBE_FODP)
        check("document conversion (presentation -> PDF)", head == "%PDF-", head or "soffice wrote no PDF")
    except StackError as error:
        check("document conversion (presentation -> PDF)", False, str(error)[:200])


def socket_holder(stack: Stack) -> None:
    """R38IDENT-FU-1: the Docker API policy proxy is the stack's only container with the Docker socket; the broker has
    neither the socket nor its group."""
    project = stack.env["E2E_PROJECT"]
    ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                         check=True, capture_output=True, text=True).stdout.split()
    inspected = json.loads(subprocess.run(["docker", "inspect", *ids], check=True, capture_output=True, text=True).stdout)
    service = {c["Id"]: (c["Config"].get("Labels") or {}).get("com.docker.compose.service", "") for c in inspected}
    holders = sorted(service[c["Id"]] for c in inspected
                     if any(m.get("Source") == "/var/run/docker.sock" for m in c.get("Mounts") or []))
    broker = next((c for c in inspected if service[c["Id"]] == "sandbox-broker"), {})
    groups = (broker.get("HostConfig") or {}).get("GroupAdd") or []
    check("only the Docker API policy proxy holds the Docker socket",
          holders == ["docker-socket-proxy"] and not groups, f"socket holders {holders}, broker's added groups {groups}")


def broker_images(stack: Stack) -> None:
    """HTIMG: the broker admits exactly the run images of this tree's allowlist, and has found each of them. A broker
    that outlived a rebuild keeps the allowlist it started with and refuses the new images (image_not_allowed)."""
    allowlist = json.loads((Path(__file__).resolve().parents[2] / "backend/python/harness_engines/allowlist.json").read_text())
    # An ACP engine shares a native engine's run image (acp-opencode): each image once, as the broker logs them.
    expected = sorted({engine["image"] for engine in allowlist["engines"]})
    container = stack.runtime.container("sandbox-broker")  # type: ignore[union-attr]
    logs = subprocess.run(["docker", "logs", container], check=False, capture_output=True, text=True)
    lines = (logs.stdout + logs.stderr).splitlines()
    started = max((i for i, line in enumerate(lines) if "broker.started " in line), default=-1)
    admitted = lines[started].split("engineImages=[", 1)[1].split("]", 1)[0].split(",") if (
        started >= 0 and "engineImages=[" in lines[started]) else []
    check("sandbox broker admits this commit's engine run images", sorted(admitted) == expected,
          f"admits {admitted or 'unknown (started before HTIMG: recreate it)'}, allowlist {expected}")
    # The broker runs on this daemon (its socket): an image missing here is an engine its health lists unavailable.
    missing = [image for image in expected
               if subprocess.run(["docker", "image", "inspect", image], check=False, capture_output=True).returncode]
    # R32-TRUST: so is an ACP engine whose profile no trusted key signed, image or not (a production-floor stack once
    # passed this check with five engines "not installed"). A broker from before it logs no list: nothing to read.
    untrusted = (lines[started].split("enginesUntrusted=[", 1)[1].split("]", 1)[0] if (
        started >= 0 and "enginesUntrusted=[" in lines[started]) else "").replace("'", "").replace(" ", "")
    detail = "; ".join(part for part in (f"missing {missing}" if missing else "",
                                         f"profile not signed for this deployment: {untrusted}" if untrusted else "") if part)
    check("every engine run image is on the broker's daemon and every profile trusted (none ENGINES_UNAVAILABLE)",
          not missing and not untrusted, detail)


def accounts(stack: Stack) -> Api:
    check("sign-in admin", bool(stack.api.token()), stack.api.email)
    member = json.loads((RUN_DIR / "member.json").read_text())
    api = Api(stack.api.base, member["email"], member["password"])
    check("sign-in member", bool(api.token()), member["email"])
    return api


AGENTS = ("agentLoop", "agentOpencode", "agentCodex", "agentClaudeCode")


def seeded(stack: Stack, member: Api) -> None:
    seed = stack.seed
    for key in AGENTS:
        agent = (stack.api.ok("GET", f"/api/v1/agents/{seed[key]}") or {}).get("agent") or {}
        engines = (agent.get("engineProfile") or {}).get("engines")
        check(f"agent {agent.get('name')}", bool(agent.get("knowledge")) and bool(agent.get("mcpServers")),
              f"engines={engines} knowledge={len(agent.get('knowledge') or [])} mcp={len(agent.get('mcpServers') or [])}")
        seen = member.request("GET", f"/api/v1/agents/{seed[key]}")
        check(f"member sees {agent.get('name')}", seen.status == 200, str(seen.status))
    for kb in ("kbTeam", "kbAdmin"):
        statuses = [str(((stack.api.ok("GET", f"/api/v1/knowledgeBase/record/{r}") or {}).get("record") or {})
                        .get("indexingStatus")) for r in seed[f"{kb}Records"]]
        check(f"knowledge base {kb} indexed", all(s == "COMPLETED" for s in statuses), ",".join(statuses))
    team = member.request("GET", f"/api/v1/knowledgeBase/record/{seed['kbTeamRecords'][0]}")
    admin_only = member.request("GET", f"/api/v1/knowledgeBase/record/{seed['kbAdminRecords'][0]}")
    check("member reads the shared knowledge base, not the admin-only one",
          team.status == 200 and admin_only.status != 200, f"shared {team.status}, admin-only {admin_only.status}")


def default_model(stack: Stack) -> None:
    """The scripted model is the default only while it is the only one: a person's own model stays default (routefix)."""
    llms = ((stack.api.ok("GET", "/api/v1/configurationManager/ai-models") or {}).get("models") or {}).get("llm") or []
    theirs = [m for m in llms if m.get("modelKey") != stack.seed["llmKey"]]
    default = [m.get("modelKey") for m in llms if m.get("isDefault")]
    check("a configured model of the org's own is still the default", not theirs or stack.seed["llmKey"] not in default,
          f"{len(llms)} LLMs, {len(theirs)} not seeded, the seeded one default: {stack.seed['llmKey'] in default}")


def contents(stack: Stack) -> None:
    """Every seeded page reads back as it was uploaded: Mongo, the graph and the vector store outlive a rebuild, so a
    blob store that does not leaves records that search finds and nobody can open."""
    for kb, (_, _, pages) in KNOWLEDGE.items():
        for record, (filename, text) in zip(stack.seed[f"{kb}Records"], pages.items(), strict=True):
            got = stack.api.request("GET", f"/api/v1/knowledgeBase/stream/record/{record}")
            body = got.body if isinstance(got.body, str) else json.dumps(got.body)
            check(f"content of {filename} reads back", got.status == 200 and body.strip() == text.strip(),
                  f"{got.status} {body[:80]!r}" if got.status != 200 or body.strip() != text.strip() else "")


def mcp_listed(stack: Stack) -> None:
    mine = stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers?includeTools=true") or {}
    text = json.dumps(mine)
    check("MCP server e2esaas lists its tools", "create_ticket" in text and "lookup_note" in text,
          f"instances={[i.get('name') for i in mine.get('instances') or []]} mcpHub={mine.get('mcpHub')}")


def _card(frame: Frame) -> bool:
    """An approval card: the stream waits for the decision, so the reader stops and answers it."""
    return frame.type == "CUSTOM" and frame.name == "approval_requested"


def turn(stack: Stack, agent_key: str | None, text: str, *, decide: str | None = None,
         filters: Json | None = None, api: Api | None = None,
         capabilities: Json | None = None, mode: str = "quick", harness: str | None = None,
         attachments: list[Json] | None = None) -> tuple[list[Frame], str, str]:
    """One new conversation with the agent on the scripted model, read to its end like the web app does. No agent:
    the default chat in Agent mode, as the web app sends it (``capabilities``: its search toggles). ``harness``:
    ``legacy`` asks for today's loop (CTL-7's chat switch). ``attachments``: the upload route's refs, as the web app
    sends a chat's files."""
    seed = stack.seed
    api = api or stack.api
    body = {"query": text, "chatMode": mode, "recordIds": [], "filters": filters or {},
            "modelKey": seed["llmKey"], "modelName": "e2e-llm"}
    if capabilities is not None:
        body["agentCapabilities"] = capabilities
    if attachments:
        body["attachments"] = attachments
    if harness is not None:
        body["harness"] = harness
    base = f"/api/v1/agents/{agent_key}/conversations"
    if agent_key is None:
        body.update(chatMode="agent", harness="durable")
        base = "/api/v1/conversations"
    stream = api.stream("POST", f"{base}/stream", body, headers={"x-request-id": f"smoke-{secrets.token_hex(4)}"},
                              stop=_card)
    if stream.error and not stream.frames:
        raise StackError(f"stream {stream.status}: {stream.error}")
    frames = list(stream.frames)
    session = run = ""
    for _ in range(10):
        for frame in frames:
            if frame.type == "CUSTOM" and frame.name == "conversation_created":
                value = frame.data.get("value") or {}
                session, run = session or value.get("conversationId", ""), run or value.get("runId", "")
            if frame.type == "RUN_STARTED" and not run:
                run = frame.data.get("runId", "")
        last = frames[-1] if frames else None
        if last is None or last.type in TERMINAL or not run:
            break
        if last.type == "CUSTOM" and last.name == "approval_requested" and decide:
            value = last.data.get("value") or {}
            api.ok("POST", f"{base}/{session}/runs/{value.get('childRunId') or run}/interactions/"
                           f"{value.get('interactionId')}/decision",
                         {"decision": decide, "idempotencyKey": f"smk-{secrets.token_hex(6)}"})
        after = next((f.id for f in reversed(frames) if f.id), None)
        more = api.stream("GET", f"{base}/{session}/runs/{run}/stream",
                                headers={"Last-Event-ID": after} if after else None, stop=_card)
        frames.extend(more.frames)
    return frames, session, run


def answer(frames: list[Frame]) -> str:
    return "".join(str(f.data.get("delta") or "") for f in frames if f.type == "TEXT_MESSAGE_CONTENT")


def discover(name: str) -> str:
    """A model finds an MCP tool with search_tools first: the chat loop above 20 tools, and every run's MCP tools on the
    loop and an engine alike, which wait behind the search until the run first uses their server (ADR-OWD-07
    D-LAZY-4)."""
    return f'tool search_tools {{"query": "{name}", "make_visible": true}}'


def saas(stack: Stack, what: str) -> list[Json]:
    return list((stack.saas.ok("GET", "/__control/effects") or {}).get(what) or [])


CODING_ENGINE = "/api/v1/agent-harness/model-accounts/coding-engine"
ORG_CODING_ENGINE = "/api/v1/agent-harness/admin/coding-engine"
ENGINE_ACCOUNTS_ME = "/api/v1/agent-harness/engine-accounts/me"
ORG_DEFAULT, NO_ENGINE = "org_default", "none"
OWN_CLAUDE = "admin has their own Claude account switched on; this check needs the org's model"


class SettingError(StackError):
    """The admin's personal setting a group of checks needs could not be read or set."""


def own_claude_connected(api: Api) -> bool:
    """Whether the admin's Claude Code runs use their own Claude account, which the scripted model cannot stand in
    for. `enabled` decides it, not the state: an admin who switched it on but never signed in reads `available`, and
    each of their Claude Code tasks then waits for a sign-in. A Node without the route or the sign-in service has
    no such account."""
    got = api.request("GET", ENGINE_ACCOUNTS_ME)
    claude = (got.body.get("claude") or {}) if got.status == 200 and isinstance(got.body, dict) else {}
    return claude.get("enabled") is True


@contextmanager
def coding_engine_choice(api: Api, choice: str) -> Iterator[str]:
    """The admin's personal coding-engine choice set to ``choice`` for the checks inside and put back after them,
    however they end; yields the previous choice. A tester's leftover choice (codex) hands every default-chat
    Agent-mode turn to a code_task child, so the checks reading that turn's own tool calls see none."""
    view = api.request("GET", CODING_ENGINE)
    if view.status != 200 or not isinstance(view.body, dict):
        raise SettingError(f"GET {CODING_ENGINE}: {view.status} {json.dumps(view.body)[:200]}")
    previous = str(view.body.get("choice") or ORG_DEFAULT)
    if previous != choice:
        saved = api.request("PUT", CODING_ENGINE, {"choice": choice})
        if saved.status != 200:
            raise SettingError(f"PUT {CODING_ENGINE} {choice}: {saved.status} {json.dumps(saved.body)[:200]}")
    try:
        yield previous
    finally:
        if previous != choice:
            back = api.request("PUT", CODING_ENGINE, {"choice": previous})
            if back.status != 200:
                check("admin's personal coding-engine choice put back", False,
                      f"{previous}: {back.status} {json.dumps(back.body)[:200]}")


def default_chat(stack: Stack, member: Api) -> None:
    """The default-chat Agent-mode checks run the turn on the chat loop itself: the admin's choice is no engine."""
    try:
        with coding_engine_choice(stack.api, NO_ENGINE):
            assistant_mcp(stack)
            selection_scope(stack, member)
    except SettingError as error:
        check("admin's personal coding-engine choice set to none for the default-chat Agent-mode checks", False,
              str(error))


def chat(stack: Stack) -> None:
    marker = f"smoke-{secrets.token_hex(3)}"
    frames, session, run = turn(stack, stack.seed["agentLoop"], f"say {marker}")
    last = frames[-1].type if frames else "none"
    check("durable chat turn (chat loop, scripted model)", bool(run) and last in TERMINAL and marker in answer(frames),
          f"conversation {session} run {run} ended {last}")
    before = len(saas(stack, "reads"))
    frames, session, run = turn(stack, stack.seed["agentLoop"],
                                f'{discover("lookup_note")}\ntool lookup_note {{"topic": "onboarding"}}\nsay looked')
    check("MCP tool lookup_note called from a chat-loop turn", len(saas(stack, "reads")) > before,
          f"run {run} ended {frames[-1].type if frames else 'none'}")


MCP_NAME = "e2esaas"


def mcp_admission(events: list[Json], run: str, instances: set[str]) -> tuple[bool, str, list[str]]:
    """Whether the run was admitted on the harness with the user's e2esaas tools and no notice names e2esaas
    (MCPNOTE-1), its detail, and the other servers a notice names: another server the person added and has not
    signed in to (a person's own MCP server) is theirs to fix, not a failure of this check. A notice that names no
    server (an older Node) may be about e2esaas, so it fails."""
    mine = [e for e in events if e.get("runId") == run]
    durable = bool(run) and any(e.get("type") == "run.admitted" for e in mine)
    spec = next((((e.get("data") or {}).get("spec") or {}) for e in mine if e.get("type") == "run.spec_pinned"), {})
    granted = {g.get("toolId") for g in ((spec.get("grants") or {}).get("tools") or [])}
    tools = sorted(t for t in granted if isinstance(t, str) and any(t.startswith(f"mcp@{i}/") for i in instances))
    # D-LAZY-2: a worker that lists at first use is granted the default chat's server whole, naming no tool; the
    # check that follows (the turn calls lookup_note) is what shows its tools are the run's.
    whole = [m.get("instanceId") for m in ((spec.get("extensions") or {}).get("x.pipeshub") or {}).get("mcp") or []
             if m.get("instanceId") in instances and m.get("tools") == "at_first_use"]
    tools = tools or [f"mcp@{i}/* (at first use)" for i in whole]
    notices = [e.get("data") or {} for e in mine if e.get("type") == "x.pipeshub.run.notice"]
    about = [n for n in notices if n.get("item") == MCP_NAME or (n.get("code") == "mcp_unavailable" and not n.get("item"))]
    others = sorted({str(n["item"]) for n in notices if n.get("code") == "mcp_unavailable" and n.get("item")} - {MCP_NAME})
    ok = durable and bool(tools) and not about
    return ok, f"run {run or 'none'} durable={durable} {MCP_NAME} tools={tools} notices about it={about}", others


def assistant_mcp(stack: Stack) -> None:
    """MCPLIVE: a new default-chat Agent-mode conversation of a user with an MCP server runs on the harness with that
    server's tools (discovered at admission while the MCP hub is off), never on today's loop."""
    before = len(saas(stack, "reads"))
    frames, session, run = turn(stack, None,
                                f'{discover("lookup_note")}\ntool lookup_note {{"topic": "onboarding"}}\nsay looked')
    events = list(stack.journal(session)["events"]) if session else []
    mine = stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers") or {}
    instances = {str(i.get("_id") or i.get("instanceId")) for i in mine.get("instances") or [] if i.get("name") == MCP_NAME}
    ok, detail, others = mcp_admission(events, run, instances)
    check("default-chat Agent-mode turn admitted on the harness with the user's MCP server", ok,
          f"conversation {session} {detail}")
    if others:
        print(f"  info the turn went without other MCP servers of the user (not this check's): {', '.join(others)}",
              flush=True)
    check("MCP tool lookup_note called from a default-chat Agent-mode turn", len(saas(stack, "reads")) > before,
          f"run {run} ended {frames[-1].type if frames else 'none'}")


def selected(record: str) -> Json:
    """One record picked in the chat's knowledge selector, as the web app sends it (main #4255, sanitizeFilters)."""
    return {"apps": [], "kb": [], "records": [record]}


def admitted(stack: Stack, session: str, run: str) -> bool:
    events = list(stack.journal(session)["events"]) if session else []
    return bool(run) and any(e.get("type") == "run.admitted" and e.get("runId") == run for e in events)


# Phrases of human_pages' Team handbook: the selected page, and the two pages of the same base left out of it.
INSIDE_TEXT, OUTSIDE_TEXTS = "14 days ahead", ("within 15 minutes", "90 days of history")
SEARCH_SELECTED = ('tool search_internal_knowledge {"query": "travel policy"}'
                   ' || knowledgegraph__search {"query": "travel policy"}')


def fetch(record: str) -> str:
    return f'tool knowledgegraph__fetch_record {json.dumps({"record_ids": [record]})}'


def selection_scope(stack: Stack, member: Api) -> None:
    """SYNC4: a default-chat Agent-mode turn that selects one record runs durable and its knowledge tools read only
    that record: another record of the same base, which the admin can read, is refused by id. A selection the user
    cannot read, on a turn that can only search (web search off), is refused with main's typed reason."""
    inside, outside = stack.seed["kbTeamRecords"][0], stack.seed["kbTeamRecords"][1]
    frames, session, run = turn(stack, None, f"{SEARCH_SELECTED}\n{fetch(outside)}\n{fetch(inside)}\nsay scoped",
                                filters=selected(inside))
    last = frames[-1].type if frames else "none"
    check("default-chat Agent-mode turn with one selected record admitted on the harness",
          admitted(stack, session, run) and last == "RUN_FINISHED",
          f"conversation {session} run {run or 'none'} ended {last}")
    calls = [f.data for f in frames if f.type == "TOOL_CALL_RESULT"]
    results = [str(c.get("content") or "") for c in calls]
    search, refused, kept = (results + ["", "", ""])[:3]
    leaked = [text for text in OUTSIDE_TEXTS if any(text in result for result in results)]
    check("the selected turn's search and fetches return nothing outside the selection",
          len(results) == 3 and not leaked, f"{len(results)} tool results, outside text seen: {leaked}")
    # The person's stream carries the typed code's words, never the detail; the model reads the id and the reason.
    refusal = calls[1] if len(calls) > 1 else {}
    check("a fetch by id of a readable record outside the selection is refused",
          refusal.get("status") == "failed" and refusal.get("errorCode") == "not_found" and not refused,
          f"status={refusal.get('status')} errorCode={refusal.get('errorCode')} content={refused[:120]!r}")
    check("a fetch by id of the selected record returns it", INSIDE_TEXT in kept, f"search {search[:80]!r} fetch {kept[:120]!r}")
    # Main refuses only a turn that can only search: with web search on, the turn runs and its search says so.
    frames, session, run = turn(stack, None, "say never", filters=selected(stack.seed["kbAdminRecords"][0]), api=member,
                                capabilities={"internalSearch": True, "webSearch": False})
    end = frames[-1] if frames else None
    code, message = (str(end.data.get("code")), str(end.data.get("message"))) if end else ("", "")
    check("a selection the user cannot read is refused with main's reason (selection_empty)",
          admitted(stack, session, run) and end is not None and end.type == "RUN_ERROR" and code == "selection_empty"
          and message.startswith("Nothing in your selection is available to search") and "never" not in answer(frames),
          f"run {run or 'none'} ended {end.type if end else 'none'} code={code} message={message[:80]!r}")


def approval(stack: Stack) -> None:
    before = len(saas(stack, "effects"))
    frames, session, run = turn(stack, stack.seed["agentLoop"],
                                f'{discover("create_ticket")}\n'
                                'tool create_ticket {"title": "Smoke approval", "body": "should ask"}\nsay done',
                                decide="denied")
    asked = any(f.type == "CUSTOM" and f.name == "approval_requested" for f in frames)
    check("create_ticket asks; a deny ends the run and sends nothing",
          asked and frames[-1].type in TERMINAL and len(saas(stack, "effects")) == before,
          f"run {run} ended {frames[-1].type if frames else 'none'}")


def shown_answer(frames: list[Frame]) -> str:
    """The answer as the web app leaves it on screen: a message the stream retracted (#4442: its end says
    ``discarded``, or a ``message_discarded`` names it) is taken away, and the final answer snapshot is shown."""
    gone = {f.data.get("messageId") for f in frames if f.type == "TEXT_MESSAGE_END" and f.data.get("discarded")}
    gone |= {(f.data.get("value") or {}).get("messageId") for f in frames
             if f.type == "CUSTOM" and f.name == "message_discarded"}
    kept = "".join(str(f.data.get("delta") or "") for f in frames
                   if f.type == "TEXT_MESSAGE_CONTENT" and f.data.get("messageId") not in gone)
    finals = [str((f.data.get("snapshot") or {}).get("answer") or "") for f in frames
              if f.type == "STATE_SNAPSHOT" and (f.data.get("snapshot") or {}).get("final")]
    return kept + "".join(finals)


def shown_outcome(frames: list[Frame]) -> dict[str, Any]:
    """The run's settled outcome the chat words (``runOutcome`` on the final snapshot or part, WP-89); {} when none."""
    def find(value: Any) -> dict[str, Any] | None:  # noqa: ANN401 - frame data is untyped JSON
        if isinstance(value, dict):
            if isinstance(value.get("runOutcome"), dict):
                return value["runOutcome"]
            for inner in value.values():
                if (got := find(inner)) is not None:
                    return got
        elif isinstance(value, list):
            for inner in value:
                if (got := find(inner)) is not None:
                    return got
        return None
    for frame in reversed(frames):
        if (got := find(frame.data)) is not None:
            return got
    return {}


def surfaced_error(frames: list[Frame]) -> str:
    """How the turn told the person it failed: a run error, or today's loop's failed conversation and error answer;
    "" when it reads as a success."""
    last = frames[-1] if frames else None
    if last is None:
        return ""
    if last.type in ("RUN_ERROR", "error"):
        return f"{last.type} {last.data.get('code') or ''}".strip()
    conversation = ((last.data.get("result") or {}).get("conversation") or {}) if last.type == "RUN_FINISHED" else {}
    final = next(((f.data.get("snapshot") or {}) for f in reversed(frames)
                  if f.type == "STATE_SNAPSHOT" and (f.data.get("snapshot") or {}).get("final")), {})
    if conversation.get("status") == "Failed" or final.get("answerMatchType") == "Error":
        return f"conversation {conversation.get('status')} errorCode={final.get('errorCode')}"
    return ""


def saved_answers(stack: Stack, path: str) -> str:
    """Every saved message of the conversation but the person's own (which holds the script), once it settles."""
    def body() -> Json:
        return ((stack.api.ok("GET", path) or {}).get("conversation") or {})

    try:
        wait_until(lambda: body().get("status") not in (None, "Inprogress", "In Progress", "inprogress"),
                   timeout=60, what="the conversation to settle", every=2)
    except StackError:
        pass
    return json.dumps([m for m in body().get("messages") or [] if m.get("messageType") != "user_query"])


def faults(stack: Stack) -> list[Json]:
    """The model calls the scripted model failed mid-stream since it started."""
    requests = (stack.model.ok("GET", "/__control/requests?all=1") or {}).get("requests") or []
    return [r for r in requests if r.get("fault") == "mid_stream"]


def partial_retraction(stack: Stack) -> None:
    """#4442: a provider that fails mid-answer leaves no partial text, on today's loop and on a durable run. The
    scripted model streams the text of a ``fail`` line, then fails the call (and each retry of it)."""
    agent = stack.seed["agentLoop"]
    for label, harness in (("today's loop", "legacy"), ("a durable run", None)):
        marker = f"partial-{secrets.token_hex(3)}"
        before = len(faults(stack))
        try:
            frames, session, run = turn(stack, agent, f"fail {marker} is the start of an answer", harness=harness)
        except StackError as error:
            check(f"a provider error mid-answer leaves no partial text ({label})", False, str(error)[:300])
            continue
        failed_calls = faults(stack)[before:]
        durable = admitted(stack, session, run)
        streamed = any(marker in str(f.data.get("delta") or "") for f in frames if f.type == "TEXT_MESSAGE_CONTENT")
        shown = marker in shown_answer(frames)
        saved = marker in saved_answers(stack, f"/api/v1/agents/{agent}/conversations/{session}") if session else True
        error = surfaced_error(frames)
        check(f"a provider error mid-answer leaves no partial text ({label})",
              bool(failed_calls) and durable == (harness is None) and not shown and not saved and bool(error),
              f"conversation {session or 'none'} run {run or 'none'} durable={durable}, {len(failed_calls)} failed "
              f"model calls, partial streamed={streamed} shown={shown} saved={saved}, error: {error or 'none'}")


def handed_off(frames: list[Frame]) -> str:
    """The engine child a coder agent's loop handed the turn to (CTL-6): its nested RUN_STARTED; "" when none."""
    return next((str(f.data.get("runId") or "") for f in frames
                 if f.type == "RUN_STARTED" and f.data.get("parentRunId")), "")


def engines(stack: Stack) -> None:
    """CTL-6: a coder agent's turn runs on the loop, which hands the script to its engine with code_task; the
    shell command and the MCP call run in that engine child, under the agent's own grants. A Claude Code child of an
    admin with their own Claude account switched on runs on that account: its checks are skipped, nobody signed out."""
    own_claude = own_claude_connected(stack.api)
    for key in AGENTS[1:]:
        if key == "agentClaudeCode" and own_claude:
            skip(f"engine run {key} (shell command in its code_task child, scripted model)", OWN_CLAUDE)
            skip(f"MCP tool lookup_note called from {key}'s code_task engine child", OWN_CLAUDE)
            skip(f"MCP tool lookup_note called by name, with no search, from {key}'s code_task engine child", OWN_CLAUDE)
            continue
        marker = f"engine-{secrets.token_hex(3)}"
        try:
            frames, session, run = turn(stack, stack.seed[key], f"shell echo {marker}\nsay {marker}")
        except StackError as error:
            check(f"engine run {key}", False, str(error)[:300])
            continue
        last = frames[-1] if frames else None
        child = handed_off(frames)
        detail = f"run {run} ended {last.type if last else 'none'}, engine child {child or 'none'}"
        if last is not None and last.type in ("RUN_ERROR", "error"):
            detail += f": {json.dumps(last.data)[:300]}"
        finished = bool(run) and last is not None and last.type == "RUN_FINISHED"
        check(f"engine run {key} (shell command in its code_task child, scripted model)", finished and bool(child), detail)
        before = len(saas(stack, "reads"))
        frames, _, run = turn(stack, stack.seed[key],
                              f'{discover("lookup_note")}\ntool lookup_note {{"topic": "onboarding"}}\nsay looked')
        child = handed_off(frames)
        check(f"MCP tool lookup_note called from {key}'s code_task engine child",
              len(saas(stack, "reads")) > before and bool(child),
              f"run {run} ended {frames[-1].type if frames else 'none'}, engine child {child or 'none'}")
        # R27REST-FU-2: a call naming a granted server's tool is its first use, searched for or not.
        before = len(saas(stack, "reads"))
        frames, _, run = turn(stack, stack.seed[key], 'tool lookup_note {"topic": "onboarding"}\nsay looked')
        child = handed_off(frames)
        check(f"MCP tool lookup_note called by name, with no search, from {key}'s code_task engine child",
              len(saas(stack, "reads")) > before and bool(child),
              f"run {run} ended {frames[-1].type if frames else 'none'}, engine child {child or 'none'}")


def child_steps(frames: list[Frame], child: str) -> list[Json]:
    """The engine child's progress lines its parent's stream said, under the child's run id (CTRESULTS)."""
    return [f.data.get("value") or {} for f in frames
            if f.type == "CUSTOM" and f.name == "engine_progress" and f.data.get("runId") == child]


def code_task_steps(stack: Stack) -> None:
    """CTRESULTS: a code task's child showed none of its work, live or after a reload. Its command with its
    output and exit code and its file change reach the parent's stream, and the child's timeline (what its card
    opens onto after a reload) holds the same."""
    key = "agentOpencode"
    label = f"a code task's command result and file change reach the stream and the transcript ({key}, scripted model)"
    marker = f"ctr-{secrets.token_hex(3)}"
    try:
        frames, session, run = turn(stack, stack.seed[key], f"write ctr.py print('{marker}')\nshell python3 ctr.py\nsay {marker}")
    except StackError as error:
        check(label, False, str(error)[:300])
        return
    child = handed_off(frames)
    lines = child_steps(frames, child) if child else []
    def shown(found: list[Json]) -> tuple[bool, bool, bool]:
        output = "".join(str(v.get("text") or "") for v in found if v.get("kind") == "output")
        exited = any(v.get("kind") == "command_done" and (v.get("data") or {}).get("exitCode") == 0 for v in found)
        changed = any(v.get("kind") in ("file", "diff") and str(v.get("text") or "").endswith("ctr.py") for v in found)
        return marker in output, exited, changed
    live = shown(lines)
    saved: tuple[bool, bool, bool] = (False, False, False)
    if child and session:
        timeline = stack.api.request("GET", f"/api/v1/agents/{stack.seed[key]}/conversations/{session}/runs/{child}/timeline")
        engine = (timeline.body or {}).get("engine") or {} if timeline.status == 200 else {}
        saved = shown(list(engine.get("frames") or []))
    check(label, bool(child) and all(live) and all(saved),
          f"run {run} child {child or 'none'}: stream output/exit/file {live}, timeline output/exit/file {saved}")


def worker_lines(run: str) -> list[str]:
    """The log lines about one run, from every process that runs one: the worker, and the query service that runs a
    turn it claimed inline."""
    lines: list[str] = []
    for name in ("worker.log", "query.log"):
        log = RUN_DIR / "logs" / name
        lines += log.read_text(errors="replace").splitlines() if log.exists() else []
    return [line for line in lines if f"runId={run} " in line]


def child_engine(child: str) -> str:
    """The engine the worker assembled for a child run (its `engine.assembly` line); "" when none."""
    found = next((line for line in worker_lines(child) if "engine.assembly" in line), "")
    return found.split(" engine=", 1)[1].split()[0] if " engine=" in found else ""


# Past 8 KB of answer: a child's result over about 3 KB was refused by Node's commit route until CHILDREF.
LONG_ANSWER_WORDS = 700
# Deep mode's intent step sends the request as one line, "user query : <request>": a script whose first line is its
# directive loses it there, the scripted model does not rewrite it and the coordinator spawns nothing.
LONG_ANSWER_LEAD = "Reply with the long text below."


def long_answers(stack: Stack) -> None:
    """CHILDREF: a child's long answer reaches its parent, from an engine child (a coder agent's code_task) and
    from a loop sub-agent (deep mode's spawn_agent). A Codex child once had every completion refused and ran its turn
    four times before quarantine; the parent's answer read the same, so the child's own ending is what is checked."""
    for name, key, mode in (("an engine child", "agentOpencode", "quick"), ("a loop sub-agent", "agentLoop", "deep")):
        marker = f"long-{secrets.token_hex(3)}"
        text = " ".join(f"{marker}-{i:04d}" for i in range(LONG_ANSWER_WORDS))
        label = f"{name}'s answer of {len(text) // 1024} KB reaches its parent ({key}, {mode}, scripted model)"
        try:
            frames, _, run = turn(stack, stack.seed[key], f"{LONG_ANSWER_LEAD}\nsay {text}", mode=mode)
        except StackError as error:
            check(label, False, str(error)[:300])
            continue
        last = frames[-1] if frames else None
        child = handed_off(frames)
        lines = worker_lines(child) if child else []
        completed = any("engine.kernel.complete" in line or "run.complete " in line for line in lines)
        refused = sum("journal.rejected" in line for line in lines)
        whole = f"{marker}-{LONG_ANSWER_WORDS - 1:04d}" in answer(frames)
        check(label, bool(run) and last is not None and last.type == "RUN_FINISHED" and completed and not refused
              and whole,
              f"run {run} ended {last.type if last else 'none'}, child {child or 'none'} "
              f"{'completed' if completed else 'did not complete'}, {refused} commits refused, "
              f"answer {'whole' if whole else 'cut'}")


# The scripted model reports this many prompt tokens for every call of the turn ("usage <n>"), the coder loop's
# hand-off call as well as its engine child's. e2e-llm is priced as the table's unknown model (USD 3 per 1M prompt
# tokens) and the child's tree shares the release's USD 5, so USD decides long before the 10M-token backstop: the
# hand-off leaves about USD 2, and the child's first call crosses the cap, so the next one is refused for budget. At 6M
# the hand-off itself cost USD 18 and Node refused the child.
CUT_SHORT_USAGE = 1_000_000


def cut_short_partial(stack: Stack) -> None:
    """SPENDCONT: an engine child its budget stops keeps its work: its partial answer is the parent's answer,
    labelled partial, and the file the person asked for is saved, partial, with the handoff "continue" reads.

    The child's work before the cut is three knowledge searches, a gateway tool its list shows. It looked the notes up
    with the MCP server before R26-LAZY; an MCP tool now waits behind the search (ADR-OWD-07 D-LAZY-4), and the usage
    this check reports overflows the engine's context, whose compaction drops the search's answer before the scripted
    model can call what it found. The engine checks above call the MCP tool from a child."""
    marker = f"spendcont-{secrets.token_hex(3)}"
    name = f"{marker}.md"
    label = "a budget-cut code_task child's partial reaches the person and its file is saved (agentOpencode, scripted)"
    searches = "\n".join(f'tool search_internal_knowledge {{"query": "{topic}"}} || knowledgegraph__search '
                         f'{{"query": "{topic}"}}' for topic in ("onboarding", "security", "billing"))
    text = f"Make a file {name} with the notes.\nusage {CUT_SHORT_USAGE}\n{searches}\nsay {marker} complete"
    try:
        frames, _, run = turn(stack, stack.seed["agentOpencode"], text)
    except StackError as error:
        check(label, False, str(error)[:300])
        return
    ok, detail = cut_short_shown(frames, worker_lines(handed_off(frames)) if handed_off(frames) else [], name)
    check(label, ok, f"run {run}: {detail}")


def cut_short_shown(frames: list[Frame], child_lines: list[str], name: str) -> tuple[bool, str]:
    """Whether the turn ended with the cut-short child's answer, labelled partial and naming the saved file."""
    last = frames[-1] if frames else None
    finished = last is not None and last.type == "RUN_FINISHED"
    cut = any("engine.kernel.cut_short" in line and "reason=budget" in line for line in child_lines)
    saved = any("engine.handoff.saved" in line and "deliverable=True" in line for line in child_lines)
    shown = shown_answer(frames)
    # WP-89 (ADR-RUN-01 D1): with outcome words on, the answer carries the prose and the chat words the typed outcome
    # (partial, its file), so the label is read from that outcome; an older stack still writes the English line.
    outcome = shown_outcome(frames)
    typed = outcome.get("kind") == "partial" and name in json.dumps(outcome)
    labelled = typed or ("This result is partial." in shown and name in shown)
    detail = (f"ended {last.type if last else 'none'}, child cut short {cut}, file saved {saved}, "
              f"answer labelled partial {labelled}")
    return finished and cut and saved and labelled, detail


def default_chat_engine(stack: Stack) -> None:
    """ENGPICK: with the admin's coding engine for the org set to Codex, a default-chat Agent-mode turn hands its code
    task to a Codex child, as a coder agent's loop does (CTL-6). R31-HOT: a person's own shape (the journeys' owner
    shape) on that job, which the saved agents' engine checks missed: the default chat holds the user's MCP server
    whole (at first use), and its job must keep that server and call its tool, and read a skill the default chat has.
    The org's and the admin's settings are put back."""
    previous = (stack.api.ok("GET", CODING_ENGINE) or {}).get("orgDefault")
    saved = stack.api.request("PUT", ORG_CODING_ENGINE, {"defaultEngine": "codex"})
    if not check("admin sets the org's default coding engine to Codex",
                 saved.status == 200 and (saved.body or {}).get("orgDefault") == "codex",
                 f"{saved.status} {json.dumps(saved.body)[:200]}"):
        return
    try:
        with coding_engine_choice(stack.api, ORG_DEFAULT):
            codex_job_shell(stack)
            codex_job_mcp(stack)
            codex_job_skill(stack)
    except SettingError as error:
        check("admin's personal coding-engine choice set to the org's default for the Codex job checks", False,
              str(error))
    finally:
        stack.api.request("PUT", ORG_CODING_ENGINE, {"defaultEngine": previous})


def codex_job(stack: Stack, label: str, text: str) -> tuple[list[Frame], str, str, str] | None:
    """One default-chat Agent-mode turn handed to a Codex child: its frames, conversation, run and child; None (and
    the check failed) when the turn did not end or no Codex child ran it."""
    try:
        frames, session, run = turn(stack, None, text)
    except StackError as error:
        check(label, False, str(error)[:300])
        return None
    last = frames[-1] if frames else None
    child = handed_off(frames)
    engine = child_engine(child) if child else ""
    detail = (f"conversation {session} run {run} ended {last.type if last else 'none'}, engine child {child or 'none'} "
              f"on {engine or 'no engine'}")
    if last is not None and last.type in ("RUN_ERROR", "error"):
        detail += f": {json.dumps(last.data)[:300]}"
    if not (bool(run) and last is not None and last.type == "RUN_FINISHED" and engine == "codex"):
        check(label, False, detail)
        return None
    return frames, session, run, child


def codex_job_shell(stack: Stack) -> None:
    label = "default-chat Agent-mode turn hands its code task to a Codex child (org's coding engine, scripted model)"
    if codex_job(stack, label, f"shell echo engpick-{secrets.token_hex(3)}\nsay done") is not None:
        check(label, True)


def tool_environment(child: str) -> str:
    """The `run.tool_environment` line the worker wrote for a child run; "" when none."""
    return next((line for line in worker_lines(child) if "run.tool_environment" in line), "")


def codex_job_mcp(stack: Stack) -> None:
    """R31-HOT: every default-chat Codex job ran with `mcpServers=0`, its parent's server held whole dropped at
    the child's admission, so the job could not call it. The job keeps it and calls its tool by name (its first use)."""
    label = "a default-chat Codex job keeps the MCP server held at first use and calls its tool lookup_note"
    before = len(saas(stack, "reads"))
    # By its full name: a person with two servers that both offer lookup_note (the journeys' owner shape) holds both
    # whole, and a bare name names neither.
    ran = codex_job(stack, label, f'tool mcp_{MCP_NAME}_lookup_note {{"topic": "onboarding"}}\nsay looked')
    if ran is None:
        return
    frames, session, run, child = ran
    events = list(stack.journal(session)["events"]) if session else []
    mine = stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers") or {}
    instances = {str(i.get("_id") or i.get("instanceId")) for i in mine.get("instances") or [] if i.get("name") == MCP_NAME}
    _, admitted_detail, _ = mcp_admission(events, run, instances)
    environment = tool_environment(child)
    servers = environment.split(" mcpServers=", 1)[1].split()[0] if " mcpServers=" in environment else "?"
    called = len(saas(stack, "reads")) > before
    check(label, "(at first use)" in admitted_detail and servers not in ("0", "?") and called,
          f"parent {admitted_detail}; child {child} mcpServers={servers}; lookup_note reached the SaaS: {called}")


SKILL = "pdf"


def codex_job_skill(stack: Stack) -> None:
    """R31-HOT: a default-chat Codex job is offered the default chat's skills (mounted, and given to Codex as its own
    skill root whatever the workspace's trust, CF-02) and reads the pdf skill's instructions."""
    label = f"a default-chat Codex job reads the {SKILL} skill mounted for it"
    ran = codex_job(stack, label, f"shell head -n 4 /skills/{SKILL}/SKILL.md\nsay read")
    if ran is None:
        return
    frames, _, _, child = ran
    output = "".join(str(v.get("text") or "") for v in child_steps(frames, child) if v.get("kind") == "output")
    assembly = next((line for line in worker_lines(child) if "engine.assembly" in line), "")
    skills = assembly.split(" skills=", 1)[1].split()[0] if " skills=" in assembly else "?"
    check(label, f"name: {SKILL}" in output and skills not in ("0", "?"),
          f"child {child} skills={skills}, SKILL.md read: {output.strip()[:120]!r}")


def main() -> None:
    stack = Stack.attach(RUN_DIR.name, "ST-1")
    wait_capabilities_settled(stack)
    # Not a check of its own: the knowledge checks below report a page the search does not return.
    wait_knowledge_searchable(stack)
    print("services")
    services(stack)
    print("accounts and seed")
    member = accounts(stack)
    seeded(stack, member)
    contents(stack)
    default_model(stack)
    mcp_listed(stack)
    print("turns (scripted model)")
    chat(stack)
    default_chat(stack, member)
    approval(stack)
    partial_retraction(stack)
    engines(stack)
    code_task_steps(stack)
    default_chat_engine(stack)
    long_answers(stack)
    cut_short_partial(stack)
    line, passed = summary()
    print(line)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
