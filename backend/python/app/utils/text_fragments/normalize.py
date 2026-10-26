from __future__ import annotations

import unicodedata

# Characters that render as nothing but would otherwise end up inside a term.
# ZWJ/ZWNJ are kept: they change how Persian and Indic text is shaped.
_INVISIBLE = dict.fromkeys(
    [
        0x00AD,  # soft hyphen
        0x200B,  # zero width space
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        *range(0x202A, 0x202F),  # bidi embeddings and overrides
        0x2060,  # word joiner
        *range(0x2066, 0x206A),  # bidi isolates
        0xFEFF,  # BOM / zero width no-break space
    ]
)


class TextNormalizer:
    """Brings text to the form a browser's find-in-page sees: NFC, plain spaces, no invisibles."""

    def normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
        # str.split() with no argument splits on every Unicode whitespace
        # (NBSP, thin space, line separators), collapsing runs.
        return " ".join(text.split())
