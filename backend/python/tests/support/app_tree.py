"""One shared, cached read of the ``app/`` source tree for the whole-tree guard tests.

``app/`` is about 2,250 files and 53 MiB, and generated clients under ``app/sources/external/`` make up much of
that (one file is 2.4 MiB). A guard that parses every file takes tens of seconds on an idle core and more on a
loaded runner, so every guard goes through this module:

* ``sources(root)`` lists and reads the tree once per process; later guards get the same strings.
* ``may_contain_name(text, *names)`` is a prefilter for rules that look at identifiers only.

Parsed trees are deliberately not cached: they are about ten times the size of their source, and a cache bounded
below that evicts in sequence and shares nothing. A guard that needs a tree computes everything it needs from it in
one pass, caches that result, and lets the tree go.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


@lru_cache(maxsize=None)
def sources(root: Path = APP) -> tuple[tuple[Path, str], ...]:
    """Every ``*.py`` file under ``root``, sorted, with its text (UTF-8, BOM tolerated)."""
    return tuple((p, p.read_text(encoding="utf-8-sig")) for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts)


def may_contain_name(text: str, *names: str) -> bool:
    """False only when no identifier in the file can equal any of ``names``.

    The parser NFKC-normalises identifiers, so ``ｃreate_anyone`` (fullwidth first letter) is the name
    ``create_anyone``; the text is normalised the same way before the substring test. Pure-ASCII text is its own
    normal form and is not copied. The test says nothing about strings: a rule that also looks at string values
    (which can be split, concatenated or written with escapes) must not use it.
    """
    if not text.isascii():
        text = unicodedata.normalize("NFKC", text)
    return any(n in text for n in names)
