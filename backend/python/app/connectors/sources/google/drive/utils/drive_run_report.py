"""What one Drive run tells the people who own the connector.

One job: after a run, decide whether anyone should be told anything, and say it in words they can act on.

- A setup problem (delegation, the service account key, the admin account, a disabled API, missing scopes, a
  personal sign-in Google no longer accepts) is reported as an error, with what to change. It is the same
  notice every run, so the platform's notification backoff keeps it to one, then one a day or so later.
- A run that could not read part of Drive for a passing reason (rate limits, outages) is not reported: the
  next run retries it. Only when that has happened `PERSISTENT_RUNS` runs in a row is one warning sent.
- Selected folders nobody can open are reported once as a warning: someone has to share or unselect them.

Notices never carry error text, ids of requests, or anything else from inside the connector.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.connectors.sources.google.common.access_problems import (
    AccessProblem,
    classify_access_problem,
)
from app.services.notification.types import NotificationSeverity, NotificationType

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.core.base.sync_point.sync_point import SyncPoint

RUN_HEALTH_SYNC_POINT_KEY = "run_health"
PERSISTENT_RUNS = 3

Notify = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class DriveNotice:
    type: NotificationType
    severity: NotificationSeverity
    event: str
    message: str


def message_for_connection_failure(error: BaseException, *, personal: bool, admin_email: str | None = None,
                                   client_id: str | None = None) -> str | None:
    """Words for a connection test Google refused, or None when the failure may pass on its own."""
    problem = classify_access_problem(error, personal=personal)
    if problem is None:
        return None
    notice = notice_for(problem, personal=personal, admin_email=admin_email, client_id=client_id)
    return None if notice is None else notice.message


def notice_for(problem: AccessProblem, *, personal: bool, admin_email: str | None = None,
               client_id: str | None = None) -> DriveNotice | None:
    """The notice for a setup problem, or None for one nobody needs to act on."""
    auth = NotificationType.CONNECTOR_AUTH_ERROR
    error = NotificationSeverity.ERROR
    if problem is AccessProblem.SIGNED_OUT:
        return DriveNotice(auth, error, "sign in to Google again",
                           "Google no longer accepts this connector's sign-in, so Drive is not syncing. Open the "
                           "connector and sign in to Google again. Files already synced stay searchable.")
    if problem is AccessProblem.MISSING_SCOPE:
        return DriveNotice(auth, error, "Google Drive access was not granted",
                           "The Google sign-in for this connector does not allow reading Google Drive files. Open "
                           "the connector, sign in again, and allow access to your Drive files.")
    if problem is AccessProblem.DELEGATION:
        client = f" ({client_id})" if client_id else ""
        return DriveNotice(auth, error, "domain-wide delegation is not set up",
                           "Google Workspace refused access for this connector's service account. In the Google "
                           f"Admin console, open Security > API controls > Domain-wide delegation, and authorize the "
                           f"service account's client ID{client} with the scopes listed in the PipesHub setup guide. "
                           "Syncing resumes on the next run.")
    if problem is AccessProblem.SERVICE_ACCOUNT_KEY:
        return DriveNotice(auth, error, "the service account key is not accepted",
                           "Google rejected this connector's service account key. It may have been deleted or "
                           "disabled in Google Cloud. Create a new key for the service account and upload it in the "
                           "connector's settings.")
    if problem is AccessProblem.UNREADABLE_KEY:
        return DriveNotice(auth, error, "the service account key is not valid",
                           "The service account key in this connector's settings is not a valid JSON key file. In "
                           "Google Cloud Console, open IAM & Admin > Service accounts, create a JSON key for the "
                           "service account, and upload that file in the connector's settings.")
    if problem is AccessProblem.ADMIN_ACCOUNT:
        who = f" ({admin_email})" if admin_email else ""
        return DriveNotice(auth, error, "the admin account cannot read the directory",
                           f"The admin email in this connector's settings{who} must be an active Google Workspace "
                           "administrator who can read users and groups. Update it in the connector's settings.")
    if problem is AccessProblem.API_DISABLED:
        apis = "Google Drive API" if personal else "Google Drive API and the Admin SDK API"
        return DriveNotice(auth, error, "a Google API is turned off",
                           f"The {apis} must be turned on for this connector's Google Cloud project. Turn them on "
                           "in Google Cloud Console under APIs & Services, then sync again.")
    return None


class DriveRunReport:
    """Collects one run's setup problems and gaps, and tells the connector's owners at the end."""

    def __init__(self, *, personal: bool, notify: Notify, title: Callable[[str], str], sync_point: SyncPoint,
                 logger: Logger, admin_email: str | None = None, client_id: str | None = None) -> None:
        self.personal = personal
        self._notify = notify
        self._title = title
        self._sync_point = sync_point
        self._logger = logger
        self._admin_email = admin_email
        self._client_id = client_id
        self.problem: AccessProblem | None = None
        self.unreadable_folders: list[str] = []

    def note_failure(self, error: BaseException, *, impersonating_admin: bool = True) -> AccessProblem | None:
        """Classify a failure; remember the first setup problem. Returns what it was, if anything."""
        problem = classify_access_problem(error, personal=self.personal, impersonating_admin=impersonating_admin)
        if problem is not None and problem is not AccessProblem.USER_UNAVAILABLE and self.problem is None:
            self.problem = problem
        return problem

    def note_unreadable_folders(self, folder_ids: list[str]) -> None:
        self.unreadable_folders = sorted(set(folder_ids))

    async def finish(self, *, complete: bool) -> None:
        """Send what this run calls for. Never raises: telling people must not change the run's outcome."""
        try:
            await self._finish(complete=complete)
        except Exception as e:
            self._logger.warning("Could not report the Drive run's outcome: %s", e)

    async def _finish(self, *, complete: bool) -> None:
        if self.problem is not None:
            notice = notice_for(self.problem, personal=self.personal, admin_email=self._admin_email,
                                client_id=self._client_id)
            if notice is not None:
                await self._send(notice)
        if self.unreadable_folders:
            count = len(self.unreadable_folders)
            await self._send(DriveNotice(
                NotificationType.CONNECTOR_WARNING, NotificationSeverity.WARNING,
                "some selected folders cannot be opened",
                f"{count} folder{'s' if count != 1 else ''} in this connector's folder filter cannot be opened by "
                "anyone it syncs, so nothing inside is synced. Share the folders with a person who uses PipesHub, "
                "or remove them from the folder filter."))
        stored = await self._sync_point.read_sync_point(RUN_HEALTH_SYNC_POINT_KEY) or {}
        runs = int(stored.get("incompleteRuns") or 0)
        if complete:
            if runs:
                await self._sync_point.update_sync_point(RUN_HEALTH_SYNC_POINT_KEY, {"incompleteRuns": 0})
            return
        runs += 1
        await self._sync_point.update_sync_point(RUN_HEALTH_SYNC_POINT_KEY, {"incompleteRuns": runs})
        if runs == PERSISTENT_RUNS and self.problem is None:
            await self._send(DriveNotice(
                NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.WARNING,
                "some of Google Drive is not syncing",
                f"Google Drive did not answer some requests in the last {PERSISTENT_RUNS} syncs, so part of Drive "
                "may be out of date in PipesHub. Each sync tries again. If this continues, check Google Workspace "
                "status and the connector's settings."))

    async def _send(self, notice: DriveNotice) -> None:
        await self._notify(type=notice.type, severity=notice.severity, title=self._title(notice.event),
                           message=notice.message)
