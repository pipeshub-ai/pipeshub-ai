"""Encoding and parsing of text directives, written to WICG spec section 3.4."""

from __future__ import annotations

from urllib.parse import quote, unquote

from app.utils.text_fragments.models import TextDirective

DIRECTIVE_PARAM = "text="


def encode_term(term: str) -> str:
    """Percent-encode one directive term as UTF-8.

    `quote` leaves `-` alone, but an unencoded `-` is a prefix/suffix marker in
    the directive grammar and invalidates the whole directive, so it is encoded
    explicitly. `,` and `&` are already encoded by `quote(safe="")`. Every other
    non-alphanumeric character (including parentheses and spaces) is encoded too,
    which keeps the URL ASCII-only and safe inside markdown links.
    """
    return quote(term, safe="").replace("-", "%2D")


def decode_term(term: str) -> str:
    return unquote(term, encoding="utf-8", errors="replace")


def serialize(directive: TextDirective) -> str:
    """Render a directive as `text=[prefix-,]start[,end][,-suffix]`."""
    parts: list[str] = []
    if directive.prefix:
        parts.append(f"{encode_term(directive.prefix)}-")
    parts.append(encode_term(directive.start))
    if directive.end:
        parts.append(encode_term(directive.end))
    if directive.suffix:
        parts.append(f"-{encode_term(directive.suffix)}")
    return DIRECTIVE_PARAM + ",".join(parts)


def parse_text_directive(value: str) -> TextDirective | None:
    """Parse the part after `text=`. Returns None when invalid, as the spec does."""
    tokens = value.split(",")
    if not 1 <= len(tokens) <= 4:
        return None

    prefix = suffix = end = None

    if tokens[0].endswith("-"):
        prefix = tokens[0][:-1]
        tokens = tokens[1:]
        if not prefix or "-" in prefix or not tokens:
            return None

    if tokens and tokens[-1].startswith("-"):
        suffix = tokens[-1][1:]
        tokens = tokens[:-1]
        if not suffix or "-" in suffix or not tokens:
            return None

    if not 1 <= len(tokens) <= 2:
        return None

    start = tokens[0]
    if not start or "-" in start:
        return None
    if len(tokens) == 2:
        end = tokens[1]
        if not end or "-" in end:
            return None

    return TextDirective(
        start=decode_term(start),
        end=decode_term(end) if end is not None else None,
        prefix=decode_term(prefix) if prefix is not None else None,
        suffix=decode_term(suffix) if suffix is not None else None,
    )


def parse_fragment_directive(fragment_directive: str) -> list[TextDirective]:
    """Parse the string after `:~:` into its text directives; unknown ones are skipped."""
    directives: list[TextDirective] = []
    for item in fragment_directive.split("&"):
        if not item.startswith(DIRECTIVE_PARAM):
            continue
        try:
            parsed = parse_text_directive(item[len(DIRECTIVE_PARAM):])
        except ValueError:
            continue
        if parsed is not None:
            directives.append(parsed)
    return directives
