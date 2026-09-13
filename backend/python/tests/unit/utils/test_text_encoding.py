"""The fallback order for undeclared text encodings."""

import pytest

from app.utils.text_encoding import TEXT_FILE_ENCODINGS


def _decode(data: bytes) -> str:
    for encoding in TEXT_FILE_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("no encoding accepted the bytes")


@pytest.mark.parametrize(
    ("data", "text"),
    [
        ("Caf\u00e9 \u2713".encode(), "Caf\u00e9 \u2713"),
        ("\ufeffName,Age".encode(), "Name,Age"),
        ("\u201cquoted\u201d \u2013 \u20ac5".encode("cp1252"), "\u201cquoted\u201d \u2013 \u20ac5"),
        ("Caf\u00e9".encode("latin-1"), "Caf\u00e9"),
    ],
    ids=["utf-8", "byte-order-mark", "windows-1252", "latin-1"],
)
def test_text_decodes_as_its_writer_meant(data: bytes, text: str) -> None:
    assert _decode(data) == text


def test_bytes_windows_1252_leaves_undefined_still_decode() -> None:
    assert _decode(bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])) == "\x81\x8d\x8f\x90\x9d"
