"""TESTINFRA: the human-test smoke (``deployment/e2e/smoke_human.py``) does not depend on the admin's leftover settings.

The smoke once went 51/56 because a tester had left the admin's personal coding-engine choice on codex
(every default-chat Agent-mode turn handed off to a code_task child) and their own Claude account connected. The
smoke now sets the choice each group of checks needs and puts the previous one back however the checks end, and
skips (never fails, never signs anyone out) the Claude Code engine-child checks while the admin's own account is on.
Node's API is a fake here: the state handling is what is under test, not the stack.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))

smoke = importlib.import_module("smoke_human")
from lane import Frame, HttpAnswer  # noqa: E402


@dataclass
class FakeApi:
    """Node's coding-engine and engine-account routes over one admin's stored settings."""

    choice: str = "org_default"
    org_default: str | None = None
    claude: str = "available"
    enabled: bool = False
    me_status: int = 200
    refuse: set[tuple[str, str]] = field(default_factory=set)
    calls: list[tuple[str, str, Any]] = field(default_factory=list)

    def request(self, method: str, path: str, body: object | None = None, **_: object) -> HttpAnswer:
        self.calls.append((method, path, body))
        if (method, str((body or {}).get("choice") if isinstance(body, dict) else "")) in self.refuse:
            return HttpAnswer(400, {"code": "ENGINE_UNAVAILABLE"}, {})
        if path == smoke.ENGINE_ACCOUNTS_ME:
            return HttpAnswer(self.me_status, {"claude": {"state": self.claude, "enabled": self.enabled}} if self.me_status == 200 else {}, {})
        if path == smoke.CODING_ENGINE and method == "PUT":
            self.choice = body["choice"]  # type: ignore[index]
        elif path == smoke.ORG_CODING_ENGINE:
            self.org_default = body["defaultEngine"]  # type: ignore[index]
        return HttpAnswer(200, {"choice": self.choice, "orgDefault": self.org_default}, {})

    def ok(self, method: str, path: str, body: object | None = None) -> Any:  # noqa: ANN401
        return self.request(method, path, body).body

    def puts(self) -> list[Any]:
        return [body for method, path, body in self.calls if method == "PUT" and path == smoke.CODING_ENGINE]


@pytest.fixture(autouse=True)
def _fresh_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "results", [])


def _stack(api: FakeApi) -> SimpleNamespace:
    return SimpleNamespace(api=api, seed={key: key for key in smoke.AGENTS})


@pytest.mark.parametrize("left", ["codex", "claude_code", "org_default", "none"])
def test_testinfra_default_chat_checks_run_with_no_engine_and_the_choice_is_put_back(
    monkeypatch: pytest.MonkeyPatch, left: str
) -> None:
    api = FakeApi(choice=left)
    seen: list[str] = []
    monkeypatch.setattr(smoke, "assistant_mcp", lambda stack: seen.append(api.choice))
    monkeypatch.setattr(smoke, "selection_scope", lambda stack, member: seen.append(api.choice))
    smoke.default_chat(_stack(api), member=None)  # type: ignore[arg-type]
    assert seen == ["none", "none"]
    assert api.choice == left
    assert api.puts() == ([] if left == "none" else [{"choice": "none"}, {"choice": left}])
    assert smoke.results == []


def test_testinfra_the_choice_is_put_back_when_a_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(choice="codex")

    def broken(stack: object) -> None:
        raise smoke.StackError("stream 502: bad gateway")

    monkeypatch.setattr(smoke, "assistant_mcp", broken)
    with pytest.raises(smoke.StackError, match="bad gateway"):
        smoke.default_chat(_stack(api), member=None)  # type: ignore[arg-type]
    assert api.choice == "codex"


def test_testinfra_a_choice_that_cannot_be_set_fails_one_named_check_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(choice="codex", refuse={("PUT", "none")})
    monkeypatch.setattr(smoke, "assistant_mcp", lambda stack: pytest.fail("ran without its setting"))
    smoke.default_chat(_stack(api), member=None)  # type: ignore[arg-type]
    [(name, outcome, detail)] = smoke.results
    assert outcome == smoke.FAIL and "set to none" in name and "400" in detail
    assert api.choice == "codex"


def test_testinfra_a_choice_that_cannot_be_put_back_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(choice="codex", refuse={("PUT", "codex")})
    monkeypatch.setattr(smoke, "assistant_mcp", lambda stack: None)
    monkeypatch.setattr(smoke, "selection_scope", lambda stack, member: None)
    smoke.default_chat(_stack(api), member=None)  # type: ignore[arg-type]
    [(name, outcome, detail)] = smoke.results
    assert (name, outcome) == ("admin's personal coding-engine choice put back", smoke.FAIL)
    assert detail.startswith("codex: 400")


def _engine_turns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each coder agent's turn finishes with an engine child that reads the SaaS once."""
    agents: list[str] = []
    reads: list[int] = []

    def turn(stack: object, key: str, text: str, **_: object) -> tuple[list[Frame], str, str]:
        agents.append(key)
        reads.append(1)
        return [Frame(None, "RUN_STARTED", {"runId": "c", "parentRunId": "r"}),
                Frame(None, "RUN_FINISHED", {})], "s", "r"

    monkeypatch.setattr(smoke, "turn", turn)
    monkeypatch.setattr(smoke, "saas", lambda stack, what: list(reads))
    return agents


