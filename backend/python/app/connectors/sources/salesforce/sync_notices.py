"""What a Salesforce sync tells the people who run the connector.

A sync that stops says why and what to do about it: sign in again, wait for the org's API
allowance, or give the connected user a permission. A sync that finishes but could not do
everything sends one warning listing what it could not do. A clean sync sends nothing.
The platform backs off repeats of the same notice, so a line names no count or time that
changes from one sync to the next unless the change is news.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.salesforce.api_errors import salesforce_error
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.url_redaction import redact_urls_in_text

_KEPT = "The sync stopped; nothing it had not read was removed."

CHATTER_NEEDS_VIEW_ALL = (
    "Deals and cases whose Chatter discussion changed could not be found: Salesforce lets only "
    "a user with the View All Data permission list Chatter posts. A deal's or a case's "
    "discussion is updated in search only when the record itself changes. Give the "
    "connected user View All Data."
)

ACCESS_KEPT = (
    "Salesforce did not say who can read some records, so the access PipesHub already had "
    "for them was kept. A share removed in Salesforce may still apply in PipesHub until a "
    "later sync reads it."
)


@dataclass(frozen=True)
class Notice:
    type: NotificationType
    severity: NotificationSeverity
    event: str  # the title, after the connector's name
    message: str


def stopped_sync_notice(error: BaseException) -> Notice:
    """Why a sync stopped, and what will make the next one work."""
    answer = salesforce_error(error)
    status = answer.status if answer else None
    code = answer.error_code if answer else None
    # The stream helpers turn Salesforce's 401 into a 409 "reconnect".
    mapped_401 = isinstance(error, HTTPException) and error.status_code == HttpStatusCode.CONFLICT.value
    if status == HttpStatusCode.UNAUTHORIZED.value or code == "invalid_grant" or mapped_401:
        return Notice(
            NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR, "needs to be reconnected",
            f"Salesforce refused the connector's sign-in, also after a token refresh. {_KEPT} "
            "Sign in to Salesforce again from the connector's settings.",
        )
    if code == "REQUEST_LIMIT_EXCEEDED" and status == HttpStatusCode.SERVICE_UNAVAILABLE.value:
        return Notice(
            NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.WARNING,
            "sync paused, Salesforce Chatter limit reached",
            "The connected Salesforce user has used this hour's Chatter request allowance for this "
            f"app. {_KEPT} The next sync continues once Salesforce accepts Chatter requests again.",
        )
    if code == "REQUEST_LIMIT_EXCEEDED":
        return Notice(
            NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR,
            "sync paused, Salesforce API limit reached",
            "Your Salesforce org has used its API request allowance for the last 24 hours. "
            f"{_KEPT} The next sync continues once Salesforce accepts requests again.",
        )
    if status == HttpStatusCode.FORBIDDEN.value:
        return Notice(
            NotificationType.CONNECTOR_NOT_ACCESSIBLE, NotificationSeverity.ERROR, "cannot read Salesforce",
            f"Salesforce refused a request the sync needs ({code or 'HTTP 403'}: {answer.detail[:200]}). "
            f"{_KEPT} The connected Salesforce user needs the API Enabled permission and read access to "
            "what the connector syncs; a user with View All Data reads all of it.",
        )
    reason = str(answer) if answer else str(error)
    return Notice(
        NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR, "sync failed",
        f"The sync stopped: {redact_urls_in_text(reason)[:200]}. {_KEPT} Recent Salesforce changes "
        "may not be in search yet; the next sync tries again.",
    )


def unreadable_list(what: str) -> str:
    return f"The list of Salesforce {what} could not be read, so the access that depends on it was left as it was."


class SyncProblems:
    """What one sync could not do, sent as one warning when it finishes."""

    MAX_SHOWN = 10

    def __init__(self) -> None:
        self._lines: list[str] = []
        self.skipped_records = 0

    def note(self, problem: str) -> None:
        if problem not in self._lines:
            self._lines.append(problem)

    def notice(self) -> Optional[Notice]:
        lines = list(self._lines)
        if self.skipped_records:
            lines.append(
                f"{self.skipped_records} Salesforce record(s) could not be read and were skipped; "
                "the connector's log names them."
            )
        if not lines:
            return None
        text = "\n".join(f"- {line}" for line in lines[: self.MAX_SHOWN])
        if len(lines) > self.MAX_SHOWN:
            text += f"\n- and {len(lines) - self.MAX_SHOWN} more (see the connector's log)"
        return Notice(
            NotificationType.CONNECTOR_WARNING, NotificationSeverity.WARNING,
            "sync finished with problems", f"The sync finished, but:\n{text}",
        )
