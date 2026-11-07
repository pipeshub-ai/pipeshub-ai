"""stated_date: the day a record's own text gives for it, and which rule found it."""

import time
from datetime import date

from app.modules.named_entities.stated_dates import stated_date


def test_an_email_thread_takes_its_last_message_header() -> None:
    text = (
        "Badge printer swap\n\nFrom: Priya <priya@example.com>\nDate: Tue, Mar 4, 2025 at 8:40 AM\n"
        "Subject: printer\n\nbody\n\nFrom: Tomas <tomas@example.com>\nDate: Thu, Mar 6, 2025 at 2:15 PM\nreply"
    )
    found = stated_date("thread", text)
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 3, 6), "message header")


def test_an_iso_timestamp_in_a_message_header_gives_its_day() -> None:
    found = stated_date("thread", "From: Priya <priya@example.com>\nDate: 2025-05-07T10:00:00Z\n\nbody")
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 5, 7), "message header")


def test_an_iso_timestamp_on_a_labelled_line_gives_its_day() -> None:
    found = stated_date("spec", "Badge spec\nLast updated: 2025-04-09T08:15:00+02:00\n")
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 4, 9), "labelled date")


def test_a_date_header_inside_a_code_fence_is_not_a_message_header() -> None:
    text = "Sample reply:\n```\nHTTP/1.1 503\nDate: Wed, 09 Apr 2025 07:00:00 GMT\n```\nRetested on 2025-02-11."
    found = stated_date("ticket", text)
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 2, 11), "latest date mentioned")


def test_a_comment_log_takes_its_last_entry() -> None:
    text = (
        "Kiosk screen flickers\n## Comments\n2025-05-02 11:10 UTC - Marta: first seen at the north desk\n"
        "2025-05-07 15:45 UTC - Felix: cable replaced, check again on 2025-06-01\n"
    )
    found = stated_date("DESK-12", text)
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 5, 7), "log entry")


def test_revision_table_rows_are_log_entries() -> None:
    text = "Revisions\n| 2024-11-18 | Jun | outline |\n| 2025-01-27 | Jun | budget table added |\n"
    found = stated_date("page", text)
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 1, 27), "log entry")


def test_a_labelled_change_date_wins_over_a_due_date() -> None:
    text = "Office move checklist\nCreated: 2024-10-03\nLast updated: Feb 12, 2025\nDue: 2025-04-30\n"
    found = stated_date("checklist", text)
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 2, 12), "labelled date")


def test_a_creation_label_alone_dates_the_record() -> None:
    found = stated_date("notice", "Notice\nPublished: 21 Jan 2025\n\nText.")
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 1, 21), "labelled date")


def test_a_label_far_back_on_one_long_line_does_not_date_the_record() -> None:
    found = stated_date("dump", "Created: " + "x" * 500 + " 2025-03-01")
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 3, 1), "latest date mentioned")


def test_many_dates_on_one_long_line_are_read_in_linear_time() -> None:
    text = " ".join(f"row {i} 2024-02-{i % 28 + 1:02d}" for i in range(20_000))
    started = time.monotonic()
    found = stated_date("export", text)
    assert time.monotonic() - started < 5
    assert found is not None
    assert found.day == date(2024, 2, 28)


def test_a_unix_time_in_the_file_name_dates_a_chat_export() -> None:
    found = stated_date("export_7f3c__1712000000-canteen-menu", "food-committee\n\nNadia: soup on fridays")
    assert found is not None
    assert (found.day, found.basis) == (date(2024, 4, 1), "file name")


def test_digits_inside_an_id_are_not_a_unix_time() -> None:
    assert stated_date("exp_ab1712000000cd__notes", "nothing dated here") is None


def test_with_no_other_rule_the_latest_mentioned_date_counts() -> None:
    text = "Planning began 2024-09-15; the opening is set for August 8, 2025."
    found = stated_date("brief", text)
    assert found is not None
    assert (found.day, found.basis) == (date(2025, 8, 8), "latest date mentioned")


def test_impossible_dates_are_ignored() -> None:
    assert stated_date("x", "Booked for 2025-02-30 and 2025-14-01.") is None
