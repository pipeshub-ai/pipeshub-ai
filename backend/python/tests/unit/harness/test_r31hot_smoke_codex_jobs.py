"""R31-HOT: the human-test smoke covers a common shape, which its 65 checks missed.

A default chat can hold Atlassian Rovo whole (at first use) and hand its work to a Codex job; every such job ran
with ``mcpServers=0``. The smoke's MCP engine checks ran on saved agents, whose servers name their tools, so they
passed. The new checks run on the default chat's Codex job: it keeps the server held at first use and calls its tool,
and reads the pdf skill mounted for it. Node, the stack and the logs are fakes here: what each check decides from what
it reads is under test.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))

smoke = importlib.import_module("smoke_human")
from lane import Frame  # noqa: E402

PARENT, CHILD, SESSION, MCP = "run-parent", "run-child", "ses-1", "inst-1"


class _Api:
    def ok(self, method: str, path: str, body: object | None = None) -> Any:  # noqa: ANN401
        assert path == "/api/v1/mcp-servers/my-mcp-servers"
        return {"instances": [{"_id": MCP, "name": smoke.MCP_NAME}]}


class _Saas:
    def __init__(self) -> None:
        self.reads: list[dict[str, Any]] = []

    def ok(self, method: str, path: str) -> Any:  # noqa: ANN401
        return {"reads": list(self.reads)}


def _stack(saas: _Saas, *, whole: bool = True) -> SimpleNamespace:
    entry: dict[str, Any] = {"instanceId": MCP, "manifestDigest": "sha256:" + "0" * 64}
    if whole:
        entry["tools"] = "at_first_use"
    events = [
        {"runId": PARENT, "type": "run.admitted"},
        {"runId": PARENT, "type": "run.spec_pinned",
         "data": {"spec": {"grants": {"tools": []}, "extensions": {"x.pipeshub": {"mcp": [entry]}}}}},
    ]
    return SimpleNamespace(api=_Api(), saas=saas, journal=lambda session: {"events": events})


def _job(monkeypatch: pytest.MonkeyPatch, saas: _Saas, lines: list[str], output: str = "", *, reads: bool) -> None:
    frames = [
        Frame(None, "RUN_STARTED", {"runId": CHILD, "parentRunId": PARENT}),
        Frame(None, "CUSTOM", {"name": "engine_progress", "runId": CHILD, "value": {"kind": "output", "text": output}}),
        Frame(None, "RUN_FINISHED", {}),
    ]

    def turn(stack: object, key: None, text: str, **_: object) -> tuple[list[Frame], str, str]:
        if reads:
            saas.reads.append({"tool": "lookup_note"})
        return frames, SESSION, PARENT

    monkeypatch.setattr(smoke, "turn", turn)
    monkeypatch.setattr(smoke, "worker_lines", lambda run: [f"{line} runId={run} " for line in lines] if run == CHILD else [])


@pytest.fixture(autouse=True)
def _fresh_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "results", [])


def _outcome() -> tuple[str, str]:
    [(_, outcome, detail)] = smoke.results
    return outcome, detail


def test_r31hot_smoke_the_codex_job_that_keeps_the_server_and_calls_it_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    saas = _Saas()
    _job(monkeypatch, saas, ["engine.assembly engine=codex granted=27 skills=9", "run.tool_environment mcpServers=1"],
         reads=True)
    smoke.codex_job_mcp(_stack(saas))  # type: ignore[arg-type]
    assert _outcome()[0] == smoke.OK


@pytest.mark.parametrize(
    ("servers", "reads", "whole"),
    [
        ("0", True, True),  # the recorded failure: the job dropped the server
        ("1", False, True),  # the tool never reached the SaaS
        ("1", True, False),  # not that shape: the parent named the server's tools
    ],
)
def test_r31hot_smoke_the_mcp_job_check_fails_where_the_owner_saw_it_fail(
    monkeypatch: pytest.MonkeyPatch, servers: str, reads: bool, whole: bool
) -> None:
    saas = _Saas()
    _job(monkeypatch, saas, ["engine.assembly engine=codex granted=27 skills=9", f"run.tool_environment mcpServers={servers}"],
         reads=reads)
    smoke.codex_job_mcp(_stack(saas, whole=whole))  # type: ignore[arg-type]
    assert _outcome()[0] == smoke.FAIL


def test_r31hot_smoke_a_job_that_is_not_on_codex_fails_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    saas = _Saas()
    _job(monkeypatch, saas, ["engine.assembly engine=opencode granted=27 skills=9", "run.tool_environment mcpServers=1"],
         reads=True)
    smoke.codex_job_mcp(_stack(saas))  # type: ignore[arg-type]
    outcome, detail = _outcome()
    assert outcome == smoke.FAIL and "on opencode" in detail


@pytest.mark.parametrize(
    ("output", "skills", "outcome"),
    [
        ("---\nname: pdf\ndescription: PDFs\n---\n", "9", smoke.OK),
        ("head: cannot open '/skills/pdf/SKILL.md'", "9", smoke.FAIL),
        ("---\nname: pdf\n", "0", smoke.FAIL),
    ],
)
def test_r31hot_smoke_the_skill_job_reads_the_mounted_pdf_skill(
    monkeypatch: pytest.MonkeyPatch, output: str, skills: str, outcome: str
) -> None:
    saas = _Saas()
    _job(monkeypatch, saas, [f"engine.assembly engine=codex granted=27 skills={skills}"], output, reads=False)
    smoke.codex_job_skill(_stack(saas))  # type: ignore[arg-type]
    assert _outcome()[0] == outcome
