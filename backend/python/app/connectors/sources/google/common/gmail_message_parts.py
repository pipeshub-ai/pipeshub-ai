"""Gmail message parts: find one by its ``partId``, and decode a part's body data."""

from __future__ import annotations

import base64
from typing import Any


def find_message_part(parts: object, part_id: str) -> dict[str, Any] | None:
    """The part with ``part_id`` at any depth. Sync records attachments from
    nested parts too (a forwarded message, inline images in multipart/related),
    whose ids look like ``1.2``, so a top-level search misses them."""
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("partId") == part_id:
            return part
        found = find_message_part(part.get("parts"), part_id)
        if found is not None:
            return found
    return None


def decode_body_data(data: str | None) -> bytes:
    """A part body's ``data``: base64url, with or without its ``=`` padding (the reference does not say which
    Gmail sends; ``urlsafe_b64decode`` refuses unpadded text)."""
    if not data:
        return b""
    text = data.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
