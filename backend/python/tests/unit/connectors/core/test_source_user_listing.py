"""SourceUserListing: what one sync's user listing proves about one email.

PRESENT needs positive evidence (the email on an identified row, or a lookup that found it).
ABSENT needs a listing the source said was over, with nothing cut, failed or unidentified, and
either a listing that includes inactive users or a lookup that settled this email as absent.
Anything else is UNKNOWN: unknown is never absence.
"""

from collections.abc import Callable

import pytest

from app.connectors.core.base.connector.source_user_listing import Presence, SourceUserListing

CREATOR = "Creator@Corp.com"

Step = Callable[[SourceUserListing], None]


def _rows(*emails: str | None) -> Step:
    def run(listing: SourceUserListing) -> None:
        for email in emails:
            listing.add_row(email)
    return run


def _finish(listing: SourceUserListing) -> None:
    listing.finish()


def _cut(listing: SourceUserListing) -> None:
    listing.cut("empty page with a cursor")


def _row_failed(listing: SourceUserListing) -> None:
    listing.row_failed("row could not be read")


def _settle(presence: Presence, email: str = CREATOR) -> Step:
    def run(listing: SourceUserListing) -> None:
        listing.settle(email, presence)
    return run


P, A, U = Presence.PRESENT, Presence.ABSENT, Presence.UNKNOWN

CASES: list[tuple[str, bool, list[Step], Presence]] = [
    # (id, includes_inactive, steps, presence of CREATOR)
    ("listed-finished", True, [_rows("a@corp.com", " creator@corp.com "), _finish], P),
    ("listed-cut", True, [_rows("creator@corp.com"), _cut], P),
    ("listed-unfinished", False, [_rows("creator@corp.com")], P),
    ("listed-with-row-failure", True, [_rows("creator@corp.com"), _row_failed, _finish], P),
    ("absent-from-full-listing", True, [_rows("a@corp.com"), _finish], A),
    ("absent-not-finished", True, [_rows("a@corp.com")], U),
    ("absent-but-cut", True, [_rows("a@corp.com"), _cut, _finish], U),
    ("absent-but-row-failed", True, [_rows("a@corp.com"), _row_failed, _finish], U),
    ("absent-but-unidentified-row", True, [_rows("a@corp.com", None), _finish], U),
    ("absent-but-blank-row", True, [_rows("a@corp.com", "  "), _finish], U),
    ("empty-listing", True, [_finish], U),
    ("active-only-listing", False, [_rows("a@corp.com"), _finish], U),
    ("active-only-settled-absent", False, [_rows("a@corp.com"), _settle(A), _finish], A),
    ("unidentified-settled-absent", True, [_rows("a@corp.com", None), _settle(A), _finish], A),
    ("settled-absent-but-cut", False, [_rows("a@corp.com"), _settle(A), _cut, _finish], U),
    ("settled-absent-not-finished", False, [_rows("a@corp.com"), _settle(A)], U),
    ("settled-absent-but-row-failed", True, [_rows("a@corp.com"), _settle(A), _row_failed, _finish], U),
    ("settled-absent-empty-listing", True, [_settle(A), _finish], U),
    ("settled-absent-other-email", False, [_rows("a@corp.com"), _settle(A, "other@corp.com"), _finish], U),
    ("settled-present", False, [_settle(P), _cut], P),
    ("settled-present-wins-over-absent", True, [_rows("a@corp.com"), _settle(P), _settle(A), _finish], P),
    ("settled-unknown-is-no-evidence", True, [_rows("a@corp.com"), _settle(U), _finish], A),
]


@pytest.mark.parametrize(("includes_inactive", "steps", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_presence(includes_inactive: bool, steps: list[Step], expected: Presence) -> None:
    listing = SourceUserListing(includes_inactive=includes_inactive)
    for step in steps:
        step(listing)

    assert listing.presence(CREATOR) is expected


@pytest.mark.parametrize("email", [None, "", "   "])
def test_no_email_is_unknown(email: str | None) -> None:
    listing = SourceUserListing(includes_inactive=True)
    listing.add_row("a@corp.com")
    listing.finish()

    assert listing.presence(email) is Presence.UNKNOWN


@pytest.mark.parametrize("value", [
    "alice at corp dot com",
    "alice[at]corp.com",
    "creator",
    "creator@",
    "@corp.com",
    "a@b@corp.com",
    "creator @corp.com",
    "creator@corp .com",
    "creator\t@corp.com",
], ids=["masked-words", "masked-brackets", "no-at", "no-domain", "no-local-part", "two-ats", "space-before-at",
        "space-in-domain", "tab"])
def test_a_value_that_is_no_email_is_an_unidentified_row(value: str) -> None:
    listing = SourceUserListing(includes_inactive=True)
    listing.add_row("a@corp.com")
    listing.add_row(value)
    listing.finish()

    assert listing.emails == frozenset({"a@corp.com"})
    assert listing.presence(CREATOR) is Presence.UNKNOWN, "the masked row may be the creator"
    assert listing.presence(value) is Presence.UNKNOWN


@pytest.mark.parametrize("value", ["alice at corp dot com", "creator", "a@b@corp.com"])
def test_a_lookup_of_a_value_that_is_no_email_settles_nothing(value: str) -> None:
    listing = SourceUserListing(includes_inactive=False)
    listing.add_row("a@corp.com")
    listing.settle(value, Presence.PRESENT)
    listing.finish()

    assert listing.presence(value) is Presence.UNKNOWN
    assert listing.emails == frozenset({"a@corp.com"})


@pytest.mark.parametrize("value", [" Creator@Corp.com ", "creator+tag@corp.co.uk", "o'brien@corp.com"])
def test_a_valid_email_is_an_identified_row(value: str) -> None:
    listing = SourceUserListing(includes_inactive=True)
    listing.add_row(value)
    listing.finish()

    assert listing.presence(value) is Presence.PRESENT


def test_emails_are_the_identified_rows_normalised() -> None:
    listing = SourceUserListing(includes_inactive=True)
    for email in (" A@Corp.com ", "b@corp.com", None, ""):
        listing.add_row(email, source_id="x")

    assert listing.emails == frozenset({"a@corp.com", "b@corp.com"})


def test_is_cut() -> None:
    listing = SourceUserListing(includes_inactive=True)
    assert listing.is_cut is False

    listing.cut("users.list failed")

    assert listing.is_cut is True
