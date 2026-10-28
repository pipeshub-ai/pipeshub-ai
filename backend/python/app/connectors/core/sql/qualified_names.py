"""Record ids for SQL objects, written as dotted qualified names.

``DB.SCHEMA.TABLE`` is how people write them, so ordinary names are written
as they are. A part that contains a dot, or starts with a double quote, is
written double-quoted with inner quotes doubled; otherwise schema ``a.b`` with
table ``c`` and schema ``a`` with table ``b.c`` would share an id.
"""

from collections.abc import Sequence


def join_qualified_name(parts: Sequence[str]) -> str:
    return ".".join(_write_part(part) for part in parts)


def split_qualified_name(fqn: str, parts: int) -> tuple[str, ...]:
    """Inverse of ``join_qualified_name`` for an id of exactly ``parts`` parts.

    Raises ValueError for anything ``join_qualified_name`` did not write.
    """
    names, rest = leading_parts(fqn, parts)
    if rest:
        raise ValueError(f"Not a {parts}-part name: {fqn!r}")
    return names


def leading_parts(fqn: str, parts: int) -> tuple[tuple[str, ...], str]:
    """The first ``parts`` parts of an id, and the text after them (from its dot on).

    For ids that continue past a qualified name, such as a stage's files.
    """
    names: list[str] = []
    rest = fqn
    for index in range(parts):
        if index:
            if not rest.startswith("."):
                raise ValueError(f"Not a {parts}-part name: {fqn!r}")
            rest = rest[1:]
        name, rest = _read_part(rest)
        names.append(name)
    return tuple(names), rest


def _write_part(name: str) -> str:
    if "." in name or name.startswith('"'):
        return '"' + name.replace('"', '""') + '"'
    return name


def _read_part(text: str) -> tuple[str, str]:
    """One part off the front of ``text``, and what follows it."""
    if not text.startswith('"'):
        dot = text.find(".")
        return (text, "") if dot == -1 else (text[:dot], text[dot:])
    chars: list[str] = []
    i = 1
    while i < len(text):
        if text[i] == '"':
            if text[i + 1:i + 2] == '"':
                chars.append('"')
                i += 2
                continue
            return "".join(chars), text[i + 1:]
        chars.append(text[i])
        i += 1
    raise ValueError(f"Unterminated quoted name in {text!r}")
