"""What one SharePoint sync run could not read or store.

A run goes on past a part of the source it cannot read (a site, a library, a permission list), so the rest
still syncs. Two things must then not happen: the run reporting success, and a full sync removing the
records it did not reach. This collects those parts so the connector can say both.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SharePointSyncIncomplete(Exception):
    """Raised at the end of a run that skipped part of the source, so the run is recorded as failed."""


@dataclass
class SyncHealth:
    problems: list[str] = field(default_factory=list)

    def skipped(self, what: str, error: object) -> None:
        self.problems.append(f"{what}: {error}")

    @property
    def complete(self) -> bool:
        return not self.problems

    def summary(self, limit: int = 5) -> str:
        shown = "; ".join(self.problems[:limit])
        more = len(self.problems) - limit
        return shown + (f"; and {more} more" if more > 0 else "")

    def raise_if_incomplete(self) -> None:
        if self.problems:
            raise SharePointSyncIncomplete(
                f"SharePoint sync skipped {len(self.problems)} part(s) of the source: {self.summary()}"
            )
