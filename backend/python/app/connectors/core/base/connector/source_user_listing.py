"""What one sync's user listing proves about one person.

A connector builds one ``SourceUserListing`` per sync while it reads the source's users and asks it
whether the user who authenticated the connector is among them. Unknown is never absence: a person is
ABSENT only when the listing is known to be whole, otherwise UNKNOWN.
"""

import re
from enum import Enum


class Presence(Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


# One "@" between a non-empty local part and a domain, and no whitespace inside. Sources mask an
# address they will not show ("alice at corp dot com"); such a value names nobody.
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")


def _normalise(email: str | None) -> str:
    """The email lower-cased, or "" when the value is no email: a row with it is unidentified."""
    normalised = email.strip().lower() if isinstance(email, str) else ""
    return normalised if _EMAIL.fullmatch(normalised) else ""


class SourceUserListing:
    """One sync's user listing, built while the connector reads it. Holds no I/O.

    ``includes_inactive``: the listing names suspended, deactivated and deleted users too, so a
    person it leaves out has no account in any state. An active-only listing proves absence only
    through ``settle``.
    """

    def __init__(self, *, includes_inactive: bool) -> None:
        self.includes_inactive = includes_inactive
        self._emails: set[str] = set()
        self._rows = 0
        self._unidentified_rows = 0
        self._row_failures: list[str] = []
        self._cuts: list[str] = []
        self._finished = False
        self._settled: dict[str, Presence] = {}

    def add_row(self, email: str | None, source_id: str | None = None) -> None:
        """A user the source listed. A row without a valid email (none, blank, or masked such as
        "alice at corp dot com") is a person whose identity is unknown."""
        self._rows += 1
        normalised = _normalise(email)
        if normalised:
            self._emails.add(normalised)
        else:
            self._unidentified_rows += 1

    def row_failed(self, reason: str = "") -> None:
        """A listed row could not be read: whoever it was is unknown."""
        self._row_failures.append(reason)

    def cut(self, reason: str) -> None:
        """The listing stopped short or cannot be trusted to be whole: a failed or malformed page, an
        empty page that points further, a capped search."""
        self._cuts.append(reason)

    def finish(self) -> None:
        """The source said the listing ended."""
        self._finished = True

    def settle(self, email: str | None, presence: Presence) -> None:
        """The answer of a lookup of one email. PRESENT is kept over ABSENT; UNKNOWN is no answer."""
        normalised = _normalise(email)
        if not normalised or presence is Presence.UNKNOWN:
            return
        if self._settled.get(normalised) is not Presence.PRESENT:
            self._settled[normalised] = presence

    def presence(self, email: str | None) -> Presence:
        normalised = _normalise(email)
        if not normalised:
            return Presence.UNKNOWN
        settled = self._settled.get(normalised)
        if normalised in self._emails or settled is Presence.PRESENT:
            return Presence.PRESENT
        if not self._finished or self._cuts or self._row_failures or not self._rows:
            return Presence.UNKNOWN
        if settled is Presence.ABSENT:
            return Presence.ABSENT
        if self._unidentified_rows or not self.includes_inactive:
            return Presence.UNKNOWN
        return Presence.ABSENT

    @property
    def emails(self) -> frozenset[str]:
        """The emails of the identified rows: positive evidence only."""
        return frozenset(self._emails)

    @property
    def is_cut(self) -> bool:
        return bool(self._cuts)
