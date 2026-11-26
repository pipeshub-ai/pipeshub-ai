"""A user's turns with an agent on an MCP-E2E stack, and what each turn's model was offered.

``Chat`` is one user's conversation with one agent on one executor (the chat loop or an engine): it sends a scripted
turn through the runner's own driver (a new conversation, or a follow-up in the same one), and returns the turn with
its journal, the frames the client read and the scripted model's own record of every request (the tool names each
request offered). ``held`` runs a turn whose first model call the scripted model holds, so a test can change the
world while the attempt is running.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import json
import os
import secrets
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lane
from driver import Driver, Paths, TurnRecord, collect
from fixtures import Fixtures
from lane import Api, Frame, Stack, StackError, Stream, wait_until
from matrix import Row
from plan import AgentSpec, ProcessEnv, RowPlan, TurnSpec
from scripts import Script

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from observe import Observation

Json = dict[str, Any]
REPO = Path(__file__).resolve().parents[3]
RUNNER_IMAGE = "pxh-mcp-runner:mcpe2e"
ECHO_SERVER = REPO / "backend/python/harness_core/testing/sandbox/mcp_echo_server.py"
# Executor -> (the agent's engine, the provider wire its model is configured on).
EXECUTORS: dict[str, tuple[str, str]] = {
    "loop": ("none", "openai_chat"),
    "opencode": ("opencode", "openai_chat"),
    "codex": ("codex", "openai_responses"),
    "claude_code": ("claude_code", "anthropic_messages"),
}
ENGINE_IMAGES = {"opencode": "pxh-opencode-tools:1.18.34-ph7", "codex": "pxh-codex-tools:0.160.0-ph7",
                 "claude_code": "pxh-claude-code-tools:2.1.319-ph7"}
MODEL_KINDS = frozenset({"loop", "engine"})
INLINE = "inline:"
DEMO_DOMAIN = "acme-demo.example"


class KnownFinding(AssertionError):
    """What a recorded finding still breaks. A strict xfail names it (``raises=KnownFinding``), so the rest of the
    case stays a real assertion and the case turns red the day the finding is fixed."""


def tool(name: str, /, **arguments: Any) -> str:  # noqa: ANN401
    return f"tool {name} {json.dumps(arguments)}"


def discover(*names: str) -> list[str]:
    """The loop discloses a large grant lazily, so a model finds its tools first (QA5-F6). One search per tool:
    a search makes its best few matches visible, not every tool a long query names."""
    return [tool("search_tools", query=name, make_visible=True) for name in names]


@dataclass
class Turn:
    record: TurnRecord
    obs: Observation
    requests: list[Json]
    """The scripted model's record of each request of the turn: ``kind``, ``tools`` (names), sizes, ``at``."""

    @property
    def model_requests(self) -> list[Json]:
        return [r for r in self.requests if r.get("kind") in MODEL_KINDS and r.get("status") == 200]

    def offered(self) -> set[str]:
        """Every tool name any model request of the turn offered."""
        return {str(t) for r in self.model_requests for t in r.get("tools") or []}

    def offers(self, exported: str) -> bool:
        """Whether a model request offered the tool, under the loop's name or an engine's prefixed one."""
        return any(t == exported or t.endswith("_" + exported) for t in self.offered())

    def offered_lists(self) -> list[frozenset[str]]:
        return [frozenset(str(t) for t in r.get("tools") or []) for r in self.model_requests]

    def text(self) -> str:
        """Everything the turn left behind that a person or an operator can read: the session's journal (a tool
        result kept inline is read as the product stored it), the frames and the answer."""
        journal = json.dumps([self.obs.session_events, self.obs.frames, self.obs.answer], default=str)
        return journal + "\n" + "\n".join(self.tool_results())

    def tool_results(self) -> list[str]:
        """What each tool call of the run answered, in order (``tool.outcome``'s inline result)."""
        out = []
        for event in self.obs.of_type("tool.outcome"):
            ref = str(((event.get("data") or {}).get("resultRef") or {}).get("ref") or "")
            if ref.startswith(INLINE):
                out.append(base64.urlsafe_b64decode(ref.removeprefix(INLINE)).decode("utf-8", "replace"))
        return out

    def failure(self) -> str | None:
        failed = self.obs.of_type("run.failed")
        data = (failed[-1].get("data") or {}) if failed else {}
        return str(data.get("errorType") or data.get("reason") or data.get("code") or "") or self.obs.error_code

    def yields(self) -> list[str]:
        return [str((e.get("data") or {}).get("reason")) for e in self.obs.of_type("run.yielded")]


