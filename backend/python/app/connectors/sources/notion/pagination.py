"""Reading Notion's paginated lists: every loop goes through these two, so a list Notion did not
finish is never taken for the whole (tests/unit/connectors/sources/test_notion_personal_access.py
checks that nothing else reads ``has_more`` or ``next_cursor``)."""

from typing import Any


def stopped_short(page: dict[str, Any], cursor: str | None) -> bool:
    """Whether a list page says there is more but gives nothing to go on: no results, or no new cursor."""
    if not page.get("has_more"):
        return False
    following = page.get("next_cursor")
    return not page.get("results") or not following or following == cursor


def next_cursor(page: dict[str, Any]) -> str | None:
    """The cursor of the page after this one; None when this one is the last."""
    return page.get("next_cursor") if page.get("has_more") else None
