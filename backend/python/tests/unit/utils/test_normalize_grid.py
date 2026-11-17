"""Unit tests for table_enrichment.normalize_grid."""

from app.utils.table_enrichment import normalize_grid


def test_mixed_row_with_dict_none_and_scalar() -> None:
    """Test mixed row containing dict with text, None, and integer scalar."""
    raw_grid = [[{"text": "A"}, None, 3]]
    expected = [["A", "", "3"]]
    assert normalize_grid(raw_grid) == expected


def test_dict_cells_empty_and_none_text() -> None:
    """Test dict cells with empty string, None text, and empty dict."""
    raw_grid = [[{"text": ""}, {"text": None}, {}]]
    expected = [["", "", ""]]
    assert normalize_grid(raw_grid) == expected


def test_dict_with_falsy_zero_is_empty() -> None:
    """Test dict cell where 'text' is integer 0, which normalizes to empty string."""
    raw_grid = [[{"text": 0}]]
    expected = [[""]]
    assert normalize_grid(raw_grid) == expected


def test_dict_with_falsy_false_is_empty() -> None:
    """Test dict cell where 'text' is boolean False, which normalizes to empty string."""
    raw_grid = [[{"text": False}]]
    expected = [[""]]
    assert normalize_grid(raw_grid) == expected


def test_dict_missing_text_key_is_empty() -> None:
    """Test dict cell without a 'text' key."""
    raw_grid = [[{"other": "x"}]]
    expected = [[""]]
    assert normalize_grid(raw_grid) == expected


def test_scalar_zero_and_boolean_stringified() -> None:
    """Test scalar values 0 and False, which stringify to '0' and 'False'."""
    raw_grid = [[0, False]]
    expected = [["0", "False"]]
    assert normalize_grid(raw_grid) == expected


def test_empty_grid() -> None:
    """Test an entirely empty grid."""
    assert normalize_grid([]) == []


def test_grid_with_empty_row() -> None:
    """Test a grid containing a single empty row."""
    assert normalize_grid([[]]) == [[]]
