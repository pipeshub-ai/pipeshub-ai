"""What one Gmail sync run could not do, and what that means for the run.

One job: collect the failures of a run per mailbox and answer three questions about them: may the run report
success, may a full sync remove what it did not see, and is there something a person has to fix. A mailbox
skipped for a reason that is not a failure (Gmail turned off for that person, the account suspended) is noted
but does not fail the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.google.common.gmail_errors import (
    NEEDS_ATTENTION,
    GmailFailure,
    classify,
)

#: A mailbox that cannot be read for these reasons is not part of the sync, rather than a failed part of it.
_NOT_A_FAILURE = {GmailFailure.MAIL_DISABLED, GmailFailure.USER_UNAVAILABLE}


class GmailSyncIncomplete(Exception):
    """Raised at the end of a run that skipped part of what it should have read."""


@dataclass
class GmailProblem:
    mailbox: str
    failure: GmailFailure
    what: str


@dataclass
class GmailSyncHealth:
    problems: list[GmailProblem] = field(default_factory=list)

    def record(self, mailbox: str, error: BaseException, what: str) -> GmailFailure:
        failure = classify(error)
        self.problems.append(GmailProblem(mailbox, failure, what))
        return failure

    def record_failure(self, mailbox: str, failure: GmailFailure, what: str) -> None:
        self.problems.append(GmailProblem(mailbox, failure, what))

    @property
    def failures(self) -> list[GmailProblem]:
        return [p for p in self.problems if p.failure not in _NOT_A_FAILURE]

    @property
    def complete(self) -> bool:
        return not self.failures

    def mailboxes_skipped(self, failure: GmailFailure) -> set[str]:
        return {p.mailbox for p in self.problems if p.failure is failure}

    def needs_attention(self) -> set[GmailFailure]:
        return {p.failure for p in self.problems if p.failure in NEEDS_ATTENTION}

    def coverage(self) -> FullSyncCoverage:
        """A full sync that missed part of a mailbox cannot tell what is gone, so it removes nothing."""
        return FullSyncCoverage(complete=self.complete)

    def summary(self) -> dict[str, object]:
        failed = sorted({p.mailbox for p in self.failures})
        return {"mailboxesFailed": len(failed), "problems": len(self.failures),
                "reasons": ",".join(sorted({p.failure.value for p in self.failures}))}

    def raise_if_incomplete(self) -> None:
        if self.failures:
            failed = sorted({p.mailbox for p in self.failures})
            raise GmailSyncIncomplete(
                f"Gmail sync incomplete: {len(self.failures)} problem(s) in {len(failed)} mailbox(es); "
                f"reasons: {sorted({p.failure.value for p in self.failures})}")
