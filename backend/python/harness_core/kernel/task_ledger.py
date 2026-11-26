"""A run's plan as core ``task.*`` events (UXR-B1): one ledger for every executor.

Engines, deep mode and the loop's ``write_todos`` each hand it their whole list; it journals only what changed, and a
step a newer list dropped as ``cancelled``. Its state rides the loop-state slot ``plan.tasks`` across a re-claim.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Final, cast

from harness_core.engines.events import PlanItem
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy, ToolEffect
    from harness_core.engines.events import PlanStatus

PLAN_SLOT: Final = "plan.tasks"
TASK_CREATED: Final = "task.created"
TASK_UPDATED: Final = "task.updated"
MAX_TASKS: Final = 50
MAX_TITLE_CHARS: Final = 300
_STATUSES: Final[dict[str, PlanStatus]] = {
    "pending": "pending",
    "in_progress": "in_progress",
    "inprogress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _valid(task_id: str | None) -> bool:
    return task_id is not None and _ID.fullmatch(task_id) is not None


class TaskLedger:
    """``asks_first``: whether the claim's journal accepts ``asksFirst`` (``task_asks_first``); without it the field
    is never written."""

    def __init__(self, *, asks_first: bool = True) -> None:
        self._asks_first = asks_first
        self._tasks: dict[str, dict[str, JsonValue]] = {}

    def replace(self, items: Sequence[PlanItem]) -> list[NewEvent]:
        """The list as it now stands, replacing the last one."""
        listed: dict[str, dict[str, JsonValue]] = {}
        for n, item in enumerate(items[:MAX_TASKS], start=1):
            title = item.title.strip()[:MAX_TITLE_CHARS]
            if not title:
                continue
            task_id = item.task_id if _valid(item.task_id) and item.task_id not in listed else f"task-{n}"
            listed[cast("str", task_id)] = self._data(cast("str", task_id), title, item)
        events = [self._event(task_id, data) for task_id, data in listed.items()]
        for task_id, before in self._tasks.items():
            if task_id not in listed:
                listed[task_id] = {**before, "status": "cancelled"}
                events.append(self._event(task_id, listed[task_id]))
        self._tasks = listed
        return [event for event in events if event is not None]

    def move(self, statuses: Mapping[str, PlanStatus]) -> list[NewEvent]:
        """Steps whose status changed without a new list (deep mode's dispatch and join)."""
        events: list[NewEvent | None] = []
        for task_id, status in statuses.items():
            before = self._tasks.get(task_id)
            if before is not None:
                events.append(self._event(task_id, {**before, "status": status}))
                self._tasks[task_id] = {**before, "status": status}
        return [event for event in events if event is not None]

    def ids(self) -> list[str]:
        return list(self._tasks)

    def restore(self, slots: Mapping[str, JsonValue]) -> None:
        raw = slots.get(PLAN_SLOT)
        if isinstance(raw, list) and not self._tasks:
            self._tasks = {
                str(task["taskId"]): dict(task) for task in raw if isinstance(task, dict) and _valid(str(task.get("taskId")))
            }

    def slot(self) -> dict[str, JsonValue]:
        return {PLAN_SLOT: cast("JsonValue", list(self._tasks.values()))}

    def _data(self, task_id: str, title: str, item: PlanItem) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {
            "taskId": task_id,
            "title": title,
            "status": item.status,
            "deps": [dep for dep in _unique(item.deps) if _valid(dep)],
        }
        if self._asks_first and item.asks_first is not None:
            data["asksFirst"] = item.asks_first
        return data

    def _event(self, task_id: str, data: dict[str, JsonValue]) -> NewEvent | None:
        before = self._tasks.get(task_id)
        if before == data:
            return None
        return NewEvent(TASK_UPDATED if before is not None else TASK_CREATED, data, critical=False)


def plan_items(
    raw: JsonValue,
    title_keys: tuple[str, ...],
    *,
    asks: Callable[[list[str]], bool] | None = None,
) -> list[PlanItem] | None:
    """A task list as an engine's todo tool or ``write_todos`` states it; ``None`` when it is not a list. An item
    without a title is skipped, an unknown status is pending; ``asks`` reads an item's ``tools`` (none named: not
    known)."""
    if not isinstance(raw, list):
        return None
    plan: list[PlanItem] = []
    for item in raw[:MAX_TASKS]:
        if not isinstance(item, dict):
            continue
        title = next((v for k in title_keys if isinstance(v := item.get(k), str) and v.strip()), None)
        if title is None:
            continue
        status = item.get("status")
        key = status.replace(" ", "").lower() if isinstance(status, str) else ""
        tools = item.get("tools")
        names = [t for t in tools if isinstance(t, str)] if isinstance(tools, list) else []
        plan.append(
            PlanItem(
                title.strip()[:MAX_TITLE_CHARS],
                _STATUSES.get(key, "pending"),
                asks_first=asks(names) if asks is not None and names else None,
            )
        )
    return plan


def asks_first(tool_names: Iterable[str], effects: Mapping[str, ToolEffect], policy: ApprovalPolicy) -> bool:
    """Whether a step naming these tools will ask a person first, by the run's static rules: a name the run does not
    have is refused, not asked; a flow rule decides per call and is never predicted."""
    from harness_core.kernel.core.messages import ToolCall

    return any(
        policy(ToolCall(id="plan", name=name), effects[name]) == "ask" for name in tool_names if name in effects
    )


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = ["MAX_TASKS", "MAX_TITLE_CHARS", "PLAN_SLOT", "TaskLedger", "asks_first", "plan_items"]
