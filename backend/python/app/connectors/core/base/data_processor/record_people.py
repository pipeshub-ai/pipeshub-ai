"""Links from a record to the organisation members it names (KG-13, slice 1).

Connectors store the people a record involves as strings on its type
document: a mail's sender and recipients, a ticket's assignee, a pull
request's reviewers. This module turns them into ``entityRelations`` edges
from the record to the matching ``users`` node, so a person can be reached
from the records they appear in.

Only existing members are linked (decision D-31): an address or source id
with no matching user gets no edge and creates no node. Bcc recipients are
never linked, since they are hidden from the other recipients.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from typing import TYPE_CHECKING, Protocol

from app.config.constants.arangodb import CollectionNames, EntityOrigin, EntityRelations
from app.models.entities import (
    CommentRecord,
    DealRecord,
    MailRecord,
    ProjectRecord,
    PullRequestRecord,
    TicketRecord,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.models.entities import Record, User

# Edges per record. A mail to an all-hands list would otherwise write one
# edge per member; past this the record links the first ones only.
MAX_LINKED_PEOPLE = 100
# Distinct people looked up per record. Each lookup is a query inside the
# record's sync transaction, and most recipients of a large mail are not
# members, so the edge cap alone does not bound the work.
MAX_PERSON_LOOKUPS = 200

# Edge types added with this module. During a rolling deploy an older pod can
# restore an ArangoDB schema whose edge-type enum lacks them; these edges are
# derived data that a later sync or the backfill repairs, so a rejected write
# of them is logged, not raised, and never fails the record's sync.
_NEW_EDGE_TYPES = frozenset({
    EntityRelations.AUTHORED_BY.value, EntityRelations.ADDRESSED_TO.value,
    EntityRelations.REVIEWED_BY.value, EntityRelations.OWNED_BY.value,
})

LINKED_RECORD_TYPES = (TicketRecord, ProjectRecord, MailRecord, CommentRecord, PullRequestRecord, DealRecord)


@dataclass(frozen=True)
class PersonLink:
    """One person a record names, by email or by the connector's user id."""

    edge_type: EntityRelations
    email: str | None = None
    source_id: str | None = None
    source_timestamp: int | None = None


class PeopleStore(Protocol):
    async def get_user_by_email(self, email: str) -> User | None: ...
    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> User | None: ...
    async def delete_edges_between_collections(
        self, from_id: str, from_collection: str, edge_collection: str, to_collection: str,
    ) -> None: ...
    async def batch_create_entity_relations(self, edges: list[dict]) -> None: ...


def _address(raw: str | None) -> str | None:
    """The bare address in ``raw`` ("Name <a@b.com>" or "a@b.com"), lowercased."""
    if not raw:
        return None
    address = parseaddr(raw)[1].strip().lower()
    return address if "@" in address else None


def _addresses(raw: list[str]) -> list[str]:
    """Bare addresses from a recipient list. Parsed as one header, since a
    connector that split the header on every comma leaves a display name
    with a comma ("Doe, John" <j@x.com>) in two pieces."""
    found = getaddresses([", ".join(r for r in raw if r)])
    return [a.strip().lower() for _, a in found if "@" in a]


def _first(*values: int | None) -> int | None:
    return next((v for v in values if v is not None), None)


def _ticket_links(ticket: TicketRecord) -> list[PersonLink]:
    if ticket.is_email_hidden:
        # The "email" fields then hold the connector's native ids.
        links = [
            PersonLink(EntityRelations.ASSIGNED_TO, source_id=sid,
                       source_timestamp=_first(ticket.assignee_source_timestamp, ticket.source_updated_at))
            for sid in ticket.assignee_source_id or [] if sid
        ]
        if ticket.reporter_source_id:
            links.append(PersonLink(
                EntityRelations.REPORTED_BY, source_id=ticket.reporter_source_id,
                source_timestamp=_first(ticket.reporter_source_timestamp, ticket.source_created_at),
            ))
        return links
    return [
        PersonLink(EntityRelations.ASSIGNED_TO, email=ticket.assignee_email,
                   source_timestamp=_first(ticket.assignee_source_timestamp, ticket.source_updated_at)),
        PersonLink(EntityRelations.CREATED_BY, email=ticket.creator_email,
                   source_timestamp=_first(ticket.creator_source_timestamp, ticket.source_created_at)),
        PersonLink(EntityRelations.REPORTED_BY, email=ticket.reporter_email,
                   source_timestamp=_first(ticket.reporter_source_timestamp, ticket.source_created_at)),
    ]


