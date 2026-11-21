"""Unit tests for ``collect_reachable_table_refs``.

The function decides which Docling tables are enriched before the document
walk: it follows ``$ref`` links from ``body.children`` depth-first in body
order and keeps only reachable ``tables`` refs (deduped).
"""

from __future__ import annotations

from app.utils.converters.docling_doc_to_blocks import collect_reachable_table_refs


def test_empty_doc_returns_empty_list() -> None:
    assert collect_reachable_table_refs({}) == []


def test_unreferenced_table_is_not_returned() -> None:
    doc = {
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [{"text": "hi"}],
        "tables": [{}],
    }
    assert collect_reachable_table_refs(doc) == []


def test_depth_first_body_order() -> None:
    doc = {
        "body": {
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/groups/0"}],
        },
        "texts": [{"text": "hi", "children": [{"$ref": "#/tables/1"}]}],
        "groups": [{"children": [{"$ref": "#/tables/0"}]}],
        "tables": [{}, {}],
    }
    assert collect_reachable_table_refs(doc) == ["#/tables/1", "#/tables/0"]


def test_duplicate_ref_visited_once() -> None:
    doc = {
        "body": {"children": [{"$ref": "#/tables/0"}, {"$ref": "#/tables/0"}]},
        "tables": [{}],
    }
    assert collect_reachable_table_refs(doc) == ["#/tables/0"]


def test_string_child_is_followed() -> None:
    doc = {
        "body": {"children": ["#/tables/0"]},
        "tables": [{}],
    }
    assert collect_reachable_table_refs(doc) == ["#/tables/0"]


def test_unresolvable_refs_return_empty() -> None:
    doc = {
        "body": {
            "children": [
                {},
                {"$ref": "#/nope"},
                {"$ref": "#/tables/x"},
                {"$ref": "#/tables/9"},
                {"$ref": "#/pages/0"},
            ],
        },
        "tables": [{}],
        "pages": [{}],
    }
    assert collect_reachable_table_refs(doc) == []


def test_self_referencing_group_terminates() -> None:
    doc = {
        "body": {"children": [{"$ref": "#/groups/0"}]},
        "groups": [{"children": [{"$ref": "#/groups/0"}]}],
        "tables": [{}],
    }
    assert collect_reachable_table_refs(doc) == []
