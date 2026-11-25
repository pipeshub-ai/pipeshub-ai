"""``ph publish`` at the Tool Gateway (C3.12, design-plan §4.6 ``artifact_publish``): a deliverable published now.

An engine's deliverables are the files it writes under its outputs folder (``out/``, C3.29): each save publishes the
new ones as drafts, and the turn's end makes them final. ``ph publish`` puts a file there and calls this tool, which
the engine controller's endpoint answers itself: it saves the workspace (the autosave every gateway call starts with,
so the file is published as a draft by the time it answers) and reports what became of the file. Nothing is published
any other way, so a file ``ph publish`` sends and a file written to ``out/`` look the same in the chat.

The tool is not in the engine's tool list (its MCP client would offer the model a second way to do what writing to
``out/`` does); ``ph`` names it, as it names the policy check.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from harness_core.gateway.types import McpResult
from harness_core.workspace.safe_names import NOT_PATH, UnsafeName, check_name_path

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

ARTIFACT_PUBLISH_TOOL: Final = "pipeshub.artifact_publish"
INVALID_OUTPUT_PATH: Final = "invalid_output_path"
OUTPUT_MISSING: Final = "output_missing"
PUBLISH_UNAVAILABLE: Final = "publish_unavailable"
MAX_OUTPUT_PATH_CHARS: Final = 512


def checked_output_path(arguments: Mapping[str, JsonValue]) -> str | UnsafeName:
    """The deliverable's path under the outputs folder in NFC, or why it is refused: the one shared check of a name
    that becomes a file (SEC-FS-02, ``safe_names.check_name_path``), so ``ph publish``, this call and a save that
    publishes the outputs folder take and refuse the same names."""
    raw = arguments.get("path")
    if not isinstance(raw, str) or len(raw) > MAX_OUTPUT_PATH_CHARS:
        return UnsafeName(NOT_PATH, "The path is not a short text")
    try:
        return check_name_path(raw)
    except UnsafeName as refused:
        return refused


def output_path(arguments: Mapping[str, JsonValue]) -> str | None:
    """``checked_output_path`` without the reason: ``None`` for a refused path."""
    checked = checked_output_path(arguments)
    return None if isinstance(checked, UnsafeName) else checked


def invalid_path(refused: UnsafeName | None = None) -> McpResult:
    why = f" {refused}." if refused is not None else ""
    return McpResult(
        "Name a file under the outputs folder by its relative path, like report.md or charts/q3.png." + why,
        is_error=True,
        disposition="refused",
        retryable=True,
        reason_code=INVALID_OUTPUT_PATH,
    )


def unavailable() -> McpResult:
    return McpResult(
        "Publishing is not available in this turn; files written to the outputs folder are still published when "
        "the turn saves.",
        is_error=True,
        disposition="refused",
        retryable=False,
        reason_code=PUBLISH_UNAVAILABLE,
    )


def published(path: str, artifact_id: str, version: int, lifecycle: str) -> McpResult:
    answer = {"path": path, "artifactId": artifact_id, "version": version, "lifecycle": lifecycle}
    return McpResult(json.dumps(answer), is_error=False, disposition="executed", retryable=False)


def unchanged(path: str) -> McpResult:
    return McpResult(
        json.dumps({"path": path, "unchanged": True}), is_error=False, disposition="executed", retryable=False
    )


def refused(path: str, reason_code: str) -> McpResult:
    return McpResult(
        f"{path} was not published ({reason_code.replace('_', ' ')}).",
        is_error=True,
        disposition="refused",
        retryable=False,
        reason_code=reason_code,
    )


def missing(path: str) -> McpResult:
    return McpResult(
        f"There is no file {path} in the outputs folder to publish.",
        is_error=True,
        disposition="refused",
        retryable=True,
        reason_code=OUTPUT_MISSING,
    )


__all__ = [
    "ARTIFACT_PUBLISH_TOOL",
    "INVALID_OUTPUT_PATH",
    "OUTPUT_MISSING",
    "PUBLISH_UNAVAILABLE",
    "checked_output_path",
    "invalid_path",
    "missing",
    "output_path",
    "published",
    "refused",
    "unavailable",
    "unchanged",
]
