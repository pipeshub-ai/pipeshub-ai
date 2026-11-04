"""Fuzzed source for every language: the parser never raises, blocks still tile the
file byte for byte, and qualified names stay unique (T-3).

Hand-written fixtures only cover the shapes someone thought of; a repo holds
half-typed files, merge-conflict markers and stray Unicode.
"""
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.models.blocks import BlockType
from app.modules.parsers.code_parser.lang_config import LANGUAGES
from tests.unit.modules.parsers.code_parser.test_exhaustiveness import (
    parse,
    reconstruct,
)

_EXTENSION = {name: sorted(cfg.extensions)[0] for name, cfg in LANGUAGES.items()}

# Fragments that open and close the constructs every grammar has, so random
# sequences reach nesting, strings and comments rather than plain identifiers.
_TOKENS = [
    "def ", "class ", "function ", "fn ", "func ", "fun ", "struct ", "impl ", "interface ",
    "import ", "from ", "export ", "return ", "if ", "else", "for ", "while ", "new ",
    "public ", "static ", "async ", "await ", "@decorator\n", "<<<<<<< HEAD\n", ">>>>>>>\n",
    "x", "Foo", "bar_baz", "_", "$v", "1", "0x1F", "(", ")", "{", "}", "[", "]", "<", ">",
    ":", ";", ",", ".", "=", "=>", "->", "::", '"', "'", "`", "\\", "#", "//", "/*", "*/",
    "--", " ", "    ", "\t", "\n", "\r\n", "é", "日本", "🙂", "​",
]

_SOURCE = st.one_of(
    st.lists(st.sampled_from(_TOKENS), max_size=80).map("".join),
    st.text(max_size=300),
)


@pytest.mark.parametrize("language", sorted(_EXTENSION))
@settings(max_examples=25, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.too_slow])
@given(source=_SOURCE)
def test_any_source_tiles_and_names_stay_unique(language: str, source: str) -> None:
    raw = source.encode("utf-8")
    container = parse(raw, f"f.{_EXTENSION[language]}")
    assert reconstruct(container).encode() == raw
    names = [b.code_metadata.qualified_name for b in container.blocks
             if b.type is BlockType.CODE and b.code_metadata]
    assert len(names) == len(set(names))


@settings(max_examples=40, deadline=None, derandomize=True)
@given(raw=st.binary(max_size=400))
def test_bytes_that_are_not_utf8_never_raise(raw: bytes) -> None:
    parse(raw, "f.py")