class _TapApi(Api):
    """A user's API whose streams are also seen as they arrive, so a turn that is still running can be found
    (its conversation and run) and stopped."""

    def __init__(self, inner: Api) -> None:
        self.inner = inner
        super().__init__(inner.base, inner.email, inner.password)
        self.live: list[Frame] = []

    # The user's one sign-in: Node limits sign-ins per minute, and every conversation would be another.
    @property
    def _token(self) -> str | None:
        return self.inner._token  # noqa: SLF001 - the same user's session

    @_token.setter
    def _token(self, value: str | None) -> None:
        self.inner._token = value  # noqa: SLF001

    def token(self) -> str:
        return self.inner.token()

    def stream(self, method: str, path: str, body: object | None = None, *, headers: dict[str, str] | None = None,
               stop: Callable[[Frame], bool] | None = None, timeout: float = 240) -> Stream:
        def tap(frame: Frame) -> bool:
            if not self.live or self.live[-1] is not frame:
                self.live.append(frame)
            return stop(frame) if stop is not None else False

        return super().stream(method, path, body, headers=headers, stop=tap, timeout=timeout)


@dataclass
class Pending:
    """A turn that is still running in its own thread."""

    chat: Chat
    thread: threading.Thread
    box: dict[str, Any]

    def ids(self, timeout: float = 120) -> tuple[str, str]:
        """The turn's conversation and run, as soon as the stream has named them."""
        found: dict[str, str] = {}

        def named() -> bool:
            if "error" in self.box:
                raise self.box["error"]
            for frame in list(self.chat.tap.live):
                value = frame.data.get("value") or {}
                if frame.type == "CUSTOM" and frame.name == "conversation_created" and value.get("runId"):
                    found.update(session=str(value["conversationId"]), run=str(value["runId"]))
                elif frame.type == "RUN_STARTED" and frame.data.get("runId") and self.chat.session_id:
                    found.update(session=self.chat.session_id, run=str(frame.data["runId"]))
            return bool(found)

        wait_until(named, timeout=timeout, what="the running turn to name its conversation and run")
        return found["session"], found["run"]

    def journal(self) -> list[Json]:
        session, run = self.ids()
        return [e for e in self.chat.stack.journal(session)["events"] if e.get("runId") == run]

    def wait_for(self, event_type: str, *, timeout: float = 180) -> Json:
        """The run's first journal event of that type."""
        seen: list[Json] = []

        def there() -> bool:
            seen[:] = [e for e in self.journal() if e.get("type") == event_type]
            return bool(seen)

        wait_until(there, timeout=timeout, what=f"{event_type} in the run's journal", every=2)
        return seen[0]

    def cancel(self) -> None:
        session, run = self.ids()
        self.chat.tap.ok("POST", f"{Paths(self.chat.agent_key).run(session, run)}/cancel",
                         {"idempotencyKey": f"mcpe2e-{secrets.token_hex(6)}"})

    def finish(self, timeout: float = 600) -> Turn:
        self.thread.join(timeout=timeout)
        if "error" in self.box:
            raise self.box["error"]
        if "record" not in self.box:
            raise StackError(f"the turn did not end within {timeout:.0f} s")
        record: TurnRecord = self.box["record"]
        self.chat.session_id = record.session_id or self.chat.session_id
        return self.chat.collected(record)


