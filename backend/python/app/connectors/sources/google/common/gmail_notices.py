"""The notifications a Gmail connector sends, in words a person can act on.

One job: turn what went wrong in a run into at most a few plain notices. Only problems a person can fix are
sent (sign-in revoked, delegation or key not accepted, Gmail turned off for some people); a failure the next
sync will retry by itself is not, and the run's status already shows it. No notice carries error codes, ids or
addresses of Google's services. Repeats of the same notice are held back by `BaseConnector.notify`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.connectors.sources.google.common.gmail_errors import GmailFailure
from app.services.notification.types import NotificationSeverity, NotificationType

if TYPE_CHECKING:
    from app.connectors.sources.google.common.gmail_sync_health import GmailSyncHealth

Notify = Callable[..., Awaitable[None]]


class GmailNotices:
    def __init__(self, notify: Notify, title: Callable[[str], str], *, workspace: bool) -> None:
        self._notify = notify
        self._title = title
        self._workspace = workspace

    async def report(self, health: GmailSyncHealth) -> None:
        attention = health.needs_attention()
        # The same failure names come from both connectors. A personal access_denied is classified as delegation
        # refused, and a Workspace 401 as reconnect, so each notice is sent only for the connector that can act on it.
        if not self._workspace and (
            GmailFailure.RECONNECT in attention
            or GmailFailure.MISSING_SCOPE in attention
            or GmailFailure.DELEGATION_REFUSED in attention
        ):
            await self._send(NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR,
                             "Reconnect your Google account",
                             "Gmail stopped accepting this connection, so new mail is not being synced. Open the "
                             "connector and sign in to Google again.")
            return
        if self._workspace and (GmailFailure.KEY_REJECTED in attention or GmailFailure.RECONNECT in attention):
            await self._send(NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR,
                             "Google did not accept the service account key",
                             "Mail is not being synced. The key may have been deleted or disabled in Google Cloud. "
                             "Upload a current service account key in the connector settings.")
            return
        if self._workspace and (
            GmailFailure.DELEGATION_REFUSED in attention or GmailFailure.MISSING_SCOPE in attention
        ):
            await self._send(NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR,
                             "Google Workspace refused access to mail",
                             "Mail is not being synced. In the Google Admin console, check that domain-wide "
                             "delegation for this service account includes Gmail read-only access, then sync again.")
            return
        disabled = health.mailboxes_skipped(GmailFailure.MAIL_DISABLED)
        if self._workspace and disabled:
            count = len(disabled)
            people = "1 person" if count == 1 else f"{count} people"
            await self._send(NotificationType.CONNECTOR_WARNING, NotificationSeverity.WARNING,
                             "Some mailboxes were not synced",
                             f"Gmail is turned off for {people} in your Google Workspace, so their mail is not "
                             "synced. Turn Gmail on for them in the Google Admin console if their mail should be "
                             "searchable.")

    async def _send(self, kind: NotificationType, severity: NotificationSeverity, event: str, message: str) -> None:
        await self._notify(type=kind, severity=severity, title=self._title(event), message=message)
