from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from app.utils.text_fragments.cache import TtlCache, ttl_memoize
from app.utils.text_fragments.generator import TextFragmentGenerator

if TYPE_CHECKING:
    from collections.abc import Hashable

    from app.utils.text_fragments.models import SourceFormat

_FRAGMENT_URL_CACHE = TtlCache(maxsize=8192, ttl_seconds=300.0)

_default_generator = TextFragmentGenerator()


def _cache_key(
    base_url: str, snippet: str, source_format: SourceFormat | None = None
) -> Hashable | None:
    """Digest the snippet so the cache does not retain whole record text as keys."""
    if not isinstance(base_url, str) or not isinstance(snippet, str):
        return None
    digest = hashlib.sha1(snippet.encode("utf-8", "surrogatepass")).digest()
    return (base_url, digest, source_format)


@ttl_memoize(_FRAGMENT_URL_CACHE, _cache_key)
def build_text_fragment_url(
    base_url: str, snippet: str, source_format: SourceFormat | None = None
) -> str:
    """Memoized `TextFragmentGenerator.build_url` with the default policy.

    The live citation overlay re-derives every citation's URL on each refresh,
    so the same (base_url, snippet) pair is rebuilt many times per turn.
    """
    return _default_generator.build_url(base_url, snippet, source_format)
