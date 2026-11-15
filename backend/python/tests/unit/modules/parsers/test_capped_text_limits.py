"""The capped text stays within its limit for any input, and its cuts never split a character."""
from __future__ import annotations

import codecs

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.parsers.text.capped_text import (
    CappedText,
    minimum_cap,
    prepare_capped_text,
)


def test_a_cap_too_small_for_the_marker_is_refused() -> None:
    with pytest.raises(ValueError, match="too small"):
        prepare_capped_text(b"x" * 1000, max_bytes=64)


@pytest.mark.parametrize("total", [1_000, 10**6])
def test_the_smallest_cap_holds_for_any_size(total: int) -> None:
    cap = minimum_cap()
    result = prepare_capped_text(b"x" * total, max_bytes=cap)
    assert isinstance(result, CappedText)
    assert len(result.text.encode("utf-8")) <= cap


@pytest.mark.parametrize(
    ("bom", "codec"),
    [(codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"), (b"", "utf-8")],
)
def test_a_cut_next_to_an_astral_character_keeps_it_whole(bom: bytes, codec: str) -> None:
    unit = 2 if codec != "utf-8" else 1
    head = (262_144 // 2 - len(bom)) // unit - 1
    content = bom + ("a" * head + "😀" + "b" * 200_000 + "😀" + "c" * 10).encode(codec)
    result = prepare_capped_text(content)
    assert isinstance(result, CappedText) and result.truncated
    assert "�" not in result.text


# No line breaks, so the cuts fall at a byte offset rather than after a line.
_ASTRAL = st.sampled_from(["😀", "𝄞", "𠀋", "🇯🇵", "a", "é", "日", " "])


@settings(max_examples=300, deadline=None)
@given(
    parts=st.lists(_ASTRAL, min_size=1, max_size=40),
    repeat=st.integers(min_value=50, max_value=400),
    cap=st.integers(min_value=0, max_value=3000),
    form=st.sampled_from(["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"]),
)
def test_no_cut_splits_a_character(parts: list[str], repeat: int, cap: int, form: str) -> None:
    text = "".join(parts) * repeat
    max_bytes = minimum_cap() + cap
    result = prepare_capped_text(text.encode(form), max_bytes=max_bytes, refuse_binary=False)
    assert isinstance(result, CappedText)
    assert "�" not in result.text
    assert len(result.text.encode("utf-8")) <= max_bytes


@settings(max_examples=400, deadline=None)
@given(
    text=st.lists(_ASTRAL, min_size=0, max_size=60).map("".join),
    form=st.sampled_from(["utf-8", "utf-16-le", "utf-16-be"]),
    odd_byte=st.booleans(),
    data=st.data(),
)
def test_a_cut_at_any_position_up_to_the_end_stays_in_bounds(
    text: str, form: str, odd_byte: bool, data: st.DataObject
) -> None:
    """Moving a cut to a character boundary never reads past the end, whichever position the
    head or tail search returns, including the last byte of an odd-length buffer."""
    from app.modules.parsers.text.capped_text import _aligned

    content = text.encode(form) + (b"x" if odd_byte else b"")
    position = data.draw(st.integers(min_value=0, max_value=len(content)))
    for forward in (True, False):
        moved = _aligned(content, position, form, 0, forward=forward)
        assert 0 <= moved <= len(content)


@pytest.mark.parametrize("cap", [1024, 4096, 65536, 131072])
@pytest.mark.parametrize("form", ["8-bit", "utf-16-le odd length"])
def test_a_tail_that_starts_on_the_last_byte_is_capped(cap: int, form: str) -> None:
    """The tail search finds its line break one byte before the end of the file: a last line
    longer than half the cap, a line break, then one byte (a stray continuation byte in 8-bit
    text; the odd byte of a UTF-16 buffer)."""
    if form == "8-bit":
        content = b"a" * (cap * 4) + b"\n" + b"b" * (cap // 2 + 10) + b"\n\xa9"
    else:
        content = ("a" * (cap * 2) + "\n" + "b" * (cap // 4 + 10) + "\n").encode("utf-16-le") + b"x"
    result = prepare_capped_text(content, max_bytes=cap)
    assert len(result.text.encode("utf-8")) <= cap