@pytest.mark.parametrize("claude", ["connected", "available"])
def test_testinfra_claude_code_checks_skip_while_the_admin_uses_their_own_claude(
    monkeypatch: pytest.MonkeyPatch, claude: str
) -> None:
    api = FakeApi(claude=claude, enabled=True)
    agents = _engine_turns(monkeypatch)
    smoke.engines(_stack(api))  # type: ignore[arg-type]
    assert "agentClaudeCode" not in agents
    skipped = [(name, detail) for name, outcome, detail in smoke.results if outcome == smoke.SKIP]
    assert [name for name, _ in skipped] == [
        "engine run agentClaudeCode (shell command in its code_task child, scripted model)",
        "MCP tool lookup_note called from agentClaudeCode's code_task engine child",
        "MCP tool lookup_note called by name, with no search, from agentClaudeCode's code_task engine child",
    ]
    assert {detail for _, detail in skipped} == {
        "admin has their own Claude account switched on; this check needs the org's model"}
    assert [call for call in api.calls if call[0] != "GET"] == []
    line, passed = smoke.summary()
    assert passed
    assert line.startswith("6 of 9 checks passed, 3 skipped: ")


@pytest.mark.parametrize(("claude", "enabled", "status"), [
    ("available", False, 200), ("disconnected", False, 200), ("not_allowed", False, 200), ("connected", False, 200),
    ("connected", True, 404), ("connected", True, 503)])
def test_testinfra_claude_code_checks_run_without_the_admins_own_account(
    monkeypatch: pytest.MonkeyPatch, claude: str, enabled: bool, status: int
) -> None:
    agents = _engine_turns(monkeypatch)
    smoke.engines(_stack(FakeApi(claude=claude, enabled=enabled, me_status=status)))  # type: ignore[arg-type]
    assert agents.count("agentClaudeCode") == 3
    assert [outcome for _, outcome, _ in smoke.results] == [smoke.OK] * 9


def test_testinfra_engpick_restores_the_org_default_and_the_admins_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(choice="opencode", org_default="claude_code")
    during: list[tuple[str, str | None]] = []

    def turn(stack: object, key: None, text: str, **_: object) -> tuple[list[Frame], str, str]:
        during.append((api.choice, api.org_default))
        return [Frame(None, "RUN_FINISHED", {})], "s", "r"

    monkeypatch.setattr(smoke, "turn", turn)
    monkeypatch.setattr(smoke, "saas", lambda stack, what: [])
    smoke.default_chat_engine(_stack(api))  # type: ignore[arg-type]
    # R31-HOT: the shell job, the MCP job and the skill job, all on the org's Codex.
    assert during == [("org_default", "codex")] * 3
    assert (api.choice, api.org_default) == ("opencode", "claude_code")


