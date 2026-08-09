"""`MessagingNotifier`: `ITaskNotifier` over the existing
`NotificationService` (`app/services/notification/notification_service.py`)
-- deliberately NOT a new publisher over `MessagingFactory` directly.
`NotificationService` already builds the exact `INotification`-shaped
payload the Node.js side renders in-app, and already swallows broker
errors after logging, which is precisely the "must not raise" contract
`ITaskNotifier.notify()` requires. Reusing it here means task notifications
show up in the same notification center as connector errors, with zero new
frontend work.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.notification.types import (
    NotificationOrigin,
    NotificationSeverity,
    NotificationType,
)
from app.services.tasks.interface.notifier import (
    ITaskNotifier,
    TaskNotification,
    TaskNotificationKind,
)

if TYPE_CHECKING:
    from app.services.notification.notification_service import NotificationService

_KIND_TO_TYPE: dict[TaskNotificationKind, NotificationType] = {
    # RUN_STARTED/FAILED/SUCCEEDED/AWAITING_INPUT map to the WORKFLOW_* types
    # (not TASK_*) because the Node side's `WORKFLOW_RUN_TYPES` set (see
    # `notification.consumer.ts`) special-cases exactly those values to also
    # push a live `workflowRunUpdate` socket event -- mapping to TASK_* here
    # would silently drop that live update for every run instead of just the
    # first one (see plan bug C3).
    TaskNotificationKind.RUN_STARTED: NotificationType.WORKFLOW_RUN_STARTED,
    TaskNotificationKind.RUN_FAILED: NotificationType.WORKFLOW_RUN_FAILED,
    TaskNotificationKind.RUN_SUCCEEDED: NotificationType.WORKFLOW_RUN_SUCCEEDED,
    TaskNotificationKind.AWAITING_INPUT: NotificationType.WORKFLOW_AWAITING_APPROVAL,
    TaskNotificationKind.PREREQUISITE_MISSING: NotificationType.TASK_PREREQUISITE_MISSING,
    TaskNotificationKind.TASK_AUTO_DISABLED: NotificationType.TASK_AUTO_DISABLED,
    TaskNotificationKind.TASK_DLQ: NotificationType.TASK_DLQ,
}

_KIND_TO_SEVERITY: dict[TaskNotificationKind, NotificationSeverity] = {
    TaskNotificationKind.RUN_STARTED: NotificationSeverity.INFO,
    TaskNotificationKind.RUN_FAILED: NotificationSeverity.ERROR,
    TaskNotificationKind.RUN_SUCCEEDED: NotificationSeverity.SUCCESS,
    TaskNotificationKind.AWAITING_INPUT: NotificationSeverity.INFO,
    TaskNotificationKind.PREREQUISITE_MISSING: NotificationSeverity.WARNING,
    TaskNotificationKind.TASK_AUTO_DISABLED: NotificationSeverity.CRITICAL,
    TaskNotificationKind.TASK_DLQ: NotificationSeverity.CRITICAL,
}


class MessagingNotifier(ITaskNotifier):
    def __init__(self, notification_service: NotificationService) -> None:
        self._notifications = notification_service

    async def notify(self, notification: TaskNotification) -> None:
        payload: dict[str, Any] = dict(notification.metadata)
        payload["taskId"] = notification.task_id
        if notification.run_id:
            payload["runId"] = notification.run_id
        if notification.workflow_id is not None:
            payload["workflowId"] = notification.workflow_id
        if notification.conversation_id is not None:
            payload["conversationId"] = notification.conversation_id
        if notification.run_status is not None:
            # Lowercase run status so the frontend's comparisons (against
            # 'succeeded' | 'failed' | 'running') work correctly.  The Node
            # consumer's fallback is `event.type` which yields an uppercase
            # type string like 'WORKFLOW_RUN_SUCCEEDED'.
            payload["status"] = notification.run_status
        if notification.trigger_kind is not None:
            payload["triggerKind"] = notification.trigger_kind
        if notification.output_summary is not None:
            payload["outputSummary"] = notification.output_summary
        if notification.workflow_name is not None:
            payload["workflowName"] = notification.workflow_name
        # redirect_link in payload for workflowRunUpdate socket consumers.
        if notification.redirect_link is not None:
            payload["redirectLink"] = notification.redirect_link
        # Tells the Node consumer to emit the live socket update without
        # storing an inbox row -- a dry run is a preview, not an event
        # anybody should be notified about after the fact.
        if notification.is_dry_run:
            payload["isDryRun"] = True

        # NotificationService already logs-and-swallows broker failures, so
        # this call cannot raise in a way that would fail the caller's own
        # run/trigger state transition -- see ITaskNotifier.notify contract.
        await self._notifications.publish_notification(
            org_id=notification.org_id,
            origin=NotificationOrigin.AI,
            type=_KIND_TO_TYPE[notification.kind],
            severity=_KIND_TO_SEVERITY[notification.kind],
            title=notification.title,
            message=notification.message,
            # Pass redirect_link as a top-level field so the stored Mongo
            # document's redirectLink is non-null -- that is what
            # notification-row.tsx reads to decide whether the title is a
            # clickable link.  Previously this was only written into payload,
            # leaving the top-level field null and making notifications
            # non-clickable.
            redirect_link=notification.redirect_link,
            payload=payload,
            recipient_user_ids=[notification.user_id],
        )
