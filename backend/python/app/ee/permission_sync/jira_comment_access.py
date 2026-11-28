# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Which comments, and which of an issue's attachments, every viewer of the issue may read.

An issue's content is indexed once and served to everyone who can open the issue, so it
may only hold what all of them can see in Jira. A comment restricted to a project role or
a group (``visibility``), or a Jira Service Management internal note (``jsdPublic`` false),
is readable by fewer people than the issue, and so is a file attached in one.

Such a comment is synced as a record of its own under the issue (``restricted_comment_grants``).
"""

from collections.abc import Callable, Iterable
from typing import Any

from app.models.permission import EntityType, Permission, PermissionType

COMMENT_RECORD_PREFIX = "comment_"

# (filenames lower-cased, attachment ids) that one description or comment shows.
Refs = tuple[set[str], set[str]]


def is_restricted_comment(comment: dict[str, Any]) -> bool:
    """True when the comment is hidden from some of the people who can see its issue."""
    if comment.get("visibility"):
        return True
    return comment.get("jsdPublic") is False


def restricted_comment_grants(
    comment: dict[str, Any],
    *,
    project_key: str,
    role_ids: dict[str, str],
    group_ids_by_name: dict[str, str],
    product_access_groups: list[str] | None,
) -> list[Permission]:
    """Who Jira shows a restricted comment to, among the people who read its issue.

    ``visibility`` names a project role (by name) or a group: by its id (``identifier``, as Jira
    Cloud sends it), or by name alone in an older payload, resolved through ``group_ids_by_name``
    (this sync's complete group read only). A name the read gives another id than the explicit one
    is stale or ambiguous, and grants nobody. A Service
    Management internal note is read by everyone who opens the issue in Jira itself, not
    through the customer portal: the people with product access, ``product_access_groups``
    (None when the application roles could not be read in full). Anything unresolved grants
    nobody, so the comment stays closed.
    """
    visibility = comment.get("visibility")
    if visibility:
        if not isinstance(visibility, dict):
            return []
        kind = visibility.get("type")
        identifier, name = str(visibility.get("identifier") or ""), str(visibility.get("value") or "")
        if kind == "group":
            by_name = group_ids_by_name.get(name)
            if identifier and by_name and by_name != identifier:
                return []
            group_id = identifier or by_name
            return [Permission(entity_type=EntityType.GROUP, external_id=group_id, type=PermissionType.READ)] \
                if group_id else []
        if kind == "role":
            role_id = identifier if identifier.isdigit() else role_ids.get(identifier) or role_ids.get(name)
            return [Permission(entity_type=EntityType.ROLE, external_id=f"{project_key}_{role_id}",
                               type=PermissionType.READ)] if role_id else []
        return []
    return [Permission(entity_type=EntityType.GROUP, external_id=g, type=PermissionType.READ)
            for g in product_access_groups or []]


def public_comments(comments: Iterable[Any]) -> list[dict[str, Any]]:
    return [c for c in comments if isinstance(c, dict) and not is_restricted_comment(c)]


def attachments_only_in_restricted_comments(
    attachments: Iterable[Any],
    description_refs: Refs,
    comments: Iterable[Any],
    comment_refs: Callable[[dict[str, Any]], Refs],
) -> dict[str, set[str]]:
    """Attachments shown by a restricted comment and nowhere public, each with the ids of the
    restricted comments that show it. Jira shows such a file only to those who can read the comment.

    An attachment the description or a public comment also shows, or one that is shown
    nowhere (attached to the issue itself), stays public.
    """
    comments = [c for c in comments if isinstance(c, dict)]
    restricted = [c for c in comments if is_restricted_comment(c)]
    if not restricted:
        return {}

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

    public = shown_by([description_refs, *(comment_refs(c) for c in public_comments(comments))])
    hidden: dict[str, set[str]] = {}
    for comment in restricted:
        for attachment_id in shown_by([comment_refs(comment)]) - public:
            hidden.setdefault(attachment_id, set()).add(str(comment.get("id")))
    return hidden
