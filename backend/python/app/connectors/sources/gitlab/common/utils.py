"""Shared utility helpers for the GitLab connector."""

from __future__ import annotations


def parse_item_id_from_url(url: str) -> int:
    """Return the numeric item ID from a GitLab web URL.

    GitLab uses two URL shapes depending on version / feature flags:
      - Classic issues:  …/-/issues/<id>
      - Work items:      …/-/work_items/<id>
      - Merge requests:  …/-/merge_requests/<id>

    Raises ``ValueError`` if no recognisable numeric ID can be found.
    """
    markers = ("issues", "work_items", "merge_requests")
    parts = url.rstrip("/").split("/")
    for marker in markers:
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                return int(parts[idx + 1])
    raise ValueError(f"Cannot parse item ID from GitLab URL: {url}")
