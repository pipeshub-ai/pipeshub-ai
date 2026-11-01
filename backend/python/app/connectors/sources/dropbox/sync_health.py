"""What one Dropbox sync run could not read or store.

A run goes on past a part of Dropbox it cannot read (a member's folder, a shared folder's members, a group), so the
rest still syncs. Two things must then not happen: the run reporting success, and a full sync removing what it did
not reach. This collects those parts so the connector can say both, and tell someone once.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class DropboxSyncIncomplete(Exception):
    """Raised at the end of a run that skipped part of Dropbox, so the run is recorded as failed."""


class DropboxAuthFailed(Exception):
    """Dropbox refused the connector's credentials: nothing can be read until someone reconnects it."""


@dataclass
class SyncHealth:
    problems: list[str] = field(default_factory=list)
    auth_error: str | None = None
    # Folders listed again from the start because Dropbox reset their cursor.
    relisted: list[str] = field(default_factory=list)

    def skipped(self, what: str, error: object) -> None:
        self.problems.append(f"{what}: {error}")

    def auth_failed(self, error: object) -> None:
        if self.auth_error is None:
            self.auth_error = str(error)

    @property
    def complete(self) -> bool:
        return not self.problems and self.auth_error is None

    def summary(self, limit: int = 5) -> str:
        shown = "; ".join(self.problems[:limit])
        more = len(self.problems) - limit
        return shown + (f"; and {more} more" if more > 0 else "")

    def raise_if_incomplete(self) -> None:
        if self.auth_error is not None:
            raise DropboxAuthFailed(f"Dropbox refused the connector's credentials: {self.auth_error}")
        if self.problems:
            raise DropboxSyncIncomplete(
                f"Dropbox sync skipped {len(self.problems)} part(s) of the source: {self.summary()}"
            )
