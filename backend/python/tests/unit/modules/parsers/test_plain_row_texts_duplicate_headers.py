"""A repeated or empty header must not make one cell overwrite another in the row text."""

from app.modules.parsers.pdf.pdfplumber_opencv_processor import _plain_row_texts


def test_repeated_and_empty_headers_keep_every_cell() -> None:
    texts = _plain_row_texts(
        ["", "", "", "At", "At"],
        [["Total assets", "", "", "$ 372,168", "$ 377,325"]],
    )
    assert "372,168" in texts[0] and "377,325" in texts[0]
    assert texts[0].count(":") == 5


def test_unique_headers_output_unchanged() -> None:
    assert _plain_row_texts(["Name", "Qty"], [["a", {"text": "2"}]]) == ["Name: a, Qty: 2"]


def test_row_longer_than_headers_names_extra_cells() -> None:
    assert _plain_row_texts(["Name"], [["a", "b"]]) == ["Name: a, Column_2: b"]
