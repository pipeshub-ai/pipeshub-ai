"""Resolves the process-wide logging provider."""

import functools

from app.utils.structured_logging.interface import (
    DefaultLoggingProvider,
    StructuredLoggingProvider,
)

__all__ = ["DefaultLoggingProvider", "StructuredLoggingProvider", "get_provider"]


@functools.lru_cache(maxsize=1)
def get_provider() -> StructuredLoggingProvider:
    # Imported here, not at module level: app.edition must stay import-free and
    # the logger loads before anything else.
    from app.edition import structured_logging_provider

    provider = structured_logging_provider()
    if provider is None:
        return DefaultLoggingProvider()
    if not isinstance(provider, StructuredLoggingProvider):
        raise TypeError(
            "app.edition.structured_logging_provider() must return a "
            f"StructuredLoggingProvider or None, got {type(provider)!r}"
        )
    return provider