def person_links(record: Record) -> list[PersonLink]:
    """The people ``record`` names, by role, before resolution to users."""
    if isinstance(record, TicketRecord):
        return _ticket_links(record)
    if isinstance(record, ProjectRecord):
        return [PersonLink(EntityRelations.LEAD_BY, email=record.lead_email,
                           source_timestamp=_first(record.source_updated_at, record.source_created_at))]
    if isinstance(record, MailRecord):
        sent = record.source_created_at
        return [
            PersonLink(EntityRelations.AUTHORED_BY, email=record.from_email, source_timestamp=sent),
            *(PersonLink(EntityRelations.ADDRESSED_TO, email=a, source_timestamp=sent)
              for a in _addresses([*(record.to_emails or []), *(record.cc_emails or [])])),
        ]
    if isinstance(record, CommentRecord):
        return [PersonLink(EntityRelations.AUTHORED_BY, source_id=record.author_source_id,
                           source_timestamp=record.source_created_at)]
    if isinstance(record, PullRequestRecord):
        return [
            PersonLink(EntityRelations.CREATED_BY, email=record.creator_email,
                       source_timestamp=record.source_created_at),
            *(PersonLink(EntityRelations.ASSIGNED_TO, email=a, source_timestamp=record.source_updated_at)
              for a in record.assignee_email or []),
            *(PersonLink(EntityRelations.REVIEWED_BY, email=r, source_timestamp=record.source_updated_at)
              for r in record.review_email or []),
        ]
    if isinstance(record, DealRecord):
        return [PersonLink(EntityRelations.OWNED_BY, source_id=record.owner_id,
                           source_timestamp=record.source_updated_at)]
    return []


async def link_record_people(record: Record, store: PeopleStore, logger: Logger) -> int:
    """Replace ``record``'s person edges with the members it names now.

    Idempotent: the record's existing edges to members are removed and the
    current ones written, so a re-sync that drops an assignee drops the
    edge. Its other ``entityRelations`` edges (organisations) are left to
    their own writers. Returns the number of edges written. A lookup failure skips
    that person (logged by record id, never by address); the others are
    still linked.
    """
    if not isinstance(record, LINKED_RECORD_TYPES):
        return 0
    try:
        await store.delete_edges_between_collections(
            record.id, CollectionNames.RECORDS.value,
            CollectionNames.ENTITY_RELATIONS.value, CollectionNames.USERS.value,
        )
    except Exception as exc:  # stale edges are rewritten below
        logger.warning("Could not clear person edges of record %s: %s", record.id, exc)

    users_by_identity: dict[tuple[str, str], User | None] = {}
    seen: set[tuple[str, str]] = set()
    edges: list[dict] = []
    now = get_epoch_timestamp_in_ms()
    failed = 0
    for link in person_links(record):
        if len(edges) >= MAX_LINKED_PEOPLE:
            logger.info("Record %s names more than %d people; linking the first", record.id, MAX_LINKED_PEOPLE)
            break
        identity = ("email", _address(link.email) or "") if link.email is not None else ("source", link.source_id or "")
        if not identity[1]:
            continue
        if identity not in users_by_identity:
            if len(users_by_identity) >= MAX_PERSON_LOOKUPS:
                logger.info("Record %s names more than %d people; looked up the first", record.id, MAX_PERSON_LOOKUPS)
                break
            try:
                users_by_identity[identity] = (
                    await store.get_user_by_email(identity[1]) if identity[0] == "email"
                    else await store.get_user_by_source_id(identity[1], record.connector_id)
                )
            except Exception:  # one unresolved person must not drop the others
                failed += 1
                users_by_identity[identity] = None
        user = users_by_identity[identity]
        if user is None or not user.id or (user.id, link.edge_type.value) in seen:
            continue
        user_org = getattr(user, "org_id", None)
        if user_org is not None and user_org != record.org_id:
            # Email lookups are not org-scoped; another tenant's member
            # sharing an address is not this record's person.
            continue
        seen.add((user.id, link.edge_type.value))
        edge = {
            "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
            "_to": f"{CollectionNames.USERS.value}/{user.id}",
            "edgeType": link.edge_type.value,
            "origin": EntityOrigin.INFERRED.value,
            "source": record.connector_id,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        if link.source_timestamp is not None:
            edge["sourceTimestamp"] = link.source_timestamp
        edges.append(edge)

    if failed:
        logger.warning("Record %s: %d person lookups failed", record.id, failed)
    established = [e for e in edges if e["edgeType"] not in _NEW_EDGE_TYPES]
    added = [e for e in edges if e["edgeType"] in _NEW_EDGE_TYPES]
    if established:
        await store.batch_create_entity_relations(established)
    if added:
        try:
            await store.batch_create_entity_relations(added)
        except Exception as exc:  # see _NEW_EDGE_TYPES
            logger.warning("Record %s: %d person edges not written: %s", record.id, len(added), type(exc).__name__)
            return len(established)
    return len(edges)
