"""Record names read as titles once their ids are gone."""

import pytest

from app.utils.record_titles import is_mostly_ids, is_weak_title, readable_record_name


@pytest.mark.parametrize(("name", "readable"), [
    ("exp_4be1c0f2a9d84e7b9c3a5f6d7e8f9012__ops-311-valve-audit.txt", "ops-311 valve audit"),
    ("sync_9d8c7b6a5f4e3d2c1b0a99887766aa55__2026-03-09-rfc-014-mist-timer.txt",
     "2026-03-09 rfc-014 mist timer"),
    ("7c1e2b3a-4d5f-6a7b-8c9d-0e1f2a3b4c5d_Bay plan.docx", "Bay plan"),
    ("5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b-1712345678901-night_shift.md", "night shift"),
    ("20260411-frost-warning-west.txt", "20260411-frost-warning-west"),
    ("Bay planning notes.docx", "Bay planning notes"),
    ("rec_0f1e2d3c4b5a69788796a5b4c3d2e1f0__1712345678.txt", ""),
    ("OPS-42 Replace misting valve", "OPS-42 Replace misting valve"),
    ("Pallet 4455667788 delayed", "Pallet delayed"),
])
def test_ids_extension_and_separators_are_dropped(name: str, readable: str) -> None:
    assert readable_record_name(name) == readable


def test_the_extension_is_kept_when_the_caller_already_removed_it() -> None:
    assert readable_record_name("firmware-3.1", strip_extension=False) == "firmware-3.1"


@pytest.mark.parametrize(("title", "weak"), [
    ("plant-ops", True), ("Bay plan", True), ("", True), ("Bay planning notes", False),
])
def test_fewer_than_three_words_is_a_weak_title(title: str, weak: bool) -> None:
    assert is_weak_title(title) is weak


@pytest.mark.parametrize(("name", "mostly"), [
    ("5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b__1712345678-mist-timer", True),
    ("7c1e2b3a-4d5f-6a7b-8c9d-0e1f2a3b4c5d", True),
    ("OPS-42 Replace misting valve", False),
    ("Pallet 4455667788 delayed", False),
])
def test_a_name_mostly_of_ids(name: str, mostly: bool) -> None:
    assert is_mostly_ids(name) is mostly
