"""Which comments, and which of an issue's attachments, every viewer of the issue may read.

An issue's content is indexed once and served to everyone who can open the issue, so it
may only hold what all of them can see in Jira. A comment restricted to a project role or
a group (``visibility``), or a Jira Service Management internal note (``jsdPublic`` false),
is readable by fewer people than the issue, and so is a file attached in one.
"""

from collections.abc import Callable, Iterable
from typing import Any

# (filenames lower-cased, attachment ids) that one description or comment shows.
Refs = tuple[set[str], set[str]]


def is_restricted_comment(comment: dict[str, Any]) -> bool:
    """True when the comment is hidden from some of the people who can see its issue."""
    if comment.get("visibility"):
        return True
    return comment.get("jsdPublic") is False


def public_comments(comments: Iterable[Any]) -> list[dict[str, Any]]:
    return [c for c in comments if isinstance(c, dict) and not is_restricted_comment(c)]


def attachment_ids_only_in_restricted_comments(
    attachments: Iterable[Any],
    description_refs: Refs,
    comments: Iterable[Any],
    comment_refs: Callable[[dict[str, Any]], Refs],
) -> set[str]:
    """Ids of attachments shown by a restricted comment and nowhere public.

    An attachment the description or a public comment also shows, or one that is shown
    nowhere (attached to the issue itself), stays public.
    """
    comments = [c for c in comments if isinstance(c, dict)]
    restricted = [c for c in comments if is_restricted_comment(c)]
    if not restricted:
        return set()

    attachments = [a for a in attachments if isinstance(a, dict) and a.get("id") is not None]

    def shown_by(refs: Iterable[Refs]) -> set[str]:
        names: set[str] = set()
        ids: set[str] = set()
        for ref_names, ref_ids in refs:
            names |= ref_names
            ids |= ref_ids
        return {
            str(a["id"]) for a in attachments
            if str(a["id"]) in ids or (a.get("filename") or "").strip().lower() in names
        }

    hidden = shown_by(comment_refs(c) for c in restricted)
    if not hidden:
        return set()
    return hidden - shown_by([description_refs, *(comment_refs(c) for c in public_comments(comments))])
