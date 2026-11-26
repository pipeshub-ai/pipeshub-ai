"""Security data shared by the gateways, the egress proxy and every redactor.

A leaf package (no imports beyond pydantic) so the proxy can use it without the
gateway, the kernel or the effect layer.
"""

from harness_core.security.token_patterns import (
    FORBIDDEN_PATTERN_SOURCES,
    FORBIDDEN_TOKEN_PATTERNS,
    REDACT_ONLY_PATTERNS,
    REDACTION_PATTERNS,
    SECRET_MASK,
    VENDOR_TOKEN_PATTERNS,
    TokenPattern,
    find_forbidden,
    find_forbidden_in,
    hide_forbidden,
    hide_forbidden_in,
    redact_tokens,
)

__all__ = [
    "FORBIDDEN_PATTERN_SOURCES",
    "FORBIDDEN_TOKEN_PATTERNS",
    "REDACTION_PATTERNS",
    "REDACT_ONLY_PATTERNS",
    "SECRET_MASK",
    "VENDOR_TOKEN_PATTERNS",
    "TokenPattern",
    "find_forbidden",
    "find_forbidden_in",
    "hide_forbidden",
    "hide_forbidden_in",
    "redact_tokens",
]
