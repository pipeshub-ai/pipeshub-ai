"""The connector-card item for folder patterns that matched no folder in a complete sync.

The dashboard words a known item itself, in the reader's language, from
``actionItems.known.folder_filter_unmatched`` with ``params``; ``context`` picks
the variant. The English text here is what it falls back to, and en-US says the same.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.core.base.connector.connector_service import ActionItem

if TYPE_CHECKING:
    from collections.abc import Collection

FOLDER_FILTER_UNMATCHED_CODE = "folder_filter_unmatched"
_NAMED_AT_MOST = 5

# context -> (title, message); "" is one pattern under Include.
FOLDER_FILTER_UNMATCHED_TEXT: dict[str, tuple[str, str]] = {
    "": (
        "Folder pattern matched nothing",
        "No folder this connector syncs matches {values}, so nothing is synced for it. "
        "Check the spelling in the Folders filter.",
    ),
    "several": (
        "{count} folder patterns matched nothing",
        "No folder this connector syncs matches {values}, so nothing is synced for them. "
        "Check the spelling in the Folders filter.",
    ),
    "exclude": (
        "Folder pattern matched nothing",
        "No folder this connector syncs matches {values}, so it excludes nothing. "
        "Check the spelling in the Folders filter.",
    ),
    "several_exclude": (
        "{count} folder patterns matched nothing",
        "No folder this connector syncs matches {values}, so they exclude nothing. "
        "Check the spelling in the Folders filter.",
    ),
}


def _named(patterns: list[str]) -> str:
    shown = ", ".join(f'"{p}"' for p in patterns[:_NAMED_AT_MOST])
    return f"{shown}, …" if len(patterns) > _NAMED_AT_MOST else shown


def unmatched_folder_patterns_item(unmatched: Collection[str], *, exclude: bool) -> ActionItem:
    """The item for ``unmatched`` folder patterns (at least one)."""
    patterns = sorted(unmatched)
    context = "_".join(part for part, on in (("several", len(patterns) > 1), ("exclude", exclude)) if on)
    title, message = FOLDER_FILTER_UNMATCHED_TEXT[context]
    params = {"count": str(len(patterns)), "values": _named(patterns)}
    return ActionItem(
        code=FOLDER_FILTER_UNMATCHED_CODE,
        title=title.format(**params),
        message=message.format(**params),
        params={**params, "context": context} if context else params,
    )
