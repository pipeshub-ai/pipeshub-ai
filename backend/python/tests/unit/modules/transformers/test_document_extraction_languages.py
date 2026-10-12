"""A document is classified by the natural languages it is written in."""

from __future__ import annotations

from app.modules.transformers.document_extraction import (
    DocumentClassification,
    SubCategories,
    semantic_metadata_from_classification,
)


def _classification(languages: list[str]) -> DocumentClassification:
    return DocumentClassification(
        departments=["Engineering/Technology"], category="c",
        subcategories=SubCategories(level1="a", level2="b", level3="c"),
        languages=languages, topics=["t"], summary="s",
    )


def test_classification_keeps_natural_languages_only_canonical_and_once() -> None:
    assert _classification(["Python", "english", "en", "JSON", "Français"]).languages == ["English", "French"]


def test_a_record_classified_only_by_code_and_formats_has_no_language() -> None:
    # Detecting a fallback from the text called Kubernetes and Helm manifests English.
    assert semantic_metadata_from_classification(_classification(["YAML", "Go"])).languages == []
    assert semantic_metadata_from_classification(_classification([])).languages == []


def test_semantic_metadata_carries_the_classification() -> None:
    metadata = semantic_metadata_from_classification(_classification(["German", "JSON"]))
    assert metadata.languages == ["German"]
    assert metadata.categories == ["c"]
    assert metadata.sub_category_level_3 == "c"
