"""`ITaskNotifier` -- outbound notifications about task lifecycle events
(run failed, prerequisite missing, awaiting input, auto-disabled). Kept
separate from `ITaskRunStore` because "persist the run" and "tell a human"
are different responsibilities with different failure modes: a notifier
timeout must never block or fail a run's own state transition.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field


class TaskNotificationKind(str, Enum):
    RUN_STARTED = "run_started"
    RUN_FAILED = "run_failed"
    RUN_SUCCEEDED = "run_succeeded"
    AWAITING_INPUT = "awaiting_input"
    PREREQUISITE_MISSING = "prerequisite_missing"
    TASK_AUTO_DISABLED = "task_auto_disabled"
    TASK_DLQ = "task_dlq"


class TaskNotification(BaseModel):
    kind: TaskNotificationKind
    org_id: str
    user_id: str
    task_id: str
    run_id: str | None = None
    title: str
    message: str
    metadata: dict = Field(default_factory=dict)
    redirect_link: str | None = None
    workflow_id: str | None = None
    conversation_id: str | None = None
    """Originating conversation. When set, redirect_link points to
    /chat?conversationId=<id> rather than the workflows page."""
    run_status: str | None = None
    """Lowercase run status string (e.g. 'succeeded'), forwarded to the
    Node socket event so the frontend's status comparisons work correctly."""
    trigger_kind: str | None = None
    output_summary: str | None = None
    workflow_name: str | None = None
    """Human-readable workflow title, forwarded to the socket event so the
    real-time RunResultCard in the chat can show it without a page reload."""
    is_dry_run: bool = False
    """Deliver live only. Dry runs publish run-lifecycle notifications purely
    so the in-chat dry-run card can resolve; the consumer must not persist
    them to anyone's notification inbox."""


class ITaskNotifier(ABC):
    @abstractmethod
    async def notify(self, notification: TaskNotification) -> None:
        """Best-effort. Implementations must not raise on delivery failure
        -- log and swallow, since a notification failure must never fail
        the run/trigger state transition that triggered it."""
        ...
