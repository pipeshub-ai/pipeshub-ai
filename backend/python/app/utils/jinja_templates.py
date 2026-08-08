"""Compile-once access to the constant Jinja prompt templates.

The context builders construct templates inside per-record loops from
module-level constants, recompiling the same handful of sources hundreds of
times per chat turn (11.5% of query-service CPU).

Its own module because `chat_helpers` and `models.entities` both need it and
`chat_helpers` already imports from `entities`.

Only pass constant sources: the cache is keyed on the source text, so a
caller-built string would fill it with single-use entries.
"""
from __future__ import annotations

from functools import lru_cache

from jinja2 import Template

__all__ = ["compiled_template"]


@lru_cache(maxsize=64)
def compiled_template(source: str) -> Template:
    """Shared compiled `Template` — safe because `render()` builds its own
    context per call and the template itself is not mutated."""
    return Template(source)
