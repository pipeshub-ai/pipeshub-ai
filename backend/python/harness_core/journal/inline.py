"""Content carried in its own ref: ``inline:`` + base64url of the bytes.

Node writes a steering message this way (``input.queued.textRef``), so its
projector reads it without a content store; the worker's store writes small
payloads the same way. Hash and size are checked by whoever reads the bytes.

How long a ref may be is the contract's (``ContentRef.ref``'s ``maxLength``),
one bound every receiver validates, so a writer can tell up front whether an
inline ref would be taken (CHILDREF).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Final

from harness_core.contracts.generated.common import ContentRef

INLINE_REF_PREFIX = "inline:"


def _ref_max_chars() -> int:
    bound = ContentRef.model_json_schema()["properties"]["ref"].get("maxLength")
    if not isinstance(bound, int):
        raise RuntimeError("contracts/harness/core/common.schema.json: ContentRef.ref has no maxLength")
    return bound


CONTENT_REF_MAX_CHARS: Final = _ref_max_chars()


def inline_ref_chars(size: int) -> int:
    """The length of the inline ref of ``size`` bytes (padded base64url)."""
    return len(INLINE_REF_PREFIX) + 4 * ((size + 2) // 3)


# The most bytes an inline ref can carry within the contract's bound.
INLINE_CAPACITY_BYTES: Final = (CONTENT_REF_MAX_CHARS - len(INLINE_REF_PREFIX)) // 4 * 3


def content_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def inline_ref(data: bytes) -> ContentRef:
    ref = INLINE_REF_PREFIX + base64.urlsafe_b64encode(data).decode("ascii")
    return ContentRef(ref=ref, hash=content_digest(data), size=len(data))


def inline_bytes(ref: ContentRef) -> bytes | None:
    """The bytes an inline ref carries (unverified); ``None`` for any other ref."""
    if not ref.ref.startswith(INLINE_REF_PREFIX):
        return None
    return base64.urlsafe_b64decode(ref.ref.removeprefix(INLINE_REF_PREFIX))


__all__ = [
    "CONTENT_REF_MAX_CHARS",
    "INLINE_CAPACITY_BYTES",
    "INLINE_REF_PREFIX",
    "content_digest",
    "inline_bytes",
    "inline_ref",
    "inline_ref_chars",
]
