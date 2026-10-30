"""A document belongs to at most two departments, main one first, and its
topics are reusable subjects, never names."""

from __future__ import annotations

from app.modules.extraction.prompt_template import prompt_for_document_extraction
from app.modules.transformers.document_extraction import (
    MAX_DEPARTMENTS,
    DocumentClassification,
    DocumentExtraction,
    SubCategories,
)


def _classification(departments: list[str]) -> DocumentClassification:
    return DocumentClassification(
        departments=departments, category="c",
        subcategories=SubCategories(level1="a", level2="b", level3="c"),
        languages=["English"], topics=["payment terms"], summary="s",
    )


_LISTED = ["Finance", "Sales", "Operations", "Legal"]


def test_a_third_department_is_cut_not_rejected_and_the_main_one_stays_first() -> None:
    metadata = DocumentExtraction.to_semantic_metadata(
        _classification(["Finance", " Sales ", "Finance", "Operations"]), _LISTED,
    )
    assert metadata.departments == ["Finance", "Sales"] and MAX_DEPARTMENTS == 2


def test_picks_not_on_the_orgs_list_are_dropped_before_the_cut() -> None:
    metadata = DocumentExtraction.to_semantic_metadata(
        _classification(["Customer Support", "finance", "Legal"]), _LISTED,
    )
    assert metadata.departments == ["Finance", "Legal"]


def test_blank_departments_are_dropped() -> None:
    assert _classification(["", "  ", "Legal"]).departments == ["Legal"]


def test_the_schema_no_longer_offers_three_departments() -> None:
    schema = DocumentClassification.model_json_schema()["properties"]["departments"]
    assert "maxItems" not in schema and "At most 2" in schema["description"]


def test_the_prompt_asks_for_at_most_two_departments_and_two_to_five_reusable_topics() -> None:
    assert "**At most 2.**" in prompt_for_document_extraction
    assert "**2 to 5** topics" in prompt_for_document_extraction
    assert "Never" in prompt_for_document_extraction and "reusable subject" in prompt_for_document_extraction
    assert "1 to 3 departments" not in prompt_for_document_extraction



def test_a_listed_name_with_surrounding_spaces_still_matches() -> None:
    metadata = DocumentExtraction.to_semantic_metadata(_classification(["Finance"]), [" Finance "])
    assert metadata.departments == ["Finance"]
