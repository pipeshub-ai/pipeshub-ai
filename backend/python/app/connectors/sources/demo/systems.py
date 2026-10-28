"""The systems the demo imitates, and the people each one names on a record.

One row per (system, record type) says which fixture person fields the real
connector for that system has a place for, and how it names those people:
the *slots* below are the record fields a real sync fills (``creator`` is
``creator_email``, ``owners`` is ``owners`` as ``SourcePerson``...), and
``edges`` is the ``entityRelations`` edge ``record_people`` writes for each.
The connector fills records from the slots, the ground truth derives the
expected graph from them, and the loader rejects a fixture field the real
connector would not emit. Nothing here imports the app, so the evaluation
harness can use it without the service's dependencies.

Evidence for each row is the real connector's record construction:

- GitHub pull request: ``github_teams/pull_requests.py`` (``creator_email``,
  ``assignee_email``, ``review_email``).
- GitHub issue: ``github_teams/issues.py`` (``is_email_hidden``, the creator as
  ``reporter_source_id``, every assignee in ``assignee_source_id``).
- Jira issue: ``atlassian/jira_cloud/connector.py`` (``creator_email``,
  ``reporter_email``, ``assignee_email``); Jira always has a reporter, the
  creator unless someone else is named.
- ServiceNow: ``servicenow/servicenow/connector.py`` syncs knowledge articles
  with no people on them, so a ServiceNow record names nobody.
- Zendesk ticket: ``zendesk/connector.py`` (requester as ``reporter_email``,
  assignee as ``assignee_email``, submitter as ``creator_email``); Zendesk
  always has a requester, the submitter unless someone else is named.
- Comments: ``CommentRecord.author_source_id``, as the Confluence connectors
  fill it (the only connectors that emit comment records).
- Slack: ``slack/individual/connector.py`` names a thread's authors in
  ``involved_user_source_ids`` (``INVOLVED_IN``, members only).
- Google Drive: ``google/common/drive_people.py`` (``owners``,
  ``last_modified_by``; a single My Drive owner is also ``authored_by``).
  Drive always reports a last modifier: the owner when nobody else edited.
- Confluence: ``atlassian/core/people.py::confluence_people`` (``authored_by``,
  ``last_modified_by``; the version author is always present).
- Gmail: ``google/gmail/team/connector.py`` (``from_email``, ``to_emails``,
  ``cc_emails``).
- Salesforce: ``salesforce/connector.py::_build_deal_record`` (``owner_id``) and
  ``_build_case_record`` (owner as ``assignee_email``, contact as
  ``reporter_email``, creator as ``creator_email``).
- Knowledge base upload: ``localKB/handlers/kb_service.py::_uploader`` (the
  uploader as ``created_by``, by email).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# entityRelations edge types, as strings so this module needs nothing from the app.
ASSIGNED_TO = "ASSIGNED_TO"
AUTHORED_BY = "AUTHORED_BY"
ADDRESSED_TO = "ADDRESSED_TO"
CREATED_BY = "CREATED_BY"
INVOLVED_IN = "INVOLVED_IN"
LAST_MODIFIED_BY = "LAST_MODIFIED_BY"
OWNED_BY = "OWNED_BY"
REPORTED_BY = "REPORTED_BY"
REVIEWED_BY = "REVIEWED_BY"

# Every fixture field that holds person ids; the plural ones hold lists.
PERSON_FIELDS = frozenset({
    "author", "assignee", "assignees", "reviewers", "reporter", "owner", "owners",
    "last_modified_by", "from", "to", "cc",
})
_LIST_FIELDS = frozenset({"assignees", "reviewers", "owners", "to", "cc"})

Slots = dict[str, list[str]]


def people_in(rec: Mapping, field: str) -> list[str]:
    """The person ids in ``rec[field]``, as a list whether the field is one id or many."""
    value = rec.get(field)
    if value is None:
        return []
    return list(value) if field in _LIST_FIELDS else [value]


def _first(*candidates: list[str]) -> list[str]:
    return next((c[:1] for c in candidates if c), [])


@dataclass(frozen=True)
class RecordShape:
    """How one system's connector names the people on one type of record."""

    label: str
    fields: frozenset[str]
    slots: Callable[[Mapping], Slots]
    edges: Mapping[str, str]


@dataclass(frozen=True)
class SystemSpec:
    label: str
    kinds: frozenset[str]
    shapes: Mapping[str, RecordShape]


def _comment(rec: Mapping) -> Slots:
    return {"author": people_in(rec, "author")}


_COMMENT = RecordShape("Review comment", frozenset({"author"}), _comment, {"author": AUTHORED_BY})


def _drive_file(rec: Mapping) -> Slots:
    # The fixture's author of a Drive file is its owner, as in My Drive.
    owners = people_in(rec, "owners") or people_in(rec, "author")
    return {
        "owners": owners,
        "authored_by": owners if len(owners) == 1 else [],
        "last_modified_by": _first(people_in(rec, "last_modified_by"), owners),
    }