@dataclass
class Chat:
    stack: Stack
    executor: str
    agent_key: str
    api: Api | None = None
    session_id: str | None = None
    turns: list[Turn] = field(default_factory=list[Turn])

    def __post_init__(self) -> None:
        engine, wire = EXECUTORS[self.executor]
        self.tap = _TapApi(self.api or self.stack.api)
        # The driver reads the stack's API for the stream and the card's decision: this user's is swapped in.
        row = Row(id=f"MCPE2E-{self.executor}", array="mcpe2e", stack=self.stack.stack_id, factors={})
        plan = RowPlan(row=row, process=ProcessEnv(engine_runs=True), agent=AgentSpec(family="mcp", engine=engine),
                       turn=TurnSpec(kind="agent", chat_mode="quick", decision="allow_once", wire=wire))
        self._plan = plan
        fixtures = Fixtures(self.stack)
        self._driver = Driver(dataclasses.replace(self.stack, api=self.tap), fixtures, plan)
        self._driver.agent_key = self.agent_key
        self._driver.paths = Paths(self.agent_key)
        self._driver.model = fixtures.model(wire)

    def send(self, *lines: str, answer: str | None = None, decision: str | None = None) -> Turn:
        """One turn read to its end: the lines are the scripted model's directives, then its answer (a marker,
        unless the case writes the answer itself). ``decision`` answers its approval cards (default: allow once)."""
        return self.start(*lines, answer=answer, decision=decision).finish()

    def start(self, *lines: str, answer: str | None = None, decision: str | None = None) -> Pending:
        """A turn sent and left running: for a run that waits, is held, or is stopped by the test."""
        script = Script("mcpe2e", (*lines, f"say {answer or f'MCPE2E {self.executor} {secrets.token_hex(3)}'}"))
        self.stack.model.ok("POST", "/__control/reset")
        self.tap.live.clear()
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["record"] = self._driver.send(script, session_id=self.session_id, decision=decision)
            except Exception as error:  # noqa: BLE001 - handed to the test's thread
                box["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return Pending(self, thread, box)

    def collected(self, record: TurnRecord) -> Turn:
        obs = collect(self.stack, self._plan, record)
        requests = (self.stack.model.ok("GET", "/__control/requests") or {}).get("requests") or []
        turn = Turn(record, obs, list(requests))
        self.turns.append(turn)
        _keep(self.stack, turn)
        return turn

    @contextlib.contextmanager
    def held(self, *lines: str, after: bool = False) -> Iterator[Pending]:
        """A turn one of whose model calls the scripted model holds (the first, or with ``after`` the one that
        follows the lines' tool calls): the body runs while the attempt waits on its model, then the call is
        released; ``finish()`` on the yielded turn reads it to its end."""
        pending = self.start(*lines, "hold") if after else self.start("hold", *lines)
        try:
            wait_until(lambda: "error" in pending.box or bool((self.stack.model.ok("GET", "/__control/holding") or {}).get("holding")),
                       timeout=240, what="the run's first model call to be held")
            if "error" in pending.box:
                raise pending.box["error"]
            yield pending
        finally:
            self.stack.model.ok("POST", "/__control/release")


def _keep(stack: Stack, turn: Turn) -> None:
    """The turn's facts, for a red case's finding (QA6_RESULTS_DIR/mcpe2e), as the row runner keeps a row's."""
    out = Path(os.environ.get("QA6_RESULTS_DIR", str(stack.run_dir))) / "mcpe2e"
    out.mkdir(parents=True, exist_ok=True)
    body = {"script": turn.record.script.lines, "terminal": turn.obs.terminal, "errorCode": turn.obs.error_code,
            "answer": turn.obs.answer, "notes": turn.obs.notes, "types": turn.obs.types(),
            "failed": turn.obs.of_type("run.failed", "run.yielded"), "toolResults": turn.tool_results(),
            "modelRequests": turn.requests}
    (out / f"{turn.record.request_id}.json").write_text(json.dumps(body, indent=1, default=str))


# Every writable file under 1 MB and every process's environment and command line, from inside a sandbox (the
# engine matrix's dump, tests/unit/harness/engines/matrix/confinement.py: `sh` only, the engine images have no python).
SANDBOX_DUMP = r"""
for m in $(awk '$4 ~ /(^|,)rw(,|$)/ && $3 !~ /^(proc|sysfs|cgroup|cgroup2|devpts|mqueue)$/ && $2 !~ /^\/(proc|sys|dev)/ {print $2}' /proc/self/mounts); do
  find "$m" -xdev -type f -size -1024k 2>/dev/null | while IFS= read -r f; do
    echo "== FILE $f"; cat "$f" 2>/dev/null; echo
  done
done
for f in /proc/[0-9]*/environ /proc/[0-9]*/cmdline; do
  echo "== PROC $f"; tr '\0' '\n' < "$f" 2>/dev/null; echo
done
"""


def engine_sandbox_dump(run_id: str) -> str:
    """What the run's live engine sandbox holds that its engine could read."""
    found = subprocess.run(["docker", "ps", "-q", "--filter", "label=io.pipeshub.sandbox-broker.managed=1",
                            "--filter", f"label=runId={run_id}"], check=True, capture_output=True, text=True).stdout.split()
    if len(found) != 1:
        raise StackError(f"run {run_id} has {len(found)} live sandboxes, not one")
    done = subprocess.run(["docker", "exec", found[0], "/bin/sh", "-c", SANDBOX_DUMP], check=False,
                          capture_output=True, timeout=120)
    return done.stdout.decode("utf-8", "replace")


def executors_here() -> list[str]:
    """The executors this host can run: the loop, and each engine whose release image is present."""
    def present(image: str) -> bool:
        return subprocess.run(["docker", "image", "inspect", image], check=False, capture_output=True).returncode == 0

    here = ["loop", *(name for name, image in ENGINE_IMAGES.items() if present(image))]
    # MCPE2E_EXECUTORS narrows a development run; unset, every executor the host can run is run.
    asked = [e.strip() for e in os.environ.get("MCPE2E_EXECUTORS", "").split(",") if e.strip()]
    return [e for e in here if not asked or e in asked]


def build_runner_image(base: str) -> str:
    """The STDIO servers' image: the sandbox image plus the repo's stdlib MCP server (one layer)."""
    context = Path(os.environ.get("MCPE2E_WORK", str(lane.STATE_DIR))) / "mcpe2e-runner-image"
    context.mkdir(parents=True, exist_ok=True)
    (context / "server.py").write_bytes(ECHO_SERVER.read_bytes())
    (context / "Dockerfile").write_text(f"FROM {base}\nCOPY server.py /opt/pxh-echo/server.py\n")
    built = subprocess.run(["docker", "build", "-q", "-t", RUNNER_IMAGE, "--label", "pipeshub.harness.owner=x-lead",
                            str(context)], check=False, capture_output=True, text=True)
    if built.returncode != 0:
        raise StackError(f"building {RUNNER_IMAGE}: {built.stderr[-500:]}")
    return RUNNER_IMAGE


def hub_environment() -> dict[str, str]:
    """What ``run.sh matrix-up`` reads to bring a stack up with the MCP hub on and the scripted MCP servers."""
    sandbox = os.environ.get("E2E_SANDBOX_IMAGE", "pxh-sandbox:x1")
    return {"E2E_MCP_HUB": "1", "E2E_MCP_RUNNER_IMAGE": build_runner_image(sandbox),
            # This lane's stacks are Compose project pxh-mcpe2e<stack><id>, not pxh-e2e-…: told apart on a shared host.
            "E2E_PROJECT_PREFIX": "pxh-"}


@dataclass
class Member:
    api: Api
    user_id: str
    email: str


def second_user(stack: Stack, name: str = "bob") -> Member:
    """A member of the org who signs in with a password (the product allows a starting password for its demo
    domain only, without SMTP), made once per stack and waited for until the agent service knows them."""
    kept = stack.run_dir / f"mcpe2e-user-{name}.json"
    if kept.exists():
        saved = json.loads(kept.read_text())
        return Member(Api(stack.api.base, saved["email"], saved["password"]), saved["id"], saved["email"])
    email = f"{name}-{secrets.token_hex(3)}@{DEMO_DOMAIN}"
    password = f"Mcp-e2e-{secrets.token_hex(6)}!1a"
    created = stack.api.ok("POST", "/api/v1/users", {"fullName": name.title(), "email": email, "role": "member",
                                                     "password": password})
    user = (created or {}).get("user") or created or {}
    member = Member(Api(stack.api.base, email, password), str(user.get("_id") or user.get("id") or ""), email)

    def known() -> bool:
        try:
            return member.api.request("GET", "/api/v1/agents").status == 200
        except StackError:
            # The minute's sign-in limit (the lane signs in often): the next minute's attempt answers.
            return False

    wait_until(known, timeout=180, what=f"{name} to sign in and be known to the agent service", every=5)
    kept.write_text(json.dumps({"email": email, "password": password, "id": member.user_id}))
    kept.chmod(0o600)
    return member
