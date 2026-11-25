"""An engine's task list as one normalized ``PlanUpdated`` (UX5): Codex's plan, Claude Code's and opencode's todo tools.

Each engine names the fields and statuses its own way; the list always replaces the previous one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.events import PlanUpdated
from harness_core.kernel.task_ledger import MAX_TASKS, plan_items

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.engines.events import EngineEventMeta

MAX_ITEMS = MAX_TASKS


def plan_updated(meta: EngineEventMeta, items: JsonValue, title_keys: tuple[str, ...]) -> PlanUpdated | None:
    """``None`` when ``items`` is not a list; an item without a title is skipped, an unknown status is pending."""
    plan = plan_items(items, title_keys)
    return PlanUpdated(meta, tuple(plan)) if plan is not None else None


__all__ = ["MAX_ITEMS", "plan_updated"]