def test_testinfra_summary_counts_skips_apart_and_only_a_failure_fails() -> None:
    smoke.check("a", True)
    smoke.skip("b", "why")
    assert smoke.summary() == ("1 of 2 checks passed, 1 skipped: ['b']", True)
    smoke.check("c", False)
    assert smoke.summary() == ("1 of 3 checks passed, 1 skipped: ['b']; failed: ['c']", False)


def test_testinfra_importing_the_smoke_reads_no_run_directory() -> None:
    assert smoke.RUN_DIR == Path()


def test_childref_the_deep_long_answer_script_reaches_the_coordinator_through_the_intent_step() -> None:
    """The loop sub-agent check sent ``say <text>`` alone. Deep mode's intent step sends the request
    as ``user query : <request>`` (app/agents/agent_loop/intent.py), so the directive was not on a line of its own,
    the scripted model did not rewrite it, and the coordinator answered without spawning a sub-agent. The script now
    opens with a plain line: rewritten, it reaches the coordinator, which hands it to ``spawn_agent``."""
    import importlib.util as util

    from harness_core.testing.engines.script import ChatMessage

    os.environ.setdefault("FAKE_LLM_API_KEY", "sk-childref")
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "control")
    spec = util.spec_from_file_location("childref_fake_llm", REPO / "deployment" / "e2e" / "fake_llm" / "server.py")
    assert spec is not None and spec.loader is not None
    fake = util.module_from_spec(spec)
    spec.loader.exec_module(fake)

    said = " ".join(f"m-{i:04d}" for i in range(smoke.LONG_ANSWER_WORDS))
    intent = lambda script: [ChatMessage("system", "route"), ChatMessage("user", f"user query : {script}")]  # noqa: E731
    assert fake._rewrite(intent(f"say {said}"), ["task_complete"]) is None, "the recorded failure"
    rewritten = fake._rewrite(intent(f"{smoke.LONG_ANSWER_LEAD}\nsay {said}"), ["task_complete"])
    assert rewritten is not None
    coordinator = [ChatMessage("system", "plan"), ChatMessage("user", rewritten.content)]
    made: list[str] = []
    for _ in range(3):
        reply = fake._delegate(coordinator, ["create_plan", "critique_plan", "spawn_agent", "verify_result"], "c")
        assert reply is not None and reply.tool_calls
        made.append(reply.tool_calls[0].name)
        coordinator.append(ChatMessage("assistant", "", tool_calls=reply.tool_calls))
        coordinator.append(ChatMessage("tool", "ok", tool_call_id=reply.tool_calls[0].id))
    assert made == ["create_plan", "critique_plan", "spawn_agent"]


@pytest.mark.parametrize("refused", [False, True])
def test_childref_the_long_answer_checks_read_the_childs_own_ending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, refused: bool
) -> None:
    """CHILDREF: a parent answered alike whether or not its Codex child's completion was refused, so each long
    answer check reads the child's own lines in the worker log: completed, and no commit refused."""
    asked: list[tuple[str, str, int]] = []

    def turn(stack: object, key: str, text: str, *, mode: str = "quick", **_: object) -> tuple[list[Frame], str, str]:
        child = f"child-{len(asked)}"
        asked.append((key, mode, len(text)))
        said = text.removeprefix("say ")
        return [Frame(None, "RUN_STARTED", {"runId": child, "parentRunId": "r"}),
                Frame(None, "TEXT_MESSAGE_CONTENT", {"delta": said}),
                Frame(None, "RUN_FINISHED", {})], "s", "r"

    lines = []
    for child in ("child-0", "child-1"):
        if refused:
            lines.append(f"x - ERROR - journal.rejected orgId=o runId={child} reason=VALIDATION_ERROR status=400")
        else:
            lines.append(f"x - INFO - engine.kernel.complete orgId=o runId={child} outcome=answered")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "worker.log").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(smoke, "RUN_DIR", tmp_path)
    monkeypatch.setattr(smoke, "turn", turn)
    smoke.long_answers(_stack(FakeApi()))  # type: ignore[arg-type]
    assert [(key, mode) for key, mode, _ in asked] == [("agentOpencode", "quick"), ("agentLoop", "deep")]
    assert all(size > 8 * 1024 for _, _, size in asked)
    outcomes = [outcome for _, outcome, _ in smoke.results]
    assert outcomes == ([smoke.FAIL] * 2 if refused else [smoke.OK] * 2)
    if refused:
        assert "1 commits refused" in smoke.results[0][2]


