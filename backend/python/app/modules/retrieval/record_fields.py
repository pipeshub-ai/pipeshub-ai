"""Every field a record query may filter, group or total on, and where the
graph keeps it. See ``docs/record-query.md``.

One declaration serves the query model (which fields and values exist), the
graph providers (which property or edge holds each), the agent tool's
description (the values a question may name) and the eval. A field a record
does not carry is unknown, never "no": the engine reports how many records it
could not judge instead of treating them as non-matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config.constants.arangodb import EntityRelations
from app.models.entities import Priority, Status


class FieldKind(str, Enum):
    VALUE = "value"  # one of a fixed set, matched case-insensitively
    TEXT = "text"
    DATE = "date"  # epoch ms in the graph; whole UTC days in a query
    NUMBER = "number"
    PERSON = "person"
    ORGANIZATION = "organization"
    RECORD = "record"


class Store(str, Enum):
    """Which node or edge holds a field."""

    RECORD = "record"  # the Record node
    TICKET = "ticket"  # the Ticket node the record IS_OF_TYPE
    DEAL = "deal"  # the Deals node the record IS_OF_TYPE
    EDGE = "edge"
    DERIVED = "derived"  # computed from other fields, never stored


@dataclass(frozen=True)
class RecordField:
    name: str
    kind: FieldKind
    store: Store
    property: str | None  # the property name on that node; None for edges and derived fields
    record_types: frozenset[str] | None  # None: every record
    values: tuple[str, ...] = ()
    description: str = ""


DEAL_OUTCOMES = ("WON", "LOST", "OPEN")
_TICKETS = frozenset({"TICKET", "CASE"})
_DEALS = frozenset({"DEAL"})

FIELDS: dict[str, RecordField] = {f.name: f for f in (
    RecordField("source", FieldKind.VALUE, Store.RECORD, "connectorName", None,
                description="the system the record came from, e.g. JIRA, ZENDESK, SALESFORCE"),
    RecordField("record_type", FieldKind.VALUE, Store.RECORD, "recordType", None,
                description="TICKET, CASE, DEAL, FILE, MAIL, MESSAGE, ..."),
    RecordField("container", FieldKind.RECORD, Store.RECORD, "recordGroupId", None,
                description="a project, queue, channel, folder or account; nested ones included"),
    RecordField("created", FieldKind.DATE, Store.RECORD, "sourceCreatedAtTimestamp", None),
    RecordField("updated", FieldKind.DATE, Store.RECORD, "sourceLastModifiedTimestamp", None),
    RecordField("status", FieldKind.VALUE, Store.TICKET, "status", _TICKETS, tuple(s.value for s in Status),
                description="the status normalised across systems"),
    RecordField("source_status", FieldKind.TEXT, Store.TICKET, "sourceStatus", _TICKETS,
                description="the status as the source system names it"),
    RecordField("priority", FieldKind.VALUE, Store.TICKET, "priority", _TICKETS, tuple(p.value for p in Priority)),
    RecordField("resolved", FieldKind.DATE, Store.TICKET, "resolvedAtTimestamp", _TICKETS),
    RecordField("due", FieldKind.DATE, Store.TICKET, "dueDateTimestamp", _TICKETS),
    RecordField("amount", FieldKind.NUMBER, Store.DEAL, "amount", _DEALS),
    RecordField("close_date", FieldKind.DATE, Store.DEAL, "closeDate", _DEALS),
    RecordField("deal_outcome", FieldKind.VALUE, Store.DERIVED, None, _DEALS, DEAL_OUTCOMES,
                description="WON, LOST or OPEN, from the deal's closed and won flags"),
    RecordField("resolution_days", FieldKind.NUMBER, Store.DERIVED, None, _TICKETS,
                description="whole days from created to resolved"),
    RecordField("person", FieldKind.PERSON, Store.EDGE, None, None,
                description="a person in a role on the record"),
    RecordField("organization", FieldKind.ORGANIZATION, Store.EDGE, None, None,
                description="an organisation the record is for or names"),
    RecordField("linked_to", FieldKind.RECORD, Store.EDGE, None, None,
                description="another record this one links to, in either direction"),
)}

# A role a question names, and the edge types that hold it.
ROLE_EDGES: dict[str, tuple[str, ...]] = {
    "assignee": (EntityRelations.ASSIGNED_TO.value,),
    "reporter": (EntityRelations.REPORTED_BY.value,),
    "author": (EntityRelations.CREATED_BY.value, EntityRelations.AUTHORED_BY.value),
    "owner": (EntityRelations.OWNED_BY.value,),
    "reviewer": (EntityRelations.REVIEWED_BY.value,),
    "editor": (EntityRelations.LAST_MODIFIED_BY.value,),
    "recipient": (EntityRelations.ADDRESSED_TO.value,),
    "lead": (EntityRelations.LEAD_BY.value,),
    "participant": (EntityRelations.INVOLVED_IN.value, EntityRelations.MENTIONED_IN.value),
}
PERSON_EDGES: tuple[str, ...] = tuple(dict.fromkeys(e for edges in ROLE_EDGES.values() for e in edges))
ORGANIZATION_EDGES: tuple[str, ...] = (EntityRelations.FOR_ACCOUNT.value, EntityRelations.MENTIONS.value)


def field(name: str) -> RecordField:
    """The declared field ``name``; a ValueError names the fields there are."""
    if name not in FIELDS:
        raise ValueError(f"unknown field {name!r}; fields: {', '.join(sorted(FIELDS))}")
    return FIELDS[name]


def role_edges(role: str | None) -> tuple[str, ...]:
    """The edge types for ``role``; every person edge when no role is given."""
    if role is None:
        return PERSON_EDGES
    if role not in ROLE_EDGES:
        raise ValueError(f"unknown role {role!r}; roles: {', '.join(ROLE_EDGES)}")
    return ROLE_EDGES[role]


__all__ = [
    "DEAL_OUTCOMES",
    "FIELDS",
    "ORGANIZATION_EDGES",
    "PERSON_EDGES",
    "ROLE_EDGES",
    "FieldKind",
    "RecordField",
    "Store",
    "field",
    "role_edges",
]
