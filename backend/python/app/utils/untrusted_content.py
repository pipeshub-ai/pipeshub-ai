"""Response headers for stored bytes someone else wrote (an upload, an agent's artifact).

The Node edge sets the same headers (`libs/utils/untrusted-content.utils.ts`);
this service sets them too, so no proxy of it serves an artifact as a page
(SEC-FS-04).
"""

from __future__ import annotations

import re

UNTRUSTED_CONTENT_CSP = "sandbox; default-src 'none'"

_ACTIVE_TYPE = re.compile(
    r"^(?:text/html|application/xhtml\+xml|image/svg\+xml|text/xml|application/xml|text/xsl"
    r"|application/[\w.-]+\+xml|text/(?:x-)?(?:java|ecma)script|application/(?:x-)?(?:java|ecma)script"
    r"|multipart/x-mixed-replace)\b",
    re.IGNORECASE,
)


def is_active_content_type(content_type: str | None) -> bool:
    return bool(content_type) and _ACTIVE_TYPE.match(content_type.strip()) is not None


def untrusted_content_headers(content_type: str | None) -> dict[str, str]:
    """`nosniff` always; an active type is a sandboxed attachment."""
    headers = {"X-Content-Type-Options": "nosniff"}
    if is_active_content_type(content_type):
        headers["Content-Security-Policy"] = UNTRUSTED_CONTENT_CSP
        headers["Content-Disposition"] = "attachment"
    return headers