@dataclass
class TimelineApi:
    """Node's child-timeline route, holding the engine frames a reload reads."""

    frames: list[Any]
    paths: list[str] = field(default_factory=list)

    def request(self, method: str, path: str, body: object | None = None, **_: object) -> HttpAnswer:
        self.paths.append(path)
        return HttpAnswer(200, {"engine": {"frames": self.frames}}, {})


def _code_task_frames(child: str, marker: str, *, with_steps: bool) -> list[Frame]:
    line = lambda kind, text, data: Frame(None, "CUSTOM", {  # noqa: E731
        "type": "CUSTOM", "name": "engine_progress", "runId": child,
        "value": {"kind": kind, "text": text, "data": data}})
    steps = [line("file", "ctr.py", {"change": "added"}), line("command", "python3 ctr.py", {"callId": "c1"}),
             line("output", f"{marker}\n", {"callId": "c1"}), line("command_done", "", {"callId": "c1", "exitCode": 0})]
    return [Frame(None, "RUN_STARTED", {"type": "RUN_STARTED", "runId": child, "parentRunId": "root"}),
            *(steps if with_steps else []), Frame(None, "RUN_FINISHED", {"type": "RUN_FINISHED"})]


@pytest.mark.parametrize("with_steps", [True, False])
def test_ctresults_the_code_task_check_needs_the_childs_output_exit_and_file_on_the_stream_and_its_timeline(
    monkeypatch: pytest.MonkeyPatch, with_steps: bool
) -> None:
    sent: list[str] = []

    def turn(stack: object, key: str, text: str, **_: object) -> tuple[list[Frame], str, str]:
        sent.append(text)
        marker = text.split("say ", 1)[1]
        return _code_task_frames("child", marker, with_steps=with_steps), "s", "r"

    monkeypatch.setattr(smoke, "turn", turn)
    marker_frames: list[Any] = []
    api = TimelineApi(marker_frames)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda n: "abc")
    marker_frames.extend(f.data["value"] for f in _code_task_frames("child", "ctr-abc", with_steps=with_steps)
                         if f.data.get("name") == "engine_progress")
    smoke.code_task_steps(_stack(api))  # type: ignore[arg-type]
    assert "shell python3 ctr.py" in sent[0]
    assert api.paths == ["/api/v1/agents/agentOpencode/conversations/s/runs/child/timeline"]
    assert [outcome for _, outcome, _ in smoke.results] == ([smoke.OK] if with_steps else [smoke.FAIL])


def test_r46lane_a_one_shot_job_that_exited_0_is_done_not_down() -> None:
    """R46-LANE: on a kv-etcd stack (ST-4) etcd waits for etcd-tls to complete; the smoke failed its exit as a
    container that is not running."""
    jobs = smoke.one_shot_jobs(["etcd-tls:service_completed_successfully:false", "", "redis:service_healthy:true"])
    assert jobs == frozenset({"etcd-tls"})
    assert smoke.container_ok("etcd-tls", "exited", "Exited (0) 6 minutes ago", jobs)
    assert not smoke.container_ok("etcd-tls", "exited", "Exited (1) 6 minutes ago", jobs)
    assert not smoke.container_ok("etcd", "exited", "Exited (0) 6 minutes ago", jobs)
    assert smoke.container_ok("etcd", "running", "Up 2 minutes (healthy)", jobs)
    assert not smoke.container_ok("etcd", "running", "Up 2 minutes (unhealthy)", jobs)
