"""A SQL table's linked tables are named to the model with it, so the answer's label names them as shown
(`chat_helpers.linked_tables_shown`), under every key a fetched or found table carries them."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.utils.chat_helpers import _fk_lines_text, linked_tables_shown


def _fk(record_id: str, **fields: str) -> dict[str, str]:
    return {"record_id": record_id, "sourceColumn": "a", "targetColumn": "b", **fields}


@pytest.mark.parametrize(
    "key",
    [
        "fk_parent_record_ids", "fk_child_record_ids", "fk_parent_relations", "fk_child_relations",
        "implicit_fk_parent_relations", "implicit_fk_child_relations",
    ],
)
def test_each_key_of_linked_tables_is_noted(key: str) -> None:
    with patch("app.utils.chat_helpers.note_shown") as shown:
        linked_tables_shown({key: [_fk("t-2"), {"no": "id"}, "not a link"]})
    shown.assert_called_once_with(records=["t-2"])


def test_a_table_with_no_links_notes_nothing() -> None:
    with patch("app.utils.chat_helpers.note_shown") as shown:
        linked_tables_shown({"fk_parent_record_ids": [], "concept_context_record_ids": ["t-9"]})
    shown.assert_not_called()


def test_the_tables_a_concept_context_names_are_noted_with_it() -> None:
    with patch("app.utils.chat_helpers.note_shown") as shown:
        linked_tables_shown({"concept_context": "* Shared Business Concepts", "concept_context_record_ids": ["t-9"]})
    shown.assert_called_once_with(records=["t-9"])


def test_the_foreign_key_text_notes_the_tables_it_names() -> None:
    record = {
        "record_type": "SQL_TABLE",
        "fk_parent_record_ids": [_fk("t-parent", parentTable="customers")],
        "fk_child_record_ids": [_fk("t-child", childTable="items")],
    }
    with patch("app.utils.chat_helpers.note_shown") as shown:
        _fk_lines_text(record)
    shown.assert_called_once_with(records=["t-parent", "t-child"])
