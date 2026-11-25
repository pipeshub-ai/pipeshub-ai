"""Inputs staged at a turn's start if small; a large one reaches the sandbox on its first read (C5.01, J-26).

A file under ``in/`` larger than ``STAGE_AT_START_MAX_BYTES`` stays in the workspace (pinned, saved, versioned as
any input) but is not copied into the sandbox when the turn starts: the sync hides it (``ON_FIRST_READ``), the
engine's task names it with its size and how to get it, and ``ph inputs get <name>`` (the session's ``ph``) asks the
controller for it (``INPUT_FETCH_TOOL``, no tool of the grant: like the policy check, the gateway's own). It writes
it as the workspace holds it, so the next save finds nothing changed. A turn that never reads it never pays to copy
it, nor to hash it back at every save.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from harness_core.engines.types import INPUTS_DIR
from harness_core.gateway.types import McpResult
from harness_core.workspace.safe_names import safe_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.workspace.types import WorkspaceFile

# The gateway's own tool ``ph inputs get`` calls (harness_engines/pipeshub/ph.py keeps the same name).
INPUT_FETCH_TOOL: Final = "pipeshub.input_fetch"
# Inputs up to this size are copied in when the turn starts: a document or a table; a larger one (a scanned PDF, a
# data export) on its first read.
STAGE_AT_START_MAX_BYTES: Final = 8 * 1024 * 1024
_MOUNTED = "/pipeshub/inputs/"


def deferred_inputs(files: Mapping[str, WorkspaceFile], max_bytes: int) -> frozenset[str]:
    """The workspace paths a sandbox gets only on first read: files under ``in/`` larger than ``max_bytes``."""
    return frozenset(
        path
        for path, entry in files.items()
        if path.startswith(f"{INPUTS_DIR}/") and not entry.is_symlink and entry.content.size_bytes > max_bytes
    )


def input_path(arguments: Mapping[str, JsonValue]) -> str | None:
    """The workspace path an ``INPUT_FETCH_TOOL`` call names: ``name`` as the task showed it (``report.pdf``,
    ``in/report.pdf`` or ``/pipeshub/inputs/report.pdf``); ``None`` when it names none."""
    raw = arguments.get("name")
    if not isinstance(raw, str) or not raw.strip():
        return None
    name = raw.strip().removeprefix(_MOUNTED).removeprefix("./").removeprefix(f"{INPUTS_DIR}/")
    # SEC-FS-02: one plain file name, or nothing; a lookup never sees ``..``, an encoded separator or a drive.
    return f"{INPUTS_DIR}/{name}" if safe_name(name) else None


def fetched(path: str, size_bytes: int, *, staged: bool) -> McpResult:
    """The answer when the input is in the sandbox: its path under the workspace and its size."""
    body = {"path": path, "bytes": size_bytes, "staged": staged}
    return McpResult(json.dumps(body), is_error=False, disposition="executed", retryable=False)


def not_waiting(path: str | None, waiting: Iterable[str]) -> McpResult:
    """The refusal for a name that is no input waiting for its first read."""
    names = sorted(p.removeprefix(f"{INPUTS_DIR}/") for p in waiting)
    listed = ", ".join(names) if names else "none"
    shown = path.removeprefix(f"{INPUTS_DIR}/") if path else "(no name)"
    text = f"{shown} is no input waiting to be read. Inputs still to fetch: {listed}."
    return McpResult(text, is_error=True, disposition="refused", retryable=False, reason_code="input_not_waiting")


def not_placed(path: str) -> McpResult:
    """The refusal when the input's folder is no folder of the workspace (a link out of it): nothing was written."""
    shown = path.removeprefix(f"{INPUTS_DIR}/")
    text = f"{shown} was not placed: {INPUTS_DIR}/ is not a folder inside the workspace. Make it a folder again and retry."
    return McpResult(text, is_error=True, disposition="refused", retryable=False, reason_code="input_folder_outside")


# The turn's own ``ph`` (the session folder's copy): it has ``inputs get`` on every image, whatever ``ph`` the image
# baked in.
SESSION_PH: Final = "python3 /pipeshub/session/ph.py"


def on_read_note(name: str) -> str:
    """How a task names an input it gets on first read."""
    return f"not copied in yet (large): run `{SESSION_PH} inputs get {name}` once before reading it"


__all__ = [
    "INPUT_FETCH_TOOL",
    "SESSION_PH",
    "STAGE_AT_START_MAX_BYTES",
    "deferred_inputs",
    "fetched",
    "input_path",
    "not_placed",
    "not_waiting",
    "on_read_note",
]
