"""Page through a Drive list method until the API says the listing is finished.

An empty page is not the end. ``files.list`` (and the drives and permissions
lists) can return a page with no items and a ``nextPageToken``, and stopping
there drops the rest of the corpus.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

PageFetch = Callable[..., Awaitable[dict[str, Any] | None]]

if TYPE_CHECKING:
    from logging import Logger


class DrivePageWalk:
    """Side channel for a listing that finished in a way the caller must not trust."""

    def __init__(self) -> None:
        self.incomplete = False
        self.stopped_on_repeat = False


async def iter_drive_pages(
    fetch: PageFetch,
    params: dict[str, Any],
    list_key: str,
    logger: Logger | None = None,
    walk: DrivePageWalk | None = None,
) -> AsyncIterator[list[Any]]:
    """Yield each page's items.

    Stops only when the response has no ``nextPageToken``. A token that has
    already been requested stops the walk as well: following it would loop.
    """
    seen_tokens: set[str] = set()
    page_token = params.get("pageToken") or None
    base = {key: value for key, value in params.items() if key != "pageToken" and value is not None}

    while True:
        request = dict(base)
        if page_token:
            if page_token in seen_tokens:
                if walk is not None:
                    walk.stopped_on_repeat = True
                    walk.incomplete = True
                if logger is not None:
                    logger.error(
                        "Google Drive repeated a page token; stopping so the listing cannot loop"
                    )
                return
            seen_tokens.add(page_token)
            request["pageToken"] = page_token

        page = await fetch(**request) or {}
        if not isinstance(page, dict):
            if walk is not None:
                walk.incomplete = True
            if logger is not None:
                logger.error("Google Drive list response was not an object; stopping pagination")
            return
        if page.get("incompleteSearch") is True:
            if walk is not None:
                walk.incomplete = True
            if logger is not None:
                logger.warning(
                    "Google Drive reported incompleteSearch; this listing may be missing items"
                )

        items = page.get(list_key) or []
        if not isinstance(items, list):
            items = []
        yield items

        next_token = page.get("nextPageToken")
        if not isinstance(next_token, str) or not next_token:
            return
        page_token = next_token
