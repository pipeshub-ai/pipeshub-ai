"""People named in Jira and Confluence payloads, as ``SourcePerson``.

Cloud names a user by ``accountId``; Data Center by ``userKey`` (Confluence)
or ``key``/``name`` (Jira), the same ids the connectors store as
``source_user_id``. Emails are often hidden by the user's profile visibility.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models.entities import SourcePerson

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.models.entities import AppUser


def atlassian_person(user: Any, email: str | None = None) -> SourcePerson | None:  # noqa: ANN401
    """``user`` (a Jira or Confluence user object) as a SourcePerson; ``email``
    is used when the payload carries none."""
    if not isinstance(user, dict):
        return None
    source_id = user.get("accountId") or user.get("userKey") or user.get("key") or user.get("name")
    email = (user.get("emailAddress") or user.get("email") or email or "").strip() or None
    if not source_id and not email:
        return None
    return SourcePerson(
        source_id=source_id,
        email=email,
        display_name=user.get("displayName") or user.get("publicName"),
        is_service_account=user.get("accountType") == "app",
    )


def jira_user(user: Any, user_by_source_id: Mapping[str, AppUser]) -> SourcePerson | None:  # noqa: ANN401
    """A Jira issue's user field, with the email from the synced directory when
    the issue payload omits it."""
    source_id = user.get("accountId") or user.get("key") or user.get("name") if isinstance(user, dict) else None
    known = user_by_source_id.get(source_id) if source_id else None
    return atlassian_person(user, known.email if known else None)


def ticket_people(
    creator: SourcePerson | None, reporter: SourcePerson | None, assignee: SourcePerson | None,
) -> dict[str, Any]:
    """TicketRecord fields naming the ticket's people by source id when one of
    them has no email.

    The ticket extractor reads either the email fields or the source-id fields,
    so a single missing email switches the ticket to source ids. TicketRecord
    has no creator source-id field; the creator goes on ``created_by``.
    """
    named = [p for p in (creator, reporter, assignee) if p is not None and p.identifiable]
    if not any(p.source_id and not p.email for p in named):
        return {}

    def source_id(person: SourcePerson | None) -> str | None:
        return person.source_id if person is not None and person.identifiable else None

    assignee_id = source_id(assignee)
    return {
        "is_email_hidden": True,
        "reporter_source_id": source_id(reporter),
        "assignee_source_id": [assignee_id] if assignee_id else [],
        "created_by": creator if source_id(creator) else None,
    }


def confluence_people(content: Any) -> dict[str, SourcePerson | None]:  # noqa: ANN401
    """``authored_by`` and ``last_modified_by`` of a Confluence page, blog post
    or attachment, v1 (``history.createdBy``, ``version.by``,
    ``history.lastUpdated.by``) or v2 (``authorId``, ``version.authorId``)."""
    if not isinstance(content, dict):
        return {"authored_by": None, "last_modified_by": None}
    history = content.get("history") if isinstance(content.get("history"), dict) else {}
    version = content.get("version") if isinstance(content.get("version"), dict) else {}
    last_updated = history.get("lastUpdated") if isinstance(history.get("lastUpdated"), dict) else {}

    editor = (
        atlassian_person(version.get("by"))
        or atlassian_person(last_updated.get("by"))
        or _by_id(version.get("authorId"))
    )
    author = atlassian_person(history.get("createdBy")) or _by_id(content.get("authorId"))
    if author is None and (version.get("number") or last_updated.get("number")) == 1:
        author = editor
    return {"authored_by": author, "last_modified_by": editor}


def _by_id(account_id: Any) -> SourcePerson | None:  # noqa: ANN401
    return SourcePerson(source_id=account_id) if isinstance(account_id, str) and account_id else None
