"""A document is classified by the natural languages it is written in."""

from __future__ import annotations

from app.models.blocks import Block, BlockType, DataFormat
from app.modules.transformers.document_extraction import (
    DocumentClassification,
    SubCategories,
    _written_language,
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


def test_a_record_whose_classification_named_no_language_gets_the_one_it_is_written_in() -> None:
    blocks = [Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="The deployment runbook covers the rollback steps for every region.")]
    assert _written_language(blocks) == ["English"]
    assert _written_language([]) == []


def test_written_language_reads_text_blocks_only() -> None:
    image = Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data="data:image/png;base64,iVBORw0KGgo=")
    assert _written_language([image]) == []


def test_semantic_metadata_falls_back_to_the_written_language() -> None:
    blocks = [Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="Le guide décrit les étapes de restauration pour chaque région du réseau.")]
    assert semantic_metadata_from_classification(_classification(["JSON"]), blocks).languages == ["French"]
    assert semantic_metadata_from_classification(_classification(["German"]), blocks).languages == ["German"]
