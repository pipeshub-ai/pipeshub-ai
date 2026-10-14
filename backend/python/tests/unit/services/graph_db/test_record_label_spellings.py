"""How a record's own spelling of a taxonomy node is read from its edge."""

from app.services.graph_db.taxonomy import (
    TaxonomyLink,
    own_record_labels,
    record_spelling,
)


class TestRecordSpelling:
    def test_the_edge_spelling_wins_and_is_cleaned(self) -> None:
        assert record_spelling("Project Falcon", ' "Launch plan". ', canonical=True, migrated=False) == "Launch plan"

    def test_a_canonical_node_without_the_edge_spelling_has_none(self) -> None:
        assert record_spelling("Project Falcon", None, canonical=True, migrated=False) is None
        assert record_spelling("Project Falcon", "  ", canonical=True, migrated=False) is None

    def test_a_legacy_node_and_a_migrated_edge_use_the_node_name(self) -> None:
        assert record_spelling("Legacy topic", None, canonical=False, migrated=False) == "Legacy topic"
        assert record_spelling("Legacy topic", None, canonical=True, migrated=True) == "Legacy topic"

    def test_link_rows_outside_the_taxonomy_are_ignored(self) -> None:
        assert TaxonomyLink.from_row({"recordId": "r", "entityId": "d", "collection": "departments"}) is None


class TestOwnRecordLabels:
    def test_items_carry_the_records_spelling_and_unknown_ones_are_left_out(self) -> None:
        shown = own_record_labels({
            "departments": [{"id": "d1", "name": "Engineering"}],
            "categories": [{"id": "c1", "name": "Codename programme", "extractedName": "Product programme",
                            "canonical": True, "migrated": False}],
            "subcategories1": [], "subcategories2": [], "subcategories3": [],
            "topics": [
                {"id": "t1", "name": "Falcon window", "extractedName": "Launch window", "canonical": True},
                {"id": "t2", "name": "Copied topic", "extractedName": None, "canonical": True},
                {"id": "t3", "name": "Legacy topic", "canonical": False},
            ],
            "languages": [{"id": "l1", "name": "English", "extractedName": "English", "canonical": True}],
        })
        assert shown == {
            "departments": [{"id": "d1", "name": "Engineering"}],
            "categories": [{"id": "c1", "name": "Product programme"}],
            "subcategories1": [], "subcategories2": [], "subcategories3": [],
            "topics": [{"id": "t1", "name": "Launch window"}, {"id": "t3", "name": "Legacy topic"}],
            "languages": [{"id": "l1", "name": "English"}],
        }

    def test_a_missing_read_stays_missing(self) -> None:
        assert own_record_labels(None) is None


class TestEverySpelling:
    def test_a_link_with_two_spellings_yields_both(self) -> None:
        link = TaxonomyLink.from_row({
            "recordId": "r", "collection": "topics", "entityId": "t", "name": "NDA",
            "canonical": True, "extractedName": "NDA",
            "extractedNames": ["NDA", "Non-disclosure agreement"], "migrated": False,
        })
        assert link.spellings == ("NDA", "Non-disclosure agreement")
        assert link.spelling == "NDA"

    def test_record_details_list_every_spelling(self) -> None:
        shown = own_record_labels({"topics": [{
            "id": "t", "name": "NDA", "extractedName": "NDA",
            "extractedNames": ["NDA", "Non-disclosure agreement"], "canonical": True,
        }]})
        assert shown["topics"] == [
            {"id": "t", "name": "NDA"}, {"id": "t", "name": "Non-disclosure agreement"},
        ]
