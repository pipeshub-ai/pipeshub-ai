"""Property tests: for any input the generator never raises and emits only well-formed, matchable terms."""

import unicodedata

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.utils.text_fragments import SourceFormat, TextDirective, TextFragmentGenerator
from app.utils.text_fragments.codec import (
    decode_term,
    encode_term,
    parse_fragment_directive,
)
from app.utils.text_fragments.segmentation import is_word_char
from app.utils.text_fragments.url import split_fragment_directive

BASE = "https://example.com/doc"
generator = TextFragmentGenerator()

_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_text = st.text(
    alphabet=st.one_of(
        st.characters(blacklist_categories=("Cs",)),
        st.sampled_from(list("-,&%()*_#[]<>`~|\n\t \u00a0\u200b\u2026")),
    ),
    max_size=400,
)
_words = st.lists(
    st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=9),
    min_size=3,
    max_size=40,
).map(" ".join)
_formats = st.sampled_from([None, SourceFormat.MARKDOWN, SourceFormat.HTML, SourceFormat.PLAIN])


def _directives(url: str) -> list[TextDirective]:
    _, raw = split_fragment_directive(url)
    return parse_fragment_directive(raw) if raw else []


def _on_word_boundaries(term: str, block: str) -> bool:
    """True when `term` occurs in `block` with a non-word character (or an edge) on each side."""
    index = block.find(term)
    while index >= 0:
        before = block[index - 1] if index > 0 else ""
        after_at = index + len(term)
        after = block[after_at] if after_at < len(block) else ""
        spaced_edges = not (before and is_word_char(before) and is_word_char(term[0])) and not (
            after and is_word_char(after) and is_word_char(term[-1])
        )
        if spaced_edges:
            return True
        index = block.find(term, index + 1)
    return False


@_SETTINGS
@given(text=_text, fmt=_formats)
def test_generation_never_raises_and_url_is_ascii(text: str, fmt: SourceFormat | None) -> None:
    url = generator.build_url(BASE, text, fmt)
    assert url.startswith(BASE)
    assert url.isascii()


@_SETTINGS
@given(text=st.one_of(_text, _words), fmt=_formats)
def test_url_parses_back_into_exactly_one_directive(text: str, fmt: SourceFormat | None) -> None:
    url = generator.build_url(BASE, text, fmt)
    if url == BASE:
        return
    directives = _directives(url)
    assert len(directives) == 1
    assert directives[0].prefix is None and directives[0].suffix is None


@_SETTINGS
@given(text=st.one_of(_text, _words), fmt=_formats)
def test_every_term_occurs_in_one_rendered_block_on_word_boundaries(
    text: str, fmt: SourceFormat | None
) -> None:
    url = generator.build_url(BASE, text, fmt)
    if url == BASE:
        return
    (directive,) = _directives(url)
    blocks = [unicodedata.normalize("NFC", b) for b in generator.rendered_blocks(text, fmt)]
    for term in (directive.start, directive.end):
        if term is None:
            continue
        assert any(_on_word_boundaries(term, block) for block in blocks), (term, blocks)


@_SETTINGS
@given(text=_words)
def test_range_end_follows_start(text: str) -> None:
    url = generator.build_url(BASE, text, SourceFormat.PLAIN)
    if url == BASE:
        return
    (directive,) = _directives(url)
    if directive.end is None:
        return
    (block,) = generator.rendered_blocks(text, SourceFormat.PLAIN)
    start_at = block.find(directive.start)
    end_at = block.rfind(directive.end)
    assert 0 <= start_at < end_at
    assert start_at + len(directive.start) <= end_at


@_SETTINGS
@given(term=st.text(max_size=60).filter(lambda t: t and "\ud800" not in t))
def test_term_encoding_round_trips_and_has_no_raw_delimiters(term: str) -> None:
    try:
        term.encode("utf-8")
    except UnicodeEncodeError:
        return
    encoded = encode_term(term)
    assert encoded.isascii()
    assert not any(ch in encoded for ch in "-,&")
    assert decode_term(encoded) == term
