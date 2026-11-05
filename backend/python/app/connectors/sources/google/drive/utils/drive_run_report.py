"""What one Drive run tells the people who own the connector.

One job: after a run, decide whether anyone should be told anything, and say it in words they can act on.

- A setup problem (delegation, the service account key, the admin account, a disabled API, missing scopes, a
  personal sign-in Google no longer accepts) is reported as an error, with what to change. It is the same
  notice every run, so the platform's notification backoff keeps it to one, then one a day or so later.
- A run that could not read part of Drive for a passing reason (rate limits, outages) is not reported: the
  next run retries it. Only when that has happened `PERSISTENT_RUNS` runs in a row is one warning sent.
- Selected folders nobody it syncs can find or open (a wrong ID, a deleted folder, one not shared with anyone
  synced) are reported as a warning: someone has to fix the ID, share the folder, or unselect it.

Notices never carry error text, ids of requests, or anything else from inside the connector.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.connectors.sources.google.common.access_problems import (
    AccessProblem,
    classify_access_problem,
    setup_problem_message,
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
                                   client_id: str | None = None, scopes: list[str] | None = None) -> str | None:
    """Words for a connection test Google refused, or None when the failure may pass on its own."""
    problem = classify_access_problem(error, personal=personal)
    if problem is None:
        return None
    notice = notice_for(problem, personal=personal, admin_email=admin_email, client_id=client_id, scopes=scopes)
    return None if notice is None else notice.message


_SETUP_EVENTS = {
    AccessProblem.DELEGATION: "domain-wide delegation is not set up",
    AccessProblem.SERVICE_ACCOUNT_KEY: "the service account key is not accepted",
    AccessProblem.UNREADABLE_KEY: "the service account key is not valid",
    AccessProblem.ADMIN_ACCOUNT: "the admin account cannot read the directory",
    AccessProblem.API_DISABLED: "a Google API is turned off",
}


def notice_for(problem: AccessProblem, *, personal: bool, admin_email: str | None = None,
               client_id: str | None = None, scopes: list[str] | None = None) -> DriveNotice | None:
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
    event = _SETUP_EVENTS.get(problem)
    if event is None:
        return None
    apis = "Google Drive API" if personal else "Google Drive API and the Admin SDK API"
    return DriveNotice(auth, error, event, setup_problem_message(problem, apis=apis, admin_email=admin_email,
                                                                 client_id=client_id, scopes=scopes))



class DriveRunReport:
    """Collects one run's setup problems and gaps, and tells the connector's owners at the end."""

    def __init__(self, *, personal: bool, notify: Notify, title: Callable[[str], str], sync_point: SyncPoint,
                 logger: Logger, admin_email: str | None = None, client_id: str | None = None,
                 scopes: list[str] | None = None) -> None:
        self.personal = personal
        self._notify = notify
        self._title = title
        self._sync_point = sync_point
        self._logger = logger
        self._admin_email = admin_email
        self._client_id = client_id
        self._scopes = scopes
        self.problem: AccessProblem | None = None
        self.unreadable_folders: list[str] = []
        # Users Google would not let the service account act as, for the run's one log line.
        self.refused_users: set[str] = set()

    def note_failure(self, error: BaseException, *, impersonating_admin: bool = True) -> AccessProblem | None:
        """Classify a failure; remember the first setup problem. Returns what it was, if anything."""
        problem = classify_access_problem(error, personal=self.personal, impersonating_admin=impersonating_admin)
        if problem is not None and problem is not AccessProblem.USER_UNAVAILABLE and self.problem is None:
            self.problem = problem
        return problem

    def note_user_failure(self, error: BaseException, user_email: str) -> bool:
        """A failure acting as one user. True when it is a setup problem, which the run's notice covers:
        the caller then skips the user without logging a traceback of its own. The first one in a run is
        logged once, as a warning; a setup problem is the same for every user, so one line says it."""
        problem = self.note_failure(error, impersonating_admin=False)
        if problem is None or problem is AccessProblem.USER_UNAVAILABLE:
            return False
        if not self.refused_users:
            self._logger.warning(
                "Google refused to let the service account act as %s (%s); users refused the same way this "
                "run are skipped without a further log line, and the connector's owners are told what to fix",
                user_email, problem.value,
            )
        self.refused_users.add(user_email)
        return True

    def note_unreadable_folders(self, folder_ids: list[str]) -> None:
        self.unreadable_folders = sorted(set(folder_ids))

    async def finish(self, *, complete: bool) -> None:
        """Send what this run calls for. Never raises: telling people must not change the run's outcome."""
        try:
            await self._finish(complete=complete)
        except Exception as e:
            self._logger.warning("Could not report the Drive run's outcome: %s", e)

    async def _finish(self, *, complete: bool) -> None:
        if self.refused_users:
            self._logger.warning("Google refused the service account for %d user(s) this run",
                                 len(self.refused_users))
        if self.problem is not None:
            notice = notice_for(self.problem, personal=self.personal, admin_email=self._admin_email,
                                client_id=self._client_id, scopes=self._scopes)
            if notice is not None:
                await self._send(notice)
        if self.unreadable_folders:
            count = len(self.unreadable_folders)
            await self._send(DriveNotice(
                NotificationType.CONNECTOR_WARNING, NotificationSeverity.WARNING,
                "some selected folders cannot be opened",
                f"{count} folder{'s' if count != 1 else ''} in this connector's folder filter cannot be found or "
                "opened by anyone it syncs, so nothing inside is synced. Check that the folder IDs are right, share "
                "the folders with a person who uses PipesHub, or remove them from the folder filter."))
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
