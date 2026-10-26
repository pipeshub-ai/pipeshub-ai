from app.connectors.sources.atlassian.core.confluence_access import (
    listing_modified_after,
)


def test_a_checkpoint_with_an_offset_is_converted_to_utc() -> None:
    # 12:00 at +05:30 is 06:30 UTC; a day earlier is 06:30 UTC the day before.
    bound = listing_modified_after(None, "2026-10-10T12:00:00+05:30", 24)
    assert bound == "2026-10-09T06:30:00.000Z"


def test_a_utc_checkpoint_is_moved_back_only_by_the_look_back() -> None:
    assert listing_modified_after(None, "2026-10-10T12:00:00.000Z", 24) == "2026-10-09T12:00:00.000Z"
    assert listing_modified_after(None, "2026-10-10T12:00:00", 24) == "2026-10-09T12:00:00.000Z"


def test_the_admin_date_wins_when_it_is_later_than_the_look_back() -> None:
    admin = "2026-10-10T01:00:00.000Z"
    assert listing_modified_after(admin, "2026-10-10T12:00:00+05:30", 24) == admin
