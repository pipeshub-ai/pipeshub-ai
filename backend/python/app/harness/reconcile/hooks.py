"""The connector reads that decide an unknown effect (P2.9, R-31).

Each hook is opt-in for one tool and reads through the same toolset instance
the run's registry built (the run's credentials), calling a read-only method
of it that is not a ``@tool``: the model's tool list is unchanged.

| Tool | The write carries | The read | Consistency |
|---|---|---|---|
| ``jira__create_issue`` | the effect tag as a label | JQL ``labels = "<tag>"`` | search (index lag) |
| ``slack__send_message`` | message metadata ``pipeshub_effect`` | channel history with metadata | read-after-write |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from app.utils.env_flags import HARNESS_RECONCILE, env_flag
from harness_core.effects.reconcile import (
    LookupAbsent,
    LookupFound,
    LookupUndecidable,
    ReconcileEvidence,
    ReconcileLookup,
    ReconcileTiming,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.effects.reconcile import LookupResult, UnknownEffect
    from harness_core.effects.types import ToolEffect
    from harness_core.logs import HarnessLog
    from harness_core.tools.registry import ToolRegistry

# Jira Cloud's search index trails a create by seconds; twice that between the two negatives.
JIRA_INDEX_LAG_S = 30.0
TOOL_TIMEOUT_S = 60.0
# Slack's `ts` is the post's server time; a margin covers clock skew between us and Slack.
SLACK_HISTORY_MARGIN_S = 60.0


class JiraIssues(Protocol):
    async def find_effect_issue(self, effect: str) -> dict[str, str] | None: ...


class SlackMessages(Protocol):
    async def find_effect_message(
        self, channel: str, effect: str, oldest: float
    ) -> dict[str, str] | None: ...


@dataclass(frozen=True)
class JiraIssueLookup:
    toolset: JiraIssues
    timing: ReconcileTiming = ReconcileTiming(
        tool_timeout_s=TOOL_TIMEOUT_S, read_after_write=False, index_lag_s=JIRA_INDEX_LAG_S
    )

    async def lookup(self, effect: UnknownEffect) -> LookupResult:
        issue = await self.toolset.find_effect_issue(effect.tag)
        if issue is None:
            return LookupAbsent()
        return LookupFound(
            ReconcileEvidence({"issue": dict(issue)}, {"issueKey": issue["key"], "issueId": issue["id"]})
        )


@dataclass(frozen=True)
class SlackMessageLookup:
    toolset: SlackMessages
    timing: ReconcileTiming = ReconcileTiming(tool_timeout_s=TOOL_TIMEOUT_S, read_after_write=True)

    async def lookup(self, effect: UnknownEffect) -> LookupResult:
        if effect.arguments.get("attachment_record_ids"):
            # The post may have landed without its files; a read of the message cannot tell.
            return LookupUndecidable("attachments")
        channel = effect.arguments.get("channel")
        if not isinstance(channel, str) or not channel:
            return LookupUndecidable("channel")
        oldest = (effect.dispatched_at - timedelta(seconds=SLACK_HISTORY_MARGIN_S)).timestamp()
        message = await self.toolset.find_effect_message(channel, effect.tag, oldest)
        if message is None:
            return LookupAbsent()
        return LookupFound(
            ReconcileEvidence({"message": dict(message)}, {"channel": message["channel"], "ts": message["ts"]})
        )


# Tool name -> its hook over the toolset instance; adding a connector is one entry.
HOOKS: Mapping[str, Callable[[Any], ReconcileLookup]] = {
    "jira__create_issue": JiraIssueLookup,
    "slack__send_message": SlackMessageLookup,
}


def reconcile_enabled() -> bool:
    return env_flag(HARNESS_RECONCILE)


def build_reconcile_hooks(
    registry: ToolRegistry,
    effects: Mapping[str, ToolEffect],
    log: HarnessLog,
) -> dict[str, ReconcileLookup] | None:
    """The granted tools' hooks by tool id; ``None`` with the flag off or none granted
    (an unknown effect then goes to a person as before). The kernel's host wraps them
    in ``TimedReconciler`` with its clock."""
    if not reconcile_enabled():
        return None
    hooks: dict[str, ReconcileLookup] = {}
    for name, hook in HOOKS.items():
        effect = effects.get(name)
        if effect is None or effect.idempotent or not registry.has(name):
            continue
        toolset = getattr(registry.resolve_by_name(name), "bound_instance", None)
        if toolset is None:
            continue
        hooks[effect.tool_id] = hook(toolset)
    log.info("reconcile.hooks", tools=sorted(hooks))
    return hooks or None


__all__ = [
    "HOOKS",
    "JIRA_INDEX_LAG_S",
    "SLACK_HISTORY_MARGIN_S",
    "TOOL_TIMEOUT_S",
    "JiraIssueLookup",
    "SlackMessageLookup",
    "build_reconcile_hooks",
    "reconcile_enabled",
]
