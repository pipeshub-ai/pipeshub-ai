"""Shared text-literal handling for native-query validators.

JQL and CQL both require any value containing special characters to be
quoted, so a validator that scans for a bare operator character (like `~`)
must first blank out quoted literals — otherwise a legitimate value
containing that character (a label, a free-text-looking title) risks a
false positive. Confluence's personal-space token (`~<accountId>`, itself
UNQUOTED) is handled separately in `confluence.py`'s validator by requiring
a known free-text field name immediately before the operator, not by
quoting — see that module for why.
"""

from __future__ import annotations

import re

_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')


def strip_quoted_literals(query: str) -> str:
    """Replace each quoted string literal with equal-length whitespace so
    downstream regexes never match characters inside a literal, while
    preserving the original string's length/character offsets."""
    return _QUOTED_RE.sub(lambda m: " " * len(m.group(0)), query)


__all__ = ["strip_quoted_literals"]
