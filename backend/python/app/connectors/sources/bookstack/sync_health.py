"""What went wrong in one BookStack sync run, and what that allows.

One job: collect the run's failures and answer three questions from them: may the run report success, may a
full sync remove what it did not see, and what should a person be told. A failure that passes with time (an
outage, a rate limit BookStack kept refusing) only fails the run, so it is tried again; a refusal (401, 403)
repeats until someone changes the token or its user's role, so it is also reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HTTP_UNAUTHORIZED, HTTP_FORBIDDEN = 401, 403
# BookStack's ApiTokenGuard answers these with 403 (lang/en/errors.php): the token, not a missing role permission.
TOKEN_GUARD_MESSAGES = frozenset({
    "The authorization token used has expired",
    "The owner of the used API token does not have permission to make API calls",
})


class BookStackSyncIncompleteError(Exception):
    """The run did not read or store everything; the platform records it as failed and the next run retries."""


@dataclass
class BookStackSyncHealth:
    problems: list[str] = field(default_factory=list)
    # BookStack refused the token itself: revoked, wrong, expired, or its user lost "access system API".
    token_refused: bool = False
    # What the token's user may not do, by the BookStack permission it needs.
    missing_permissions: set[str] = field(default_factory=set)

    def fail(self, what: str) -> None:
        self.problems.append(what)

    def refused(self, what: str, status: int | None, permission: str, message: str | None = None) -> None:
        """A read BookStack refused. 401 is the token; so is a 403 BookStack's token guard sends (an expired
        token, or a user who lost "access system API"); any other 403 is the token's user lacking ``permission``."""
        self.fail(f"{what} (HTTP {status})")
        if status == HTTP_UNAUTHORIZED or (status == HTTP_FORBIDDEN and message in TOKEN_GUARD_MESSAGES):
            self.token_refused = True
        elif status == HTTP_FORBIDDEN:
            self.missing_permissions.add(permission)

    def note_status(self, what: str, status: int | None, permission: str, message: str | None = None) -> None:
        if status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
            self.refused(what, status, permission, message)
        else:
            self.fail(what if status is None else f"{what} (HTTP {status})")

    @property
    def ok(self) -> bool:
        return not self.problems

    def raise_if_failed(self) -> None:
        if self.problems:
            shown = "; ".join(self.problems[:5]) + (f"; and {len(self.problems) - 5} more" if len(self.problems) > 5 else "")
            raise BookStackSyncIncompleteError(f"BookStack sync incomplete: {shown}")
