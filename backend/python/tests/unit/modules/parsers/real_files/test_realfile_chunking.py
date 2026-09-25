"""Splitting text into blocks and sentences never drops or reorders words."""

from __future__ import annotations

import random
import re
from unittest.mock import MagicMock

import pytest

from app.config.constants.ai_models import OCRProvider
from app.models.blocks import BlocksContainer, BlockType
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.text_splitting import split_into_sentences, split_long_text
from app.services.parsing.providers.ocr_parser import OCRParser

from .samples import all_text


def _words(text: str) -> list[str]:
    return text.split()


def _random_text(rng: random.Random, n_words: int) -> str:
    pieces = []
    for i in range(n_words):
        word = "".join(rng.choice("abcdefghij") for _ in range(rng.randint(1, 12))) + str(i)
        pieces.append(word)
        roll = rng.random()
        if roll < 0.08:
            pieces.append(rng.choice([".", "!", "?", "..."]))
        pieces.append(rng.choice([" ", " ", " ", "\n", "  ", "\n\n", "\t"]))
    return "".join(pieces)


class TestSplitLongText:
    @pytest.mark.parametrize("seed", range(12))
    @pytest.mark.parametrize("max_chars", [40, 500, 5_000])
    def test_chunks_fit_and_keep_every_character_in_order(self, seed: int, max_chars: int) -> None:
        text = _random_text(random.Random(seed), 3_000)
        chunks = split_long_text(text, max_chars=max_chars)
        assert all(len(c) <= max_chars for c in chunks)
        assert re.sub(r"\s", "", "".join(chunks)) == re.sub(r"\s", "", text)

    @pytest.mark.parametrize("seed", range(6))
    def test_long_run_on_sentence_is_split_between_words(self, seed: int) -> None:
        # Transcripts, logs and OCR output often have no sentence punctuation
        # for thousands of characters.
        rng = random.Random(seed)
        words = [f"{''.join(rng.choice('klmnop') for _ in range(rng.randint(2, 15)))}{i}" for i in range(4_000)]
        text = " ".join(words)
        chunks = split_long_text(text, max_chars=997)
        assert len(chunks) > 1
        assert all(len(c) <= 997 for c in chunks)
        assert [w for c in chunks for w in c.split()] == words

    def test_one_giant_sentence_is_hard_split_without_loss(self) -> None:
        text = "x" * 125_001
        chunks = split_long_text(text, max_chars=50_000)
        assert [len(c) for c in chunks] == [50_000, 50_000, 25_001]
        assert "".join(chunks) == text

    @pytest.mark.parametrize("max_chars", [0, -5])
    def test_non_positive_limit_is_rejected(self, max_chars: int) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            split_long_text("some text that needs splitting", max_chars=max_chars)

    @pytest.mark.parametrize(
        ("text", "max_chars"),
        [("hello  world", 5), ("alpha \t\n beta  gamma", 5), ("  lead and trail  ", 5)],
    )
    def test_runs_of_whitespace_never_split_a_word(self, text: str, max_chars: int) -> None:
        chunks = split_long_text(text, max_chars=max_chars)
        assert all(len(c) <= max_chars for c in chunks)
        assert [w for c in chunks for w in c.split()] == text.split()

    def test_short_and_empty_text(self) -> None:
        assert split_long_text("") == []
        assert split_long_text("short.") == ["short."]


class TestSentences:
    @pytest.mark.parametrize(
        ("text", "language"),
        [
            ("Dr. Smith arrived at 5 p.m. and left... Then he said 'Hi!' Visit https://x.com/a.b?c=d. Done", "en"),
            ("Item 1.\n\n2. Second item\n- bullet one\n- bullet two\nEnd", "en"),
            ("価格は100円です。次の文。最後！", "ja"),
            ("Das ist z.B. ein Satz. Noch einer! Und dann?", "de"),
            ("Première phrase. Deuxième phrase ? Oui !", "fr"),
        ],
    )
    def test_sentences_keep_every_character(self, text: str, language: str) -> None:
        sentences = split_into_sentences(text, language)
        assert len(sentences) >= 2
        assert re.sub(r"\s", "", "".join(sentences)) == re.sub(r"\s", "", text)


