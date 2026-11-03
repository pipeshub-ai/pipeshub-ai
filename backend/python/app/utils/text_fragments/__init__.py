"""Text fragment (`#:~:text=`) URL generation and parsing. See docs/text-fragments.md."""

from app.utils.text_fragments.codec import (
    decode_term,
    encode_term,
    parse_fragment_directive,
    parse_text_directive,
    serialize,
)
from app.utils.text_fragments.facade import build_text_fragment_url
from app.utils.text_fragments.generator import TextFragmentGenerator
from app.utils.text_fragments.models import (
    SourceFormat,
    TextDirective,
    TextFragmentConfig,
)
from app.utils.text_fragments.url import (
    FRAGMENT_DIRECTIVE_DELIMITER,
    TEXT_FRAGMENT_DIRECTIVE_PREFIX,
    append_directive,
    has_fragment_directive,
    split_fragment_directive,
    strip_fragment_directive,
)

__all__ = [
    "FRAGMENT_DIRECTIVE_DELIMITER",
    "TEXT_FRAGMENT_DIRECTIVE_PREFIX",
    "SourceFormat",
    "TextDirective",
    "TextFragmentConfig",
    "TextFragmentGenerator",
    "append_directive",
    "build_text_fragment_url",
    "decode_term",
    "encode_term",
    "has_fragment_directive",
    "parse_fragment_directive",
    "parse_text_directive",
    "serialize",
    "split_fragment_directive",
    "strip_fragment_directive",
]
