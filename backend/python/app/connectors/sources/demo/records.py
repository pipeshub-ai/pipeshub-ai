"""Demo records shaped like each real connector's, people fields included.

One builder per (system, record type). Each takes the fields every demo
record shares and the record's people slots (``systems.py``) and fills
exactly the people fields the real connector fills, the way it fills them:
by email, by the connector's user id, or as ``SourcePerson``. A person's
connector user id is their fixture id, which is also the ``source_user_id``
of the app user the sync writes for a member.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import Connectors
from app.connectors.sources.demo.fields import deal_fields, ticket_fields
from app.connectors.sources.demo.systems import shape_of
from app.models.entities import (
    CommentRecord,
    DealRecord,
    FileRecord,
    MailRecord,
    MessageRecord,
    PullRequestRecord,
    RecordGroupType,
    RecordType,
    SourcePerson,
    TicketRecord,
    WebpageRecord,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.connectors.sources.demo.systems import Slots
    from app.models.entities import Record

    Builder = Callable[[dict[str, Any], "Slots", "People", Mapping[str, Any]], Record]

# Knowledge-base files keep the Demo connector's name: a record named KB is
# streamed from blob storage, where the demo's text does not live.
SYSTEM_TO_CONNECTOR = {
    "GITHUB": Connectors.GITHUB,
    "JIRA": Connectors.JIRA,
    "SLACK": Connectors.SLACK,
    "DRIVE": Connectors.GOOGLE_DRIVE,
    "SERVICENOW": Connectors.SERVICENOW,
    "ZENDESK": Connectors.ZENDESK,
    "CONFLUENCE": Connectors.CONFLUENCE,
    "GMAIL": Connectors.GOOGLE_MAIL,
    "SALESFORCE": Connectors.SALESFORCE,
    "KB": Connectors.DEMO,
}
KIND_TO_GROUP_TYPE = {
    "repository": RecordGroupType.REPOSITORY,
    "project": RecordGroupType.PROJECT,
    "channel": RecordGroupType.SLACK_CHANNEL,
    "folder": RecordGroupType.DRIVE,
    "desk": RecordGroupType.PROJECT,
    "group": RecordGroupType.PROJECT,
    "space": RecordGroupType.CONFLUENCE_SPACES,
    "mailbox": RecordGroupType.MAILBOX,
    "account": RecordGroupType.SALESFORCE_ORG,
    "collection": RecordGroupType.KB,
}


class People:
    """Fixture people by id, in the forms the real connectors name them."""

    def __init__(self, people: Mapping[str, Mapping[str, Any]]) -> None:
        self._people = people

    def email(self, ids: list[str]) -> str | None:
        return self._people[ids[0]]["email"] if ids else None

    def emails(self, ids: list[str]) -> list[str]:
        return [self._people[pid]["email"] for pid in ids]

    def name(self, ids: list[str]) -> str | None:
        return self._people[ids[0]]["name"] if ids else None

    def source_person(self, ids: list[str], *, by_id: bool = True) -> SourcePerson | None:
        if not ids:
            return None
        person = self._people[ids[0]]
        return SourcePerson(
            source_id=person["id"] if by_id else None, email=person["email"], display_name=person["name"],
        )

    def source_people(self, ids: list[str]) -> list[SourcePerson]:
        return [p for p in (self.source_person([pid]) for pid in ids) if p is not None]


def _first(ids: list[str]) -> str | None:
    return ids[0] if ids else None


def _pull_request(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return PullRequestRecord(
        record_type=RecordType.PULL_REQUEST,
        creator_email=people.email(slots["creator"]),
        creator_name=people.name(slots["creator"]),
        assignee_email=people.emails(slots["assignees"]),
        review_email=people.emails(slots["reviewers"]),
        **common,
    )


def _github_issue(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    # GitHub hides addresses, so the issue names people by GitHub user id.
    return TicketRecord(
        record_type=RecordType.TICKET,
        is_email_hidden=True,
        reporter_source_id=_first(slots["reporter"]),
        reporter_name=people.name(slots["reporter"]),
        creator_email=people.email(slots["reporter"]),
        creator_name=people.name(slots["reporter"]),
        assignee_source_id=list(slots["assignees"]),
        assignee=people.name(slots["assignees"]),
        **ticket_fields(rec),
        **common,
    )


def _jira_issue(
    common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any], **source_ids: str | list[str] | None,
) -> Record:
    return TicketRecord(
        record_type=RecordType.TICKET,
        creator_email=people.email(slots["creator"]),
        creator_name=people.name(slots["creator"]),
        reporter_email=people.email(slots["reporter"]),
        reporter_name=people.name(slots["reporter"]),
        assignee_email=people.email(slots["assignee"]),
        assignee=people.name(slots["assignee"]),
        **source_ids,
        **ticket_fields(rec),
        **common,
    )


def _zendesk_ticket(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    # Zendesk fills Jira's fields and also names the requester and assignee by its own user id.
    return _jira_issue(
        common, slots, people, rec,
        reporter_source_id=_first(slots["reporter"]), assignee_source_id=list(slots["assignee"]),
    )


def _servicenow_ticket(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return TicketRecord(record_type=RecordType.TICKET, **ticket_fields(rec), **common)


def _comment(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return CommentRecord(
        record_type=RecordType.COMMENT,
        parent_external_record_id=rec.get("parent"),
        author_source_id=_first(slots["author"]),
        **common,
    )


def _message(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return MessageRecord(
        record_type=RecordType.MESSAGE,
        author_email=people.email(slots["involved"]),
        involved_user_source_ids=list(slots["involved"]),
        **common,
    )


def _drive_file(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return FileRecord(
        record_type=RecordType.FILE,
        is_file=True,
        extension="md",
        owners=people.source_people(slots["owners"]),
        authored_by=people.source_person(slots["authored_by"]),
        last_modified_by=people.source_person(slots["last_modified_by"]),
        **common,
    )


def _confluence_page(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return WebpageRecord(
        record_type=RecordType.CONFLUENCE_PAGE,
        record_group_type=RecordGroupType.CONFLUENCE_SPACES,
        authored_by=people.source_person(slots["authored_by"]),
        last_modified_by=people.source_person(slots["last_modified_by"]),
        **common,
    )


def _mail(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return MailRecord(
        record_type=RecordType.MAIL,
        record_group_type=RecordGroupType.MAILBOX,
        subject=rec["title"],
        from_email=people.email(slots["from"]),
        to_emails=people.emails(slots["to"]),
        cc_emails=people.emails(slots["cc"]),
        **common,
    )


def _deal(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    # Salesforce names the owner by its own user id, never by address.
    return DealRecord(
        record_type=RecordType.DEAL,
        record_group_type=RecordGroupType.DEAL,
        name=rec["title"],
        owner_id=_first(slots["owner"]),
        **deal_fields(rec),
        **common,
    )


def _case(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    return TicketRecord(
        record_type=RecordType.CASE,
        record_group_type=RecordGroupType.CASE,
        creator_email=people.email(slots["creator"]),
        creator_name=people.name(slots["creator"]),
        assignee_email=people.email(slots["assignee"]),
        assignee=people.name(slots["assignee"]),
        reporter_email=people.email(slots["reporter"]),
        reporter_name=people.name(slots["reporter"]),
        **ticket_fields(rec),
        **common,
    )


def _kb_file(common: dict[str, Any], slots: Slots, people: People, rec: Mapping[str, Any]) -> Record:
    # Platform users have no id on the knowledge-base app; the uploader is named by email.
    return FileRecord(
        record_type=RecordType.FILE,
        is_file=True,
        extension="md",
        created_by=people.source_person(slots["created_by"], by_id=False),
        **common,
    )


BUILDERS: dict[tuple[str, str], Builder] = {
    ("GITHUB", "PULL_REQUEST"): _pull_request,
    ("GITHUB", "TICKET"): _github_issue,
    ("GITHUB", "COMMENT"): _comment,
    ("JIRA", "TICKET"): _jira_issue,
    ("JIRA", "COMMENT"): _comment,
    ("SERVICENOW", "TICKET"): _servicenow_ticket,
    ("ZENDESK", "TICKET"): _zendesk_ticket,
    ("SLACK", "MESSAGE"): _message,
    ("DRIVE", "FILE"): _drive_file,
    ("CONFLUENCE", "PAGE"): _confluence_page,
    ("GMAIL", "MAIL"): _mail,
    ("SALESFORCE", "DEAL"): _deal,
    ("SALESFORCE", "CASE"): _case,
    ("KB", "KB_FILE"): _kb_file,
}


def build_record(
    rec: Mapping[str, Any], container: Mapping[str, Any], people: People, common: dict[str, Any],
) -> Record:
    """``rec`` as the record its system's real connector would emit."""
    key = (container["system"], rec["type"])
    shape = shape_of(*key)
    if shape is None or key not in BUILDERS:
        raise ValueError(f"record {rec['id']}: no demo builder for {key}")
    return BUILDERS[key](common, shape.slots(rec), people, rec)
