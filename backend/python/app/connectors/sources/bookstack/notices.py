"""The notifications a BookStack connector sends, in words a person can act on.

One job: turn what went wrong in a run into at most one plain notice. Only problems a person can fix are sent:
BookStack refused the API token, or the token's user lacks a permission the connector needs. An outage or a rate
limit is not: the next sync retries by itself and the run's status shows the failure. No notice carries error
codes, ids or addresses. Repeats of the same notice are held back by ``BaseConnector.notify``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.services.notification.types import NotificationSeverity, NotificationType

if TYPE_CHECKING:
    from app.connectors.sources.bookstack.sync_health import BookStackSyncHealth

Notify = Callable[..., Awaitable[None]]

# BookStack's role permission names, as its role editor shows them.
PERMISSION_NAMES = {
    "users-manage": "Manage users",
    "user-roles-manage": "Manage roles & role permissions",
    "settings-manage": "Manage app settings",
    "restrictions-manage-all": "Manage all book, chapter & page permissions",
    "content-export": "Export content",
}


class BookStackNotices:
    def __init__(self, notify: Notify, title: Callable[[str], str]) -> None:
        self._notify = notify
        self._title = title

    async def report(self, health: BookStackSyncHealth) -> None:
        if health.token_refused:
            await self._notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR, severity=NotificationSeverity.ERROR,
                title=self._title("BookStack refused the API token"),
                message="BookStack content is not being synced. The token may have been deleted or may have "
                        "expired, or its user may have lost permission to use the API. Create a new token in "
                        "BookStack (your profile, API Tokens) and enter it in the connector settings.")
            return
        if health.missing_permissions:
            names = sorted(PERMISSION_NAMES.get(p, p) for p in health.missing_permissions)
            await self._notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR, severity=NotificationSeverity.ERROR,
                title=self._title("The BookStack token cannot read everything it needs"),
                message="BookStack content and who may see it are not fully synced. Give the role of the "
                        "token's user these permissions in BookStack: " + ", ".join(names) + ". An Admin "
                        "user's token has all of them.")
