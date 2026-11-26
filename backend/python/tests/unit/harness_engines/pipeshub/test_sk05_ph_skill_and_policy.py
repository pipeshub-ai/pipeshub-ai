"""SK-05 (C3.56): ``ph skill`` reads the run's skills for an engine without a skills tool of its own; G-25 (C20.26,
GA-14): ``ph policy check`` asks the Tool Gateway's policy check, and as the managed hook (``--hook``) fails closed.

``ph`` runs in process against the same JSON-RPC test server ``test_ph_cli`` uses, which answers the way the tool
gateway's MCP transport does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_engines.pipeshub import ph
from tests.unit.harness_engines.pipeshub import test_ph_cli as cli
from tests.unit.harness_engines.pipeshub.test_ph_cli import Gateway, run

# ``test_ph_cli``'s fixtures: its test gateway server and the folder ``ph`` reads.
gateway = cli.gateway
folder = cli.folder

REPORT = "---\nname: report\ndescription: Use when asked for a report.\n  Two lines.\n---\n# Report\n\nWrite it.\n"


@pytest.fixture
def skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    (root / "report" / "templates").mkdir(parents=True)
    (root / "report" / "SKILL.md").write_text(REPORT)
    (root / "report" / "templates" / "a.md").write_text("x")
    (root / "deck").mkdir()
    (root / "deck" / "SKILL.md").write_text("# Deck\nNo front matter.\n")
    (root / "not-a-skill").mkdir()
    monkeypatch.setenv(ph.SKILLS_ENV, str(root))
    return root


def test_sk05_c3_56_skill_list_names_each_skill_with_its_description(skills: Path) -> None:
    code, out, err = run(["skill", "list"])
    assert (code, err) == (0, "")
    assert out.splitlines() == ["deck", "report: Use when asked for a report."]
    code, out, _ = run(["--json", "skill", "list"])
    assert json.loads(out) == {
        "ok": True,
        "command": "skill.list",
        "data": {"skills": [{"name": "deck", "description": ""}, {"name": "report", "description": "Use when asked for a report."}]},
    }


def test_sk05_skill_show_prints_the_instructions_and_the_other_files(skills: Path) -> None:
    code, out, _ = run(["skill", "show", "report"])
    assert code == 0
    assert out.startswith("---\nname: report\n") and "# Report\n\nWrite it." in out
    assert out.rstrip().endswith(f"Its other files, under {skills / 'report'}:\n  templates/a.md")
    code, out, _ = run(["--json", "skill", "show", "report"])
    data = json.loads(out)["data"]
    assert (data["name"], data["files"], data["content"]) == ("report", ["templates/a.md"], REPORT)


@pytest.mark.parametrize("name", ["nope", "not-a-skill", "../report", "report/SKILL.md", ".."])
def test_sk05_a_name_that_is_no_skill_of_the_run_is_refused(skills: Path, name: str) -> None:
    code, out, err = run(["skill", "show", name])
    assert out == "" and code in (ph.EX_DATAERR, ph.EX_USAGE), (name, err)


def test_sk05_the_skills_folder_is_the_engines_or_the_pipeshub_link(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ph.SKILLS_ENV, raising=False)
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    assert ph.skills_dir() == str(folder / "skills")
    assert ph.skills_dir({ph.SKILLS_ENV: "/skills"}) == "/skills"
    code, out, _ = run(["skill", "list"])
    assert (code, out.strip()) == (0, "This run has no skills.")


def _denying(state: Gateway) -> None:
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "/tmp/x is outside the workspace: edits stay under /workspace."}],
        "structuredContent": {"disposition": "denied", "retryable": False, "reasonCode": "outside_workspace"},
        "isError": True,
    }


HOOK_WRITE = {
    "session_id": "s",
    "hook_event_name": "PreToolUse",
    "cwd": "/workspace",
    "tool_name": "Write",
    "tool_input": {"file_path": "/tmp/x", "content": "hello"},
}


def test_g25_policy_check_asks_the_gateways_check_and_never_decides_itself(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    code, out, err = run(["policy", "check", "edit", "--path", "/workspace/a.md"], url)
    assert (code, out.strip(), err) == (0, "allowed", "")
    ((_, sent),) = state.requests
    assert sent["method"] == "tools/call"
    assert sent["params"] == {"name": ph.POLICY_CHECK, "arguments": {"tool": "edit", "name": "edit", "paths": ["/workspace/a.md"]}}
    _denying(state)
    code, out, err = run(["policy", "check", "edit", "--path", "/tmp/x"], url)
    assert (code, out) == (ph.EX_REFUSED, "")
    assert err.strip() == "PipesHub policy refused this edit call: /tmp/x is outside the workspace: edits stay under /workspace."


@pytest.mark.parametrize(
    ("payload", "sent"),
    [
        (HOOK_WRITE, {"tool": "edit", "name": "Write", "paths": ["/tmp/x"]}),
        ({"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"tool": "shell", "name": "Bash", "paths": []}),
        (
            {"tool_name": "Edit", "cwd": "/workspace/repo", "tool_input": {"file_path": "src/a.py"}},
            {"tool": "edit", "name": "Edit", "paths": ["/workspace/repo/src/a.py"]},
        ),
        ({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/workspace/n.ipynb"}}, {"tool": "edit", "name": "NotebookEdit", "paths": ["/workspace/n.ipynb"]}),
        ({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}}, {"tool": "web", "name": "WebFetch", "paths": []}),
        # A tool the hook does not know is sent as unknown, which the gateway refuses.
        ({"tool_name": "Teleport", "tool_input": {}}, {"tool": "unknown", "name": "Teleport", "paths": []}),
    ],
)
def test_g25_the_hook_input_is_sent_as_the_native_call_it_is(
    gateway: tuple[Gateway, str], payload: dict[str, Any], sent: dict[str, Any]
) -> None:
    state, url = gateway
    code, out, err = run(["policy", "check", "--hook"], url, stdin=json.dumps(payload))
    assert (code, out, err) == (0, "", "")
    assert state.requests[-1][1]["params"]["arguments"] == sent


def test_ga14_the_hook_blocks_a_refused_call_with_the_reason_on_stderr(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    _denying(state)
    code, out, err = run(["policy", "check", "--hook"], url, stdin=json.dumps(HOOK_WRITE))
    assert (code, out) == (2, "")
    assert "outside the workspace" in err


@pytest.mark.parametrize(
    "broken",
    [
        "not json",
        "[1, 2]",
        json.dumps({"tool_input": {}}),
    ],
)
def test_ga14_a_hook_input_it_cannot_read_is_refused(gateway: tuple[Gateway, str], broken: str) -> None:
    state, url = gateway
    code, _, err = run(["policy", "check", "--hook"], url, stdin=broken)
    assert code == 2 and "refused" in err
    assert state.requests == []


def test_ga14_the_hook_fails_closed_when_the_gateway_cannot_answer(
    folder: Path, gateway: tuple[Gateway, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, url = gateway
    # Unreachable.
    code, _, err = run(["policy", "check", "--hook"], "http://127.0.0.1:9/k/mcp", stdin=json.dumps(HOOK_WRITE))
    assert code == 2 and "could not reach" in err and "refused" in err
    # The run ended: the gateway refuses the sandbox's credentials.
    state.status, state.headers = 401, {"x-gateway-refusal": "token_expired"}
    code, _, err = run(["policy", "check", "--hook"], url, stdin=json.dumps(HOOK_WRITE))
    assert code == 2 and "refused" in err
    # An answer that is neither an allowance nor a refusal.
    state.status = 200
    state.answer = lambda name, arguments: {"content": [], "structuredContent": {"disposition": "deferred"}, "isError": False}
    code, _, err = run(["policy", "check", "--hook"], url, stdin=json.dumps(HOOK_WRITE))
    assert code == 2 and "could not be read" in err
    # Outside a run: no gateway at all.
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    monkeypatch.delenv(ph.URL_ENV, raising=False)
    code, _, err = run(["policy", "check", "--hook"], None, stdin=json.dumps(HOOK_WRITE))
    assert code == 2 and "refused" in err


def test_ga14_the_policy_check_waits_far_less_than_the_hooks_own_timeout() -> None:
    from harness_core.engines.native_policy import HOOK_TIMEOUT_S, POLICY_CHECK_TOOL

    assert ph.POLICY_CHECK == POLICY_CHECK_TOOL
    assert ph.POLICY_TIMEOUT_S * 2 <= HOOK_TIMEOUT_S


def test_ga14_each_turns_session_folder_holds_the_hook_program_and_it_is_this_ph() -> None:
    """The hook runs the worker's own ``ph``, seeded read-only at every turn, so it always speaks the gateway's check
    whatever ``ph`` the engine image was built with."""
    from harness_core.access.types import ModelApi
    from harness_core.engines.types import GatewayEndpoints
    from harness_engines.pipeshub import (
        HOOK_PROGRAM_FILE,
        POLICY_HOOK_PATH,
        SESSION_DIR,
        pipeshub_tool_folder,
        session_files,
    )

    endpoints = GatewayEndpoints("http://m/v1", ModelApi.RESPONSES, "http://t/mcp", "x" * 16)
    assert pipeshub_tool_folder().policy_hook == POLICY_HOOK_PATH == f"{SESSION_DIR}/{HOOK_PROGRAM_FILE}"
    assert session_files((), endpoints)[HOOK_PROGRAM_FILE] == Path(ph.__file__).read_bytes()
