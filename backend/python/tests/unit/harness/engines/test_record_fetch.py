"""FR-1, FR-10 (S1c): which records count as shown to an engine run, and short labels."""

from __future__ import annotations

from typing import Any

from app.harness.engines.record_fetch import (
    resolve_record_label,
    surfaced_record_ids,
)
from app.utils.chat_helpers import RecordIdShortener


def test_fr_1_surfaced_ids_are_the_requests_known_ids_and_its_record_map() -> None:
    state: dict[str, Any] = {
        "known_record_ids": {"nav-1"},
        "virtual_record_id_to_result": {"vr-a": {"id": "rec-a"}, "vr-none": None},
    }
    assert surfaced_record_ids(state) == {"nav-1", "rec-a"}
    assert surfaced_record_ids({}) == frozenset()


def test_fr_10_a_short_label_resolves_to_its_record() -> None:
    shortener = RecordIdShortener()
    label = shortener.get_or_create_short_id("2b0c1f7e-full-record-id")
    state: dict[str, Any] = {"record_id_shortener": shortener}
    assert resolve_record_label(state, label) == "2b0c1f7e-full-record-id"
    assert resolve_record_label({}, label) == label
