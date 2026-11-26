"""The run's policy for an engine's own (native) tool calls, wherever it is asked (G-25, C20.26).

One engine-neutral verdict serves every place a native call is checked: the managed ``PreToolUse`` hook of the engines
that run hooks (Claude Code, Codex), which asks the Tool Gateway through ``ph policy check``; and the ACP driver's
permission bridge, which the agent asks itself. The rules are the run's profile: a native tool the run's mode does not
permit is refused; the engine never fetches from the web itself (the network is the egress proxy's); edits stay in
the workspace and never write an engine's own configuration there; a read that names its file (Codex's
``view_image``, which hands the file to the model) stays in the workspace too. Reads and in-sandbox commands the mode
permits run without asking, as all code inside the sandbox does. Defence in depth only: the outer sandbox stays the
boundary.

The wire form is what ``ph policy check`` sends as the arguments of ``POLICY_CHECK_TOOL``; anything it cannot read is
refused, so a hook that cannot say what it is checking never lets the call through.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from harness_core.engines.policy import NativeTool, expected_native_tools
from harness_core.workspace.sync.rules import is_engine_config

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.engines.types import EngineProfile

# The Tool Gateway's own name for the check. Never listed to the model, and a grant's names never hold a dot, so it
# can never be shadowed by a granted tool.
POLICY_CHECK_TOOL: Final = "pipeshub.policy_check"
WORKDIR: Final = "/workspace"
# What a native call is refused for (``NativeVerdict.code``): stable names the logs and tests read.
NOT_PERMITTED: Final = "not_permitted"
OUTSIDE_WORKSPACE: Final = "outside_workspace"
ENGINE_CONFIG: Final = "engine_config"
UNREADABLE: Final = "unreadable"
NO_POLICY: Final = "no_policy"

Decision = Literal["allow", "deny"]


@dataclass(frozen=True, slots=True)
class NativeCall:
    """One native call by what it does: the tool class, and the paths it writes (an edit's) when it says them."""

    tool: NativeTool
    paths: tuple[str, ...] = ()
    # The engine's own name for the tool (``Bash``, ``Write``...), for the reason a person reads.
    name: str = ""


@dataclass(frozen=True, slots=True)
class NativeVerdict:
    decision: Decision
    # The tool class (``shell``...) when allowed, else why it is refused.
    code: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


_MODE_TEXT = {
    NativeTool.SHELL: "run commands",
    NativeTool.EDIT: "edit files",
    NativeTool.READ: "read files",
    NativeTool.SEARCH: "search files",
    NativeTool.SKILL: "load skills",
    NativeTool.SUBAGENT: "start helpers",
}


def _inside(path: str, workdir: str) -> str | None:
    """The path relative to ``workdir`` when it is inside it, else ``None``. A relative path is refused: where it
    lands depends on a working folder the check cannot see (the hook resolves it against the engine's own)."""
    if not path.startswith("/"):
        return None
    absolute = posixpath.normpath(path)
    if absolute == workdir:
        return ""
    return absolute[len(workdir) + 1 :] if absolute.startswith(workdir + "/") else None


@dataclass(frozen=True, slots=True)
class NativeCallPolicy:
    """The native tools the run may use, and the folder its edits stay in."""

    allowed: frozenset[NativeTool]
    workdir: str = WORKDIR

    @staticmethod
    def for_profile(profile: EngineProfile, *, skills: bool = True) -> NativeCallPolicy:
        return NativeCallPolicy(expected_native_tools(profile, skills=skills))

    def verdict(self, call: NativeCall) -> NativeVerdict:
        named = call.name or call.tool.value
        if call.tool is NativeTool.WEB:
            return _deny(
                NOT_PERMITTED,
                f"{named} is not available in this run: the engine does not reach the web itself. "
                "Use the run's PipesHub tools instead (ph tools search web).",
            )
        if call.tool not in self.allowed:
            what = _MODE_TEXT.get(call.tool, f"use {named}")
            return _deny(NOT_PERMITTED, f"This run may not {what}: its mode does not permit {named}.")
        if call.tool is NativeTool.EDIT:
            for path in call.paths:
                relative = _inside(path, self.workdir)
                if relative is None:
                    return _deny(OUTSIDE_WORKSPACE, f"{path} is outside the workspace: edits stay under {self.workdir}.")
                if relative and is_engine_config(relative):
                    return _deny(ENGINE_CONFIG, f"{path} is an engine's configuration, which a run never writes.")
        if call.tool is NativeTool.READ:
            for path in call.paths:
                if _inside(path, self.workdir) is None:
                    return _deny(
                        OUTSIDE_WORKSPACE, f"{path} is outside the workspace: {named} opens only files under {self.workdir}."
                    )
        return NativeVerdict("allow", call.tool.value, "")

    def check(self, arguments: Mapping[str, JsonValue]) -> NativeVerdict:
        """The verdict on ``POLICY_CHECK_TOOL``'s arguments; arguments it cannot read are refused."""
        call = native_call_from_wire(arguments)
        if call is None:
            return _deny(UNREADABLE, "PipesHub could not tell what this call does, so it is refused.")
        return self.verdict(call)


def _deny(code: str, reason: str) -> NativeVerdict:
    return NativeVerdict("deny", code, reason)


def no_policy_verdict() -> NativeVerdict:
    """What a gateway without a native policy answers: refused, as a hook that gets no answer refuses."""
    return _deny(NO_POLICY, "This run has no policy for the engine's own tools here, so the call is refused.")


def native_call_from_wire(arguments: Mapping[str, JsonValue]) -> NativeCall | None:
    """``{"tool": "<NativeTool>", "name": "<engine's name>", "paths": [...]}``; ``None`` when it is not that."""
    tool = arguments.get("tool")
    name = arguments.get("name", "")
    paths = arguments.get("paths", [])
    if not isinstance(tool, str) or not isinstance(name, str) or not isinstance(paths, list):
        return None
    if not all(isinstance(p, str) and p for p in paths):
        return None
    try:
        kind = NativeTool(tool)
    except ValueError:
        return None
    if kind is NativeTool.EDIT and not paths:
        return None
    return NativeCall(kind, tuple(p for p in paths if isinstance(p, str)), name)


# How long the hook waits for its program, in seconds; ``ph policy check`` gives up well before (``PH_POLICY_TIMEOUT_S``),
# so a slow gateway ends in the program's own refusal rather than in the engine's timeout, which lets the call run.
HOOK_TIMEOUT_S: Final = 30
# The exit status both engines read as "block this call", with the reason on stderr (Claude Code, Codex 0.160).
HOOK_BLOCK_STATUS: Final = 2


def policy_hook_command(program: str) -> str:
    """The managed ``PreToolUse`` hook's shell command: the run's ``ph policy check`` on the hook's input.

    Both engines let a call run when its hook fails any other way than exit 2 (a missing interpreter, a crash, exit 1:
    verified on Codex 0.160.0, documented for Claude Code), so every other status becomes a refusal here: the hook
    fails closed."""
    return (
        f'/usr/bin/env python3 {program} policy check --hook || {{ s=$?; [ "$s" -eq {HOOK_BLOCK_STATUS} ] || '
        f'echo "PipesHub could not check this call against the run\'s policy (status $s), so it is refused." >&2; '
        f"exit {HOOK_BLOCK_STATUS}; }}"
    )


def native_call_wire(call: NativeCall) -> dict[str, JsonValue]:
    return {"tool": call.tool.value, "name": call.name, "paths": list(call.paths)}


__all__ = [
    "ENGINE_CONFIG",
    "HOOK_BLOCK_STATUS",
    "HOOK_TIMEOUT_S",
    "NOT_PERMITTED",
    "NO_POLICY",
    "OUTSIDE_WORKSPACE",
    "POLICY_CHECK_TOOL",
    "UNREADABLE",
    "WORKDIR",
    "NativeCall",
    "NativeCallPolicy",
    "NativeVerdict",
    "native_call_from_wire",
    "native_call_wire",
    "no_policy_verdict",
    "policy_hook_command",
]
