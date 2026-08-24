"""Single source of truth for code symbol-ID normalization.

Ported verbatim from graphify (``graphify/ids.py``). Two independent producers
must agree on symbol IDs or the graph splits one entity into disconnected ghost
nodes: the parser that mints them, and the edge resolver that looks them up.

The recipe: NFKC-normalize (so composed/decomposed Unicode forms collapse),
replace runs of non-word characters with a single underscore (``re.UNICODE`` so
CJK/Cyrillic/accented-Latin letters survive instead of collapsing to one node
per file), collapse repeated underscores, strip the edges, casefold.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["file_stem_for", "make_id", "normalize_id"]

_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)
_REPEATED_UNDERSCORE_RE = re.compile(r"_+")


def normalize_id(value: str) -> str:
    """Normalize a single ID string to its canonical form.

    Idempotent: ``normalize_id(normalize_id(s)) == normalize_id(s)``.
    """
    value = unicodedata.normalize("NFKC", value)
    value = _NON_WORD_RE.sub("_", value)
    value = _REPEATED_UNDERSCORE_RE.sub("_", value)
    return value.strip("_").casefold()


def make_id(*parts: str) -> str:
    """Build a canonical symbol ID from one or more name parts."""
    return normalize_id("_".join(part.strip("_.") for part in parts if part))


def file_stem_for(file_path: str) -> str:
    """Return the ID stem for a repo-relative file path.

    The *full* path minus its extension, so ``src/client.py`` becomes
    ``src_client`` rather than ``client`` -- two files with the same basename in
    different directories must not collide.
    """
    cleaned = (file_path or "").replace("\\", "/").strip("/")
    if not cleaned:
        return ""
    head, _, tail = cleaned.rpartition("/")
    base, dot, _ext = tail.rpartition(".")
    stem = base if dot else tail
    return make_id(head, stem) if head else make_id(stem)
