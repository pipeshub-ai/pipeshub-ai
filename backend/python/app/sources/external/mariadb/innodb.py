"""InnoDB's own table names, as ``information_schema.INNODB_SYS_TABLES`` shows them.

A name there is ``database/table`` in the server's filename encoding: letters,
digits and ``_`` as they are, every other character as ``@`` and four hex
digits (``my-items`` is ``my@002ditems``). Some letters have short codes
instead (``Ü`` is ``@1O``); those, and the ``#`` of a partition (``t#P#p0``) or
an internal table (``#sql-...``), are not decoded: such a table gets no id.
"""

from __future__ import annotations

import re

_HEX4 = re.compile(r"[0-9a-fA-F]{4}")


def _decode_part(encoded: str) -> str | None:
    decoded: list[str] = []
    position = 0
    while position < len(encoded):
        char = encoded[position]
        if char == "@":
            code = encoded[position + 1:position + 5]
            if not _HEX4.fullmatch(code):
                return None
            decoded.append(chr(int(code, 16)))
            position += 5
            continue
        if char == "#" or char == "/":
            return None
        decoded.append(char)
        position += 1
    return "".join(decoded) or None


def decode_innodb_name(name: str) -> tuple[str, str] | None:
    """(database, table) from an ``INNODB_SYS_TABLES.NAME``; None when it is not a plain table's."""
    database, slash, table = (name or "").partition("/")
    if not slash:
        return None
    decoded_database, decoded_table = _decode_part(database), _decode_part(table)
    if decoded_database is None or decoded_table is None:
        return None
    return decoded_database, decoded_table

