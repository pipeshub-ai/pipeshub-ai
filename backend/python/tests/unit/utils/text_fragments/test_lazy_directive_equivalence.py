"""`build_directive` and `extract` give the same output as the eager pipeline they replaced.

The reference below is the previous implementation: parse every markdown
token, normalize and segment every block, then hand all eligible blocks to the
strategy. The generator now parses and segments only the first and last
eligible block, which must not change a single directive.
"""

import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from markdown_it import MarkdownIt

from app.utils.text_fragments import SourceFormat, TextDirective, TextFragmentGenerator
from app.utils.text_fragments.extractors import MarkdownBlockExtractor
from app.utils.text_fragments.segmentation import (
    _ALNUM,
    _UNSPACED_CHAR,
    SegmentedBlock,
    Unit,
    WordSegmenter,
    _runs_of_word_chars,
    is_unspaced_script,
    is_word_char,
)
from tests.support.text_fragment_corpus import load_cases

generator = TextFragmentGenerator()
_eager_parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])


def _eager_markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for token in _eager_parser.parse(text):
        if token.type == "inline":
            blocks.extend(MarkdownBlockExtractor._inline_blocks(token.children or []))
        elif token.type in ("fence", "code_block"):
            blocks.extend(line for line in token.content.splitlines() if line.strip())
        elif token.type == "html_block":
            blocks.extend(generator._registry.get(SourceFormat.HTML).extract(token.content))
    return [block for block in blocks if block.strip()]


def _eager_units_of_token(token: str, offset: int) -> list[Unit]:
    if any(is_unspaced_script(ch) for ch in token):
        return _runs_of_word_chars(token, offset)
    first = next((i for i, ch in enumerate(token) if is_word_char(ch)), None)
    if first is None:
        return []
    last = max(i for i, ch in enumerate(token) if is_word_char(ch))
    return [Unit(offset + first, offset + last + 1, unspaced=False)]


def _eager_segment(block: str) -> SegmentedBlock | None:
    units: list[Unit] = []
    position = 0
    for token in block.split(" "):
        token_start = position
        position += len(token) + 1
        if token:
            units.extend(_eager_units_of_token(token, token_start))
    if not units:
        return None
    return SegmentedBlock(text=block, units=tuple(units), alnum_count=sum(1 for ch in block if ch.isalnum()))


def _eager_directive(snippet: str, source_format: SourceFormat | None) -> TextDirective | None:
    if (source_format or SourceFormat.MARKDOWN) is SourceFormat.MARKDOWN:
        raw = _eager_markdown_blocks(snippet)
    else:
        raw = generator._registry.get(source_format).extract(snippet)
    segmenter: WordSegmenter = generator._segmenter
    segmented = []
    for block in (generator._normalizer.normalize(b) for b in raw):
        candidate = _eager_segment(block) if block else None
        if candidate is not None and segmenter.is_eligible(candidate):
            segmented.append(candidate)
    return generator._strategy.build(segmented) if segmented else None


def _assert_same(snippet: str, source_format: SourceFormat | None) -> None:
    assert generator.build_directive(snippet, source_format) == _eager_directive(snippet, source_format)
    if (source_format or SourceFormat.MARKDOWN) is SourceFormat.MARKDOWN:
        assert MarkdownBlockExtractor().extract(snippet) == _eager_markdown_blocks(snippet)


_FORMATS = [None, SourceFormat.MARKDOWN, SourceFormat.HTML, SourceFormat.PLAIN]

_CASES = [
    "",
    "   \n\n  ",
    "a b",
    "Quarterly revenue grew",
    "# Heading words here\n\nBody paragraph that goes on for quite a few more words than eight",
    "!!\n\nonly one real block here\n\n--\n\n..",
    "first block words\n\n  \n\n&nbsp;\n\nlast block words here",
    "Escaped \\*stars\\* and &amp; entities &copy; in text\n\nsecond &lt;para&gt; \\_x\\_",
    "See [the docs][ref] and ![img][ref]\n\nthen more text\n\n[ref]: https://example.com/x",
    "| a | b |\n|---|---|\n| cell one | cell two |\n| ~~gone~~ text | `code` here |",
    "```python\nprint('x')\n\n\nvalue = 1\n```\n\nafter the fence words",
    "    indented code line\n    second line\n\nprose after code",
    "<div><p>html block text</p><img src=x><p>after image</p></div>\n\ntrailing prose words",
    "line one  \nline two with hard break\\\nline three",
    "- item one words\n- item two words\n  - nested item\n\n1. ordered thing\n2. another thing",
    "> quoted words here\n> more quote\n\nSetext Heading\n===\n\n---\n\nend",
    "日本語のテキストです。これは二番目の文です。\n\n東京は大きな都市です。",
    "été café ​zero­soft naïve\n\nZürich … München",
    "x" * 5 + " " + "_" * 3 + " snake_case_word __init__ a-b-c",
    "first ... second … third\r\nfourth fifth",
]


def test_explicit_cases_match_the_eager_pipeline() -> None:
    for snippet in _CASES:
        for source_format in _FORMATS:
            _assert_same(snippet, source_format)


def test_golden_corpus_matches_the_eager_pipeline() -> None:
    for case in load_cases():
        _assert_same(case.snippet, case.format)


_SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
_text = st.lists(
    st.one_of(
        st.characters(blacklist_categories=("Cs",)),
        st.sampled_from(list("-,&%()*_#[]<>`~|!\\:; \t\u00a0\u200b\u2026")),
        st.sampled_from(["\n", "\n\n", "**", "~~", "&amp;", "[a](b)", "\n- ", "\n| x |", "\n```\n", "<p>", "  \n"]),
    ),
    max_size=200,
).map("".join)
_words = st.lists(
    st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Lo", "Mn")), min_size=1, max_size=9),
    min_size=1,
    max_size=60,
)
_documents = st.lists(
    st.tuples(st.sampled_from(["", "# ", "- ", "> ", "**", "1. ", "| "]), _words.map(" ".join)),
    min_size=1,
    max_size=8,
).map(lambda parts: "\n\n".join(prefix + body for prefix, body in parts))


@_SETTINGS
@given(_text, st.sampled_from(_FORMATS))
def test_arbitrary_text_matches_the_eager_pipeline(snippet: str, source_format: SourceFormat | None) -> None:
    _assert_same(snippet, source_format)


@_SETTINGS
@given(_documents, st.sampled_from(_FORMATS))
def test_multi_block_documents_match_the_eager_pipeline(snippet: str, source_format: SourceFormat | None) -> None:
    _assert_same(snippet, source_format)


@_SETTINGS
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200))
def test_segment_matches_the_eager_segmenter(block: str) -> None:
    assert generator._segmenter.segment(block) == _eager_segment(block)


def test_character_classes_match_the_predicates_they_replace() -> None:
    every_char = "".join(chr(code) for code in range(sys.maxunicode + 1) if not 0xD800 <= code <= 0xDFFF)
    assert "".join(_ALNUM.findall(every_char)) == "".join(ch for ch in every_char if ch.isalnum())
    assert "".join(_UNSPACED_CHAR.findall(every_char)) == "".join(ch for ch in every_char if is_unspaced_script(ch))
