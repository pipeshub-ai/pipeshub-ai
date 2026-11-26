"""Telling the people who own a Dropbox connector what a run could not do.

One job: turn a run's `SyncHealth` into notifications. One notice per kind of problem, each naming the connector
instance; repeats of the same notice are held back by the base connector's back-off, so a connector that stays
broken does not notify on every scheduled run. A clean run sends nothing.

  - Dropbox refused the credentials (revoked or expired refresh token, a missing permission): someone must reconnect.
  - Part of Dropbox could not be read: the run is marked failed and nothing it did not read is removed.
  - Dropbox reset folder cursors: those folders were listed again from the start.
  - The team activity log cannot be read: sharing and group changes then only arrive with a full sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.sources.dropbox.errors import missing_scope
from app.services.notification.types import NotificationSeverity, NotificationType

if TYPE_CHECKING:
    from app.connectors.core.base.connector.connector_service import BaseConnector
    from app.connectors.sources.dropbox.sync_health import SyncHealth


class SyncNotices:
    def __init__(self, connector: BaseConnector) -> None:
        self.connector = connector

    async def report(self, health: SyncHealth, *, activity_log_missing: str | None = None) -> None:
        c = self.connector
        if health.auth_error is not None:
            scope = missing_scope(health.auth_error)
            if scope:
                message = (f"Dropbox refused a request because the connection lacks the '{scope}' permission. "
                           "Add it to the Dropbox app and reconnect the connector.")
            else:
                message = ("Dropbox no longer accepts this connector's sign-in (it was revoked or has expired). "
                           "Reconnect the connector to resume syncing.")
            await c.notify(type=NotificationType.CONNECTOR_AUTH_ERROR, severity=NotificationSeverity.ERROR,
                           title=c._notification_title("Dropbox sign-in failed"), message=message)
        elif health.problems:
            await c.notify(type=NotificationType.CONNECTOR_SYNC_ERROR, severity=NotificationSeverity.WARNING,
                           title=c._notification_title("Part of Dropbox could not be synced"),
                           message=(f"{len(health.problems)} part(s) of Dropbox could not be read, so this sync did "
                                    "not finish, and nothing was removed from them. It will be tried again on the next sync. "
                                    f"First: {health.problems[0][:300]}"))
        if health.relisted:
            await c.notify(type=NotificationType.CONNECTOR_INFO, severity=NotificationSeverity.INFO,
                           title=c._notification_title("Folders synced again from the start"),
                           message=(f"Dropbox reset the change tracking of {len(health.relisted)} folder(s), so they "
                                    "were read again in full."))
        if activity_log_missing:
            await c.notify(type=NotificationType.CONNECTOR_WARNING, severity=NotificationSeverity.WARNING,
                           title=c._notification_title("Dropbox activity log unavailable"),
                           message=("The team's activity log could not be read, so sharing and group changes are "
                                    f"picked up only by a full sync. Dropbox said: {activity_log_missing[:200]}"))
