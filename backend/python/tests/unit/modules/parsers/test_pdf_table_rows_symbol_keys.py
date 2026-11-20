"""A band of one-line records whose keys are short value tokens (``0``, ``1``, ``>=2``) is cut into its records."""
from __future__ import annotations

from typing import Any

from app.modules.parsers.pdf.table_rows import band_lines, split_band_records

COLUMNS = [(131.0, 166.0), (166.0, 215.0), (215.0, 464.0)]


def _w(text: str, x0: float, top: float) -> dict[str, Any]:
    return {"text": text, "x0": x0, "x1": x0 + 5.0 * len(text), "top": top, "bottom": top + 9.0}


def _band(third_key: str) -> list[dict[str, Any]]:
    words = [
        _w("0", 158, 509), _w("Direct", 179, 509), _w("All", 218, 509), _w("traps", 231, 509), _w("set", 252, 509), _w("pc", 265, 509),
        _w("1", 161, 520), _w("Vectored", 174, 520), _w("Asynchronous", 218, 520), _w("interrupts", 275, 520), _w("set", 316, 520),
        _w(third_key, 154, 533), _w("---", 186, 533), _w("Reserved", 218, 533),
    ]
    return band_lines(words)


def test_keys_with_a_comparison_sign_are_records_like_plain_digits() -> None:
    for key in ("≥2", ">=2", "<=2", "2"):
        assert [len(g) for g in split_band_records(_band(key), COLUMNS, ruled_cell=True)] == [1, 1, 1], key


def test_a_value_list_in_one_cell_is_still_one_record() -> None:
    """The key cell holds the three values; no other column has a line beside each of them."""
    words = [_w("0", 158, 509), _w("1", 161, 520), _w("≥2", 154, 533), _w("Direct", 179, 509), _w("All", 218, 509), _w("traps", 231, 509)]
    assert len(split_band_records(band_lines(words), COLUMNS, ruled_cell=True)) == 1
