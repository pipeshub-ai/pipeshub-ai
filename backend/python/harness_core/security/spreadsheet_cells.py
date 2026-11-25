"""A text cell as a file a spreadsheet opens must hold it (SEC-B-06, ADR-TEXT-01).

A spreadsheet runs a cell that starts with ``=``, ``+``, ``-`` or ``@`` (or their full-width forms, or any of them
after leading blanks) as a formula, and a formula can send neighbouring cells out (``HYPERLINK``, ``WEBSERVICE``) or
start a program (DDE). Query results and model output carry text an outsider wrote, so every export writes such a
cell with a leading ``'`` and it opens as text. A plain number keeps its sign: ``-42`` is a value, not a formula.

The sets are ``contracts/harness/vectors/spreadsheet-cells.json``'s, which Node and the web client carry too.
"""

from __future__ import annotations

import re
from typing import Final

PREFIX: Final = "'"
TRIGGERS: Final = frozenset("=+-@＝＋－＠")
STARTS_ALWAYS: Final = frozenset("\t\r")
# A sign alone is a table's placeholder ("-" for none), not a formula: nothing follows it to run.
SIGNS: Final = frozenset("+-\uff0b\uff0d")
LEADING: Final = frozenset(
    "\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_PLAIN_NUMBER: Final = re.compile(r"[+-]?(?:[0-9][0-9.,]*|[.,][0-9]+)(?:[eE][+-]?[0-9]+)?%?")


def spreadsheet_cell(value: str) -> str:
    """``value`` as a spreadsheet must read it: as text, never as a formula."""
    if not value:
        return value
    if value[0] in STARTS_ALWAYS:
        return PREFIX + value
    first = next((ch for ch in value if ch not in LEADING), None)
    if first is None or first not in TRIGGERS:
        return value
    if _PLAIN_NUMBER.fullmatch(value):
        return value
    if first in SIGNS and all(ch in LEADING for ch in value.replace(first, "", 1)):
        return value
    return PREFIX + value


def spreadsheet_row(row: object) -> list[object]:
    """A row's cells with every text cell made safe; numbers, dates and ``None`` are written as they are."""
    cells = row if isinstance(row, (list, tuple)) else [row]
    return [spreadsheet_cell(cell) if isinstance(cell, str) else cell for cell in cells]


__all__ = ["LEADING", "PREFIX", "SIGNS", "STARTS_ALWAYS", "TRIGGERS", "spreadsheet_cell", "spreadsheet_row"]
