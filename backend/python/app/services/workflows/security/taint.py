"""Taint tracking for workflow tool calls.

Tracks whether a workflow has read untrusted external content (connector data,
search results). Once tainted, a *destructive* tool call is blocked until the
workflow gets explicit human approval via `ctx.request_approval()`.

This defends against prompt injection: malicious text in a Jira ticket or Slack
message must not be able to steer the workflow into deleting or overwriting
data.

The gate deliberately stops at `destructive` rather than covering every write.
A workflow is reviewed, committed Python: the sequence of calls is fixed before
the run, so injected content can influence a write's *arguments* but not which
tools run. Blocking ordinary writes would break the shape most workflows have
("read issues, post a summary") and, on a scheduled run with nobody watching,
would park it in AWAITING_INPUT forever. LLM-mediated steps are the real
exposure and are gated separately, at their own capabilities.

Classification is supplied by the caller (the broker, which can read tool tags)
rather than hardcoded here, so adding a connector does not require editing this
module and no naming convention has to be kept in sync.

Approval is implicit in resumption: `ctx.request_approval()` suspends the run,
and the replayed execution reads its prior results from the journal instead of
re-calling the read tools, so the resumed run starts untainted.
"""
from __future__ import annotations

__all__ = ["TaintState"]


class TaintState:
    """Per-run taint tracking state."""

    def __init__(self) -> None:
        self._taint_sources: list[str] = []

    def mark_tainted(self, source_tool: str) -> None:
        if source_tool not in self._taint_sources:
            self._taint_sources.append(source_tool)

    @property
    def is_tainted(self) -> bool:
        return bool(self._taint_sources)

    @property
    def taint_sources(self) -> list[str]:
        return list(self._taint_sources)

    def check_tool_call(self, tool_name: str, *, is_destructive: bool) -> dict | None:
        """Returns an error dict if the call should be blocked, None if OK."""
        if not is_destructive or not self.is_tainted:
            return None
        return {
            "code": "TAINT_BLOCKED",
            "tool_name": tool_name,
            "taint_sources": self._taint_sources,
            "fix_hint": (
                f"This workflow read external content via {self._taint_sources} and is now "
                f"calling the destructive tool '{tool_name}'. Call ctx.request_approval() first "
                "so a human confirms the action before it is performed."
            ),
        }

    def after_tool_result(self, tool_name: str, *, is_taint_source: bool) -> None:
        """Update taint state based on the tool that just ran."""
        if is_taint_source:
            self.mark_tainted(tool_name)
