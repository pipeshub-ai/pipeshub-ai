import pytest

from app.utils.text_fragments.codec import (
    decode_term,
    encode_term,
    parse_fragment_directive,
    parse_text_directive,
    serialize,
)
from app.utils.text_fragments.models import TextDirective


class TestEncodeTerm:
    @pytest.mark.parametrize(
        ("term", "encoded"),
        [
            ("plain", "plain"),
            ("two words", "two%20words"),
            ("e-mail", "e%2Dmail"),
            ("a,b", "a%2Cb"),
            ("a&b", "a%26b"),
            ("(x)", "%28x%29"),
            ("100%", "100%25"),
            ("Zürich", "Z%C3%BCrich"),
            ("日本語", "%E6%97%A5%E6%9C%AC%E8%AA%9E"),
        ],
    )
    def test_encodes_to_ascii(self, term: str, encoded: str) -> None:
        assert encode_term(term) == encoded
        assert encoded.isascii()

    def test_hyphen_is_never_left_raw(self) -> None:
        assert "-" not in encode_term("a-b--c-")

    def test_round_trip(self) -> None:
        term = "naïve - café, (x) & 日本 100%"
        assert decode_term(encode_term(term)) == term

    def test_decode_tolerates_invalid_utf8(self) -> None:
        assert decode_term("%FF") == "\ufffd"


class TestSerialize:
    def test_start_only(self) -> None:
        assert serialize(TextDirective(start="hello world")) == "text=hello%20world"

    def test_start_and_end(self) -> None:
        assert serialize(TextDirective(start="a b", end="c d")) == "text=a%20b,c%20d"

    def test_context_terms_carry_markers(self) -> None:
        directive = TextDirective(start="s", end="e", prefix="p", suffix="x")
        assert serialize(directive) == "text=p-,s,e,-x"

    def test_hyphenated_term_does_not_look_like_context(self) -> None:
        assert serialize(TextDirective(start="-lead", end="trail-")) == "text=%2Dlead,trail%2D"


class TestParseTextDirective:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("start", TextDirective(start="start")),
            ("start,end", TextDirective(start="start", end="end")),
            ("pre-,start", TextDirective(start="start", prefix="pre")),
            ("start,-suf", TextDirective(start="start", suffix="suf")),
            ("pre-,start,end,-suf", TextDirective(start="start", end="end", prefix="pre", suffix="suf")),
            ("a%20b", TextDirective(start="a b")),
        ],
    )
    def test_valid(self, value: str, expected: TextDirective) -> None:
        assert parse_text_directive(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["", ",", "a,b,c,d,e", "pre-", "-suf", "a-b", "pre-,", "a,,b", "p-,s,e,-x,extra"],
    )
    def test_invalid_returns_none(self, value: str) -> None:
        assert parse_text_directive(value) is None

    def test_encoded_hyphen_is_part_of_the_term(self) -> None:
        assert parse_text_directive("e%2Dmail") == TextDirective(start="e-mail")


class TestParseFragmentDirective:
    def test_parses_multiple_directives(self) -> None:
        parsed = parse_fragment_directive("text=one&text=two,three")
        assert parsed == [TextDirective(start="one"), TextDirective(start="two", end="three")]

    def test_skips_unknown_and_invalid_directives(self) -> None:
        parsed = parse_fragment_directive("unknown=1&text=&text=ok")
        assert parsed == [TextDirective(start="ok")]

    def test_round_trips_serialize(self) -> None:
        directive = TextDirective(start="a-b, c", end="d&e", prefix="p q", suffix="s-t")
        assert parse_fragment_directive(serialize(directive)) == [directive]
