"""How a reader tells the storage download route's two answers apart."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def is_signed_url_reply(headers: Mapping[str, str]) -> bool:
    """Whether ``GET /api/v1/document/internal/{id}/download`` answered with its JSON reply (``{signedUrl}``, cloud
    storage) rather than the stored file.

    Local storage streams the file itself, typed by its extension and sent as an attachment, so a stored ``.json``
    file is also ``application/json``: only the JSON reply comes without ``Content-Disposition: attachment``.
    """
    if "attachment" in (headers.get("Content-Disposition") or "").lower():
        return False
    return "application/json" in (headers.get("Content-Type") or "").lower()


__all__ = ["is_signed_url_reply"]
