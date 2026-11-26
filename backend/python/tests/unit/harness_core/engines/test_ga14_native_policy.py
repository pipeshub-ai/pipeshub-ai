"""C20.26 / G-25 / GA-14: the run's policy for an engine's own tool calls, and the Tool Gateway's check of it.

The managed hook (Claude Code and Codex ``PreToolUse``) asks ``pipeshub.policy_check`` through ``ph policy check``;
the ACP driver's permission bridge asks the same ``NativeCallPolicy``. The gateway answers from the run's profile,
never runs or journals anything for it, and refuses whatever it cannot read, as the hook refuses whatever it cannot
get an answer for: here the verdicts, the gateway's answer, and the hook command's fail-closed exit statuses.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.native_policy import (
    ENGINE_CONFIG,
    HOOK_BLOCK_STATUS,
    NO_POLICY,
    NOT_PERMITTED,
    OUTSIDE_WORKSPACE,
    POLICY_CHECK_TOOL,
    UNREADABLE,
    NativeCall,
    NativeCallPolicy,
    native_call_from_wire,
    native_call_wire,
    policy_hook_command,
)
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import EngineMode, EngineProfile, EngineVersion
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    GRANTED,
    build_endpoint,
    claims_for,
)

if TYPE_CHECKING:
    from pathlib import Path

ENGINE = EngineVersion("codex", "0.160.0", "sha256:" + "d" * 64)
BUILD = NativeCallPolicy.for_profile(EngineProfile(engine=ENGINE, mode=EngineMode.BUILD, access=AccessRouteRef("r", "d")))
PLAN = NativeCallPolicy.for_profile(EngineProfile(engine=ENGINE, mode=EngineMode.PLAN, access=AccessRouteRef("r", "d")))


@pytest.mark.parametrize(
    ("policy", "call", "decision", "code"),
    [
        (BUILD, NativeCall(NativeTool.SHELL, (), "Bash"), "allow", "shell"),
        (BUILD, NativeCall(NativeTool.EDIT, ("/workspace/src/app.py",), "Write"), "allow", "edit"),
        (BUILD, NativeCall(NativeTool.READ, (), "Read"), "allow", "read"),
        (PLAN, NativeCall(NativeTool.READ, (), "Read"), "allow", "read"),
        # Plan and review read and report: no commands, no edits, whatever the engine was started with.
        (PLAN, NativeCall(NativeTool.SHELL, (), "Bash"), "deny", NOT_PERMITTED),
        (PLAN, NativeCall(NativeTool.EDIT, ("/workspace/a.md",), "Edit"), "deny", NOT_PERMITTED),
        # The engine never reaches the web itself, in any mode.
        (BUILD, NativeCall(NativeTool.WEB, (), "WebFetch"), "deny", NOT_PERMITTED),
        # Edits stay in the workspace: outside it, through .., or by a path whose folder the check cannot see.
        (BUILD, NativeCall(NativeTool.EDIT, ("/tmp/notes.md",), "Write"), "deny", OUTSIDE_WORKSPACE),
        (BUILD, NativeCall(NativeTool.EDIT, ("/workspace/../state/claude/harness-settings.json",), "Edit"), "deny", OUTSIDE_WORKSPACE),
        (BUILD, NativeCall(NativeTool.EDIT, ("/pipeshub/session/ph.py",), "Write"), "deny", OUTSIDE_WORKSPACE),
        (BUILD, NativeCall(NativeTool.EDIT, ("notes.md",), "Write"), "deny", OUTSIDE_WORKSPACE),
        (BUILD, NativeCall(NativeTool.EDIT, ("/workspace/ok.md", "/etc/passwd"), "MultiEdit"), "deny", OUTSIDE_WORKSPACE),
        # An engine's own configuration in the workspace is never written (plug-ins, hooks, settings).
        (BUILD, NativeCall(NativeTool.EDIT, ("/workspace/.claude/settings.json",), "Write"), "deny", ENGINE_CONFIG),
        (BUILD, NativeCall(NativeTool.EDIT, ("/workspace/repo/.codex/hooks.json",), "Write"), "deny", ENGINE_CONFIG),
        (BUILD, NativeCall(NativeTool.EDIT, ("/workspace/opencode.json",), "Write"), "deny", ENGINE_CONFIG),
    ],
)
def test_ga14_c20_26_a_native_call_is_judged_by_the_runs_profile(
    policy: NativeCallPolicy, call: NativeCall, decision: str, code: str
) -> None:
    verdict = policy.verdict(call)
    assert (verdict.decision, verdict.code) == (decision, code)
    assert bool(verdict.reason) is (decision == "deny")


def test_ga14_the_reason_names_the_call_in_plain_words() -> None:
    assert PLAN.verdict(NativeCall(NativeTool.SHELL, (), "Bash")).reason == (
        "This run may not run commands: its mode does not permit Bash."
    )
    assert BUILD.verdict(NativeCall(NativeTool.EDIT, ("/tmp/x",), "Write")).reason == (
        "/tmp/x is outside the workspace: edits stay under /workspace."
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"tool": "teleport", "name": "X", "paths": []},
        {"tool": "shell", "name": 3, "paths": []},
        {"tool": "edit", "name": "Write", "paths": []},
        {"tool": "edit", "name": "Write", "paths": [""]},
        {"tool": "edit", "name": "Write", "paths": "/workspace/a"},
    ],
)
def test_ga14_what_the_check_cannot_read_is_refused(arguments: dict[str, object]) -> None:
    assert native_call_from_wire(arguments) is None  # type: ignore[arg-type]
    assert BUILD.check(arguments).code == UNREADABLE  # type: ignore[arg-type]


def test_ga14_the_wire_form_round_trips() -> None:
    call = NativeCall(NativeTool.EDIT, ("/workspace/a.md",), "Write")
    assert native_call_from_wire(native_call_wire(call)) == call


async def test_ga14_g25_the_gateway_answers_a_policy_check_without_running_or_journaling_anything() -> None:
    world = build_endpoint(native=PLAN)
    claims = claims_for(BINDING)
    allowed = await world.endpoint.call_tool(claims, POLICY_CHECK_TOOL, {"tool": "read", "name": "Read", "paths": []})
    assert (allowed.is_error, allowed.disposition, allowed.text) == (False, "executed", "allowed")
    refused = await world.endpoint.call_tool(claims, POLICY_CHECK_TOOL, {"tool": "shell", "name": "Bash", "paths": []})
    assert (refused.is_error, refused.disposition, refused.reason_code, refused.retryable) == (
        True, "denied", NOT_PERMITTED, False,
    )
    assert refused.structured()["text"] == "This run may not run commands: its mode does not permit Bash."
    assert world.runs == [] and refused.invocation_id is None and allowed.invocation_id is None
    # Never one of the run's tools: not listed, and a granted name never shadows it.
    assert POLICY_CHECK_TOOL not in {t.name for t in await world.endpoint.list_tools(claims)}
    assert all("." not in t.name for t in GRANTED)


async def test_ga14_a_gateway_without_a_native_policy_refuses_every_check() -> None:
    world = build_endpoint()
    answer = await world.endpoint.call_tool(claims_for(BINDING), POLICY_CHECK_TOOL, {"tool": "read", "name": "Read", "paths": []})
    assert (answer.disposition, answer.reason_code) == ("denied", NO_POLICY)


def _hook(program: Path, payload: str = "{}") -> tuple[int, str]:
    done = subprocess.run(
        ["sh", "-c", policy_hook_command(str(program))], input=payload, capture_output=True, text=True, timeout=30, check=False
    )
    return done.returncode, done.stderr


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("import sys; sys.exit(0)", 0),
        ("import sys; print('refused: outside the workspace', file=sys.stderr); sys.exit(2)", HOOK_BLOCK_STATUS),
        # Both engines let a call run when its hook exits 1 or crashes: the command turns that into a refusal.
        ("import sys; sys.exit(1)", HOOK_BLOCK_STATUS),
        ("raise RuntimeError('boom')", HOOK_BLOCK_STATUS),
        ("import sys; sys.exit(77)", HOOK_BLOCK_STATUS),
    ],
)
def test_ga14_the_hook_command_fails_closed(tmp_path: Path, body: str, status: int) -> None:
    program = tmp_path / "ph.py"
    program.write_text(body + "\n")
    code, err = _hook(program)
    assert code == status
    if status == HOOK_BLOCK_STATUS:
        assert err.strip(), "the engines show the hook's stderr as the reason; it must never be empty"


def test_ga14_a_hook_whose_program_is_missing_refuses(tmp_path: Path) -> None:
    code, err = _hook(tmp_path / "missing.py")
    assert code == HOOK_BLOCK_STATUS and err.strip()