class TestMarkdownBlocks:
    async def test_long_mixed_document_keeps_every_word(self) -> None:
        rng = random.Random(7)
        sections = []
        for s in range(30):
            sections.append(f"## Section {s}\n\n{_random_text(rng, 80).replace('#', '')}\n")
            sections.append(f"- first point {s}\n- second point {s}\n")
            sections.append(f"| key | value |\n|---|---|\n| k{s} | v{s} |\n")
        md = "\n".join(sections)
        container = (await MarkdownItParser().parse(md.encode(), "long.md")).block_container
        text = all_text(container)
        for s in range(30):
            assert f"Section {s}" in text
            assert f"first point {s}" in text and f"second point {s}" in text
            assert f"key: k{s}, value: v{s}" in text
        body_words = [w for w in _words(md) if re.fullmatch(r"[a-j]+\d+[.!?]*", w)]
        assert body_words
        text_words = set(_words(text))
        assert [w for w in body_words if w not in text_words] == []


class _FakeOcrHandler:
    """Stands in for the vision model: returns fixed per-page Markdown."""

    provider = OCRProvider.VLM_OCR.value

    def __init__(self, pages: list[str]) -> None:
        self._pages = pages

    async def process_document(self, content: bytes) -> dict:
        return {"pages": [{"page_number": i + 1, "markdown": md} for i, md in enumerate(self._pages)]}


def _assert_indexes_consistent(container: BlocksContainer) -> None:
    assert [b.index for b in container.blocks] == list(range(len(container.blocks)))
    assert [g.index for g in container.block_groups] == list(range(len(container.block_groups)))
    for block in container.blocks:
        if block.parent_index is not None:
            assert 0 <= block.parent_index < len(container.block_groups)
    for group in container.block_groups:
        if group.children:
            for r in group.children.block_ranges:
                for i in range(r.start, r.end + 1):
                    assert container.blocks[i].parent_index == group.index


class TestOcrPagesMerge:
    async def test_pages_are_merged_in_order_with_consistent_links(self) -> None:
        pages = [
            "# Page one\n\nIntro text.\n\n- a1\n- a2\n\n| h | v |\n|---|---|\n| x1 | y1 |\n",
            "   \n",
            "Second page body.\n\n- b1\n\n| h | v |\n|---|---|\n| x3 | y3 |\n| x4 | y4 |\n",
        ]
        parser = OCRParser(_FakeOcrHandler(pages), MarkdownItParser())
        result = await parser.parse(b"%PDF-", "scan.pdf")
        container = result.block_container

        text = all_text(container)
        for expected in ("Page one", "Intro text.", "a1", "a2", "h: x1, v: y1",
                         "Second page body.", "b1", "h: x3, v: y3", "h: x4, v: y4"):
            assert expected in text
        assert text.index("a2") < text.index("Second page body.")
        _assert_indexes_consistent(container)
        pages_seen = {b.citation_metadata.page_number for b in container.blocks}
        assert pages_seen == {1, 3}
        rows = [b for b in container.blocks if b.type == BlockType.TABLE_ROW]
        assert [r.citation_metadata.page_number for r in rows] == [1, 3, 3]

    async def test_unknown_ocr_provider_is_a_clear_error(self) -> None:
        handler = MagicMock()
        handler.provider = "some-other-ocr"
        handler.process_document = _FakeOcrHandler(["x"]).process_document
        from app.services.parsing.interface import ParseError, ParseErrorCode

        with pytest.raises(ParseError) as caught:
            await OCRParser(handler, MarkdownItParser()).parse(b"%PDF-", "scan.pdf")
        assert caught.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE
