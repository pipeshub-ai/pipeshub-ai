"""Issue and comment bodies that name attachments, for the Jira suites' own setup calls.

A real site takes v2 wiki markup, which is how text names an attachment there. The
simulator serves only v3, so it gets ADF in which each attachment is a ``media`` node
naming its file by ``attrs.alt``. The ``id`` there is a generated UUID, not a Media
Services id (the attachment API returns no such id), so Jira would not resolve or render
it: what the v3 body covers is the connector reading attachment names from ``alt``.
"""

from __future__ import annotations

import uuid
from typing import Any

# Jira does not display a media item in a mediaSingle without positive dimensions.
INLINE_IMAGE_SIZE = {"width": 1, "height": 1}


def _media(name: str, **size: int) -> dict[str, Any]:
    return {"type": "media", "attrs": {"type": "file", "id": str(uuid.uuid4()), "collection": "", "alt": name, **size}}


def media_adf(text: str, *, images: tuple[str, ...] = (), files: tuple[str, ...] = ()) -> dict[str, Any]:
    """ADF that shows ``images`` inline and links ``files``."""
    content: list[dict[str, Any]] = [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
    for name in images:
        content.append({"type": "mediaSingle", "attrs": {"layout": "align-start"},
                        "content": [_media(name, **INLINE_IMAGE_SIZE)]})
    if files:
        content.append({"type": "mediaGroup", "content": [_media(name) for name in files]})
    return {"type": "doc", "version": 1, "content": content}


def wiki_markup(text: str, *, images: tuple[str, ...] = (), files: tuple[str, ...] = ()) -> str:
    return "\n\n".join([text, *(f"!{name}!" for name in images), *(f"[^{name}]" for name in files)])
