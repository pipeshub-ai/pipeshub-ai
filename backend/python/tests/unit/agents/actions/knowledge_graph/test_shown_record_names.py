"""``shown_record_names``: a result may list one record more than once, and it is named once."""
from __future__ import annotations

from app.agents.actions.knowledge_graph.ops.results import shown_record_names


def _block(record_id: str, name: str) -> str:
    return f"Record ID : {record_id}\nName : {name}\nBlock: text\n"


def test_a_double_listing_is_named_once_in_order() -> None:
    text = _block("r1", "Alpha") + _block("r2", "Beta") + _block("r1", "Alpha") + _block("r3", "Gamma")
    assert shown_record_names(text) == ["Alpha", "Beta", "Gamma"]


def test_a_name_with_no_record_id_before_it_is_kept_as_it_is() -> None:
    assert shown_record_names("Name : Legacy\nName : Legacy\n") == ["Legacy", "Legacy"]


def test_two_records_that_share_a_name_are_both_named() -> None:
    assert shown_record_names(_block("r1", "Same") + _block("r2", "Same")) == ["Same", "Same"]


def test_a_repeat_of_an_id_does_not_swallow_the_next_record_without_one() -> None:
    text = _block("r1", "Alpha") + "Record ID : r1\nName : Alpha\nName : Loose\n"
    assert shown_record_names(text) == ["Alpha", "Loose"]


def test_empty_text_names_nothing() -> None:
    assert shown_record_names("") == []