# A ticket whose creator, reporter and assignee are all named, as Jira and
# Zendesk name them; the reporter is the creator unless someone else is named.
_REPORTED_TICKET = RecordShape(
    "Ticket", frozenset({"author", "reporter", "assignee"}),
    lambda r: {"creator": people_in(r, "author"),
               "reporter": _first(people_in(r, "reporter"), people_in(r, "author")),
               "assignee": people_in(r, "assignee")},
    {"creator": CREATED_BY, "reporter": REPORTED_BY, "assignee": ASSIGNED_TO},
)


SYSTEMS: dict[str, SystemSpec] = {
    "GITHUB": SystemSpec("GitHub", frozenset({"repository"}), {
        "PULL_REQUEST": RecordShape(
            "Pull request", frozenset({"author", "assignees", "reviewers"}),
            lambda r: {"creator": people_in(r, "author"), "assignees": people_in(r, "assignees"),
                       "reviewers": people_in(r, "reviewers")},
            {"creator": CREATED_BY, "assignees": ASSIGNED_TO, "reviewers": REVIEWED_BY},
        ),
        "TICKET": RecordShape(
            "Ticket", frozenset({"author", "assignee"}),
            lambda r: {"reporter": people_in(r, "author"), "assignees": people_in(r, "assignee")},
            {"reporter": REPORTED_BY, "assignees": ASSIGNED_TO},
        ),
        "COMMENT": _COMMENT,
    }),
    "JIRA": SystemSpec("Jira", frozenset({"project"}), {"TICKET": _REPORTED_TICKET, "COMMENT": _COMMENT}),
    "SERVICENOW": SystemSpec("ServiceNow", frozenset({"desk"}), {
        "TICKET": RecordShape("Ticket", frozenset({"author"}), lambda r: {}, {}),
    }),
    "ZENDESK": SystemSpec("Zendesk", frozenset({"group"}), {"TICKET": _REPORTED_TICKET}),
    "SLACK": SystemSpec("Slack", frozenset({"channel"}), {
        "MESSAGE": RecordShape(
            "Chat message", frozenset({"author"}),
            lambda r: {"involved": people_in(r, "author")}, {"involved": INVOLVED_IN},
        ),
    }),
    "DRIVE": SystemSpec("Google Drive", frozenset({"folder"}), {
        "FILE": RecordShape(
            "Document", frozenset({"author", "owners", "last_modified_by"}), _drive_file,
            {"owners": OWNED_BY, "authored_by": AUTHORED_BY, "last_modified_by": LAST_MODIFIED_BY},
        ),
    }),
    "CONFLUENCE": SystemSpec("Confluence", frozenset({"space"}), {
        "PAGE": RecordShape(
            "Page", frozenset({"author", "last_modified_by"}),
            lambda r: {"authored_by": people_in(r, "author"),
                       "last_modified_by": _first(people_in(r, "last_modified_by"), people_in(r, "author"))},
            {"authored_by": AUTHORED_BY, "last_modified_by": LAST_MODIFIED_BY},
        ),
    }),
    "GMAIL": SystemSpec("Gmail", frozenset({"mailbox"}), {
        "MAIL": RecordShape(
            "Email", frozenset({"from", "to", "cc"}),
            lambda r: {"from": people_in(r, "from"), "to": people_in(r, "to"), "cc": people_in(r, "cc")},
            {"from": AUTHORED_BY, "to": ADDRESSED_TO, "cc": ADDRESSED_TO},
        ),
    }),
    "SALESFORCE": SystemSpec("Salesforce", frozenset({"account"}), {
        "DEAL": RecordShape(
            "Opportunity", frozenset({"owner"}),
            lambda r: {"owner": people_in(r, "owner")}, {"owner": OWNED_BY},
        ),
        "CASE": RecordShape(
            "Case", frozenset({"author", "owner", "reporter"}),
            lambda r: {"creator": people_in(r, "author"), "assignee": people_in(r, "owner"),
                       "reporter": people_in(r, "reporter")},
            {"creator": CREATED_BY, "assignee": ASSIGNED_TO, "reporter": REPORTED_BY},
        ),
    }),
    "KB": SystemSpec("Knowledge base", frozenset({"collection"}), {
        "KB_FILE": RecordShape(
            "Document", frozenset({"author"}),
            lambda r: {"created_by": people_in(r, "author")}, {"created_by": CREATED_BY},
        ),
    }),
}

# Record types a CRM files under an account, which the graph links to the account's organisation.
ACCOUNT_RECORD_TYPES = frozenset({"DEAL", "CASE"})


def shape_of(system: str, record_type: str) -> RecordShape | None:
    spec = SYSTEMS.get(system)
    return spec.shapes.get(record_type) if spec else None


def thread_slots(messages: list[Mapping]) -> Slots:
    """A Slack thread's people: every author, in order of first message."""
    authors: list[str] = []
    for m in messages:
        authors.extend(a for a in people_in(m, "author") if a not in authors)
    return {"involved": authors}
