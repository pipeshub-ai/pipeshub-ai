"""Links from a record to the people it names (KG-13 slice 1; authorship).

Two sources of people:

- Typed records carry them as fields of their type document: a mail's sender
  and recipients, a ticket's assignee, a pull request's reviewers. One
  extractor per type turns those into links (``_TYPED_LINKS``).
- Any record can carry ``SourcePerson`` values on the base ``Record``: who
  authored, created, last modified and owns it, as the source reports them
  for the current version. Connectors fill them; the generic extractor reads
  them.

Each link is resolved to an organisation member (``users``) by the
connector's user id, then by email. Someone who is not a member gets a
``people`` node when a connector names them in an authorship or ticket role
(decision D2); mail recipients do not, or every outside address on every
mail would become a node. Bcc recipients are never linked.
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
    Person,
    ProjectRecord,
    PullRequestRecord,
    TicketRecord,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable
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
    EntityRelations.LAST_MODIFIED_BY.value,
})

# Roles in which someone who is not a member still gets a person node.
_PERSON_NODE_ROLES = frozenset(EntityRelations) - {EntityRelations.ADDRESSED_TO}

# _PersonResolver._member's answer for a user of another org: no member here,
# and no person node either.
_ANOTHER_ORG = object()

LINKED_RECORD_TYPES = (TicketRecord, ProjectRecord, MailRecord, CommentRecord, PullRequestRecord, DealRecord)


@dataclass(frozen=True)
class PersonLink:
    """One person a record names, by the connector's user id and/or email."""

    edge_type: EntityRelations
    email: str | None = None
    source_id: str | None = None
    source_timestamp: int | None = None
    display_name: str | None = None


class PeopleStore(Protocol):
    async def get_user_by_email(self, email: str) -> User | None: ...
    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> User | None: ...
    async def get_person_by_email(self, email: str, org_id: str) -> Person | None: ...
    async def get_person_by_source_key(self, source_key: str, org_id: str) -> Person | None: ...
    async def upsert_person_by_email(self, person: Person, *, raise_on_error: bool = False) -> str | None: ...
    async def upsert_person_by_source_key(self, person: Person, *, raise_on_error: bool = False) -> str | None: ...
    async def delete_edges_between_collections(
        self, from_id: str, from_collection: str, edge_collection: str, to_collection: str,
    ) -> None: ...
    async def batch_create_entity_relations(self, edges: list[dict]) -> None: ...
    def is_transient_error(self, error: BaseException) -> bool: ...


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


_TYPED_LINKS: dict[type, Callable[[Record], list[PersonLink]]] = {}


def _links_for(record_type: type) -> Callable[[Callable[..., list[PersonLink]]], Callable[..., list[PersonLink]]]:
    def register(extract: Callable[..., list[PersonLink]]) -> Callable[..., list[PersonLink]]:
        _TYPED_LINKS[record_type] = extract
        return extract
    return register


@_links_for(TicketRecord)
def _ticket_links(ticket: TicketRecord) -> list[PersonLink]:
    if ticket.is_email_hidden:
        # The "email" fields then hold the connector's native ids.
        assignee_ids = [sid for sid in ticket.assignee_source_id or [] if sid]
        assignee_name = ticket.assignee if len(assignee_ids) == 1 else None
        links = [
            PersonLink(EntityRelations.ASSIGNED_TO, source_id=sid, display_name=assignee_name,
                       source_timestamp=_first(ticket.assignee_source_timestamp, ticket.source_updated_at))
            for sid in assignee_ids
        ]
        if ticket.reporter_source_id:
            links.append(PersonLink(
                EntityRelations.REPORTED_BY, source_id=ticket.reporter_source_id, display_name=ticket.reporter_name,
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


@_links_for(ProjectRecord)
def _project_links(project: ProjectRecord) -> list[PersonLink]:
    return [PersonLink(EntityRelations.LEAD_BY, email=project.lead_email,
                       source_timestamp=_first(project.source_updated_at, project.source_created_at))]


@_links_for(MailRecord)
def _mail_links(mail: MailRecord) -> list[PersonLink]:
    sent = mail.source_created_at
    return [
        PersonLink(EntityRelations.AUTHORED_BY, email=mail.from_email, source_timestamp=sent,
                   display_name=parseaddr(mail.from_email or "")[0] or None),
        *(PersonLink(EntityRelations.ADDRESSED_TO, email=a, source_timestamp=sent)
          for a in _addresses([*(mail.to_emails or []), *(mail.cc_emails or [])])),
    ]


@_links_for(CommentRecord)
def _comment_links(comment: CommentRecord) -> list[PersonLink]:
    return [PersonLink(EntityRelations.AUTHORED_BY, source_id=comment.author_source_id,
                       source_timestamp=comment.source_created_at)]


@_links_for(PullRequestRecord)
def _pull_request_links(pr: PullRequestRecord) -> list[PersonLink]:
    return [
        PersonLink(EntityRelations.CREATED_BY, email=pr.creator_email, source_timestamp=pr.source_created_at),
        *(PersonLink(EntityRelations.ASSIGNED_TO, email=a, source_timestamp=pr.source_updated_at)
          for a in pr.assignee_email or []),
        *(PersonLink(EntityRelations.REVIEWED_BY, email=r, source_timestamp=pr.source_updated_at)
          for r in pr.review_email or []),
    ]


@_links_for(DealRecord)
def _deal_links(deal: DealRecord) -> list[PersonLink]:
    return [PersonLink(EntityRelations.OWNED_BY, source_id=deal.owner_id, source_timestamp=deal.source_updated_at)]


def _source_people_links(record: Record) -> list[PersonLink]:
    """Links from the base record's SourcePerson fields (any record type)."""
    created = record.source_created_at
    updated = _first(record.source_updated_at, record.source_created_at)
    named = [
        (EntityRelations.AUTHORED_BY, record.authored_by, created),
        (EntityRelations.CREATED_BY, record.created_by, created),
        (EntityRelations.LAST_MODIFIED_BY, record.last_modified_by, updated),
        *((EntityRelations.OWNED_BY, owner, updated) for owner in record.owners),
    ]
    return [
        PersonLink(role, email=person.email, source_id=person.source_id,
                   source_timestamp=timestamp, display_name=person.display_name)
        for role, person, timestamp in named
        if person is not None and person.identifiable
    ]


def person_links(record: Record) -> list[PersonLink]:
    """The people ``record`` names, by role, before resolution."""
    typed = next((_TYPED_LINKS[cls] for cls in type(record).__mro__ if cls in _TYPED_LINKS), None)
    return [*(typed(record) if typed else []), *_source_people_links(record)]


class _PersonResolver:
    """Resolves links to ``(collection, key)`` for one record: a member by the
    connector's user id, then by email; otherwise, in a role that warrants
    it, a person node. Each identity is resolved once per record."""

    def __init__(self, record: Record, store: PeopleStore) -> None:
        self._record = record
        self._store = store
        self._resolved: dict[tuple[str, str], tuple[str, str] | None] = {}
        self.failed = 0

    @staticmethod
    def identity(link: PersonLink) -> tuple[str, str]:
        return (link.source_id or "", _address(link.email) or "")

    @property
    def lookups(self) -> int:
        return len(self._resolved)

    def known(self, link: PersonLink) -> bool:
        return self.identity(link) in self._resolved

    async def resolve(self, link: PersonLink) -> tuple[str, str] | None:
        identity = self.identity(link)
        if identity not in self._resolved:
            try:
                member = await self._member(*identity)
                self._resolved[identity] = (
                    None if member is _ANOTHER_ORG else member or await self._person(link, *identity)
                )
            except Exception as exc:
                if self._store.is_transient_error(exc) is True:
                    # A lock held by a concurrent sync: fail the record so its
                    # transaction is retried, rather than commit it without the edge.
                    raise
                # Otherwise one unresolved person must not drop the others.
                self.failed += 1
                self._resolved[identity] = None
        return self._resolved[identity]

    async def _member(self, source_id: str, email: str) -> tuple[str, str] | object | None:
        user = None
        if source_id:
            user = await self._store.get_user_by_source_id(source_id, self._record.connector_id)
        if user is None and email:
            user = await self._store.get_user_by_email(email)
        if user is None or not user.id:
            return None
        user_org = getattr(user, "org_id", None)
        if user_org is not None and user_org != self._record.org_id:
            # Email lookups are not org-scoped; another tenant's member sharing
            # an address is not this record's person, and recording the
            # address as an outsider here could misfile this org's own member.
            return _ANOTHER_ORG
        return CollectionNames.USERS.value, user.id

    async def _person(self, link: PersonLink, source_id: str, email: str) -> tuple[str, str] | None:
        if link.edge_type not in _PERSON_NODE_ROLES or not (source_id or email):
            return None
        org_id = self._record.org_id
        source_key = f"{self._record.connector_id}:{source_id}" if source_id else None
        # Read first: an upsert of a node that exists still takes its write lock
        # inside the sync transaction, so popular outsiders would serialise syncs.
        existing = (
            source_key and await self._store.get_person_by_source_key(source_key, org_id)
        ) or (email and await self._store.get_person_by_email(email, org_id))
        if existing:
            return CollectionNames.PEOPLE.value, existing.id
        person = Person(email=email or None, source_key=source_key, org_id=org_id, full_name=link.display_name)
        key = await (
            self._store.upsert_person_by_email(person, raise_on_error=True) if email
            else self._store.upsert_person_by_source_key(person, raise_on_error=True)
        )
        return (CollectionNames.PEOPLE.value, key) if key else None


async def link_record_people(
    record: Record, store: PeopleStore, logger: Logger, *, may_have_edges: bool = True,
) -> int:
    """Replace ``record``'s person edges with the people it names now.

    Idempotent: the record's existing edges to members and to person nodes
    are removed and the current ones written, so a re-sync that drops an
    assignee drops the edge. Its other ``entityRelations`` edges
    (organisations) are left to their own writers. Returns the number of
    edges written. A failed lookup or person write skips that person (logged
    by record id, never by address); the others are still linked, unless it
    collided with a concurrent sync, which fails the record for a retry.

    ``may_have_edges`` is False for a record being created: one that names
    nobody then costs no query. An existing record is always cleared, so
    one that stops naming anyone loses its old edges.
    """
    links = person_links(record)
    if not links and not may_have_edges and not isinstance(record, LINKED_RECORD_TYPES):
        return 0
    for target in (CollectionNames.USERS.value, CollectionNames.PEOPLE.value):
        try:
            await store.delete_edges_between_collections(
                record.id, CollectionNames.RECORDS.value, CollectionNames.ENTITY_RELATIONS.value, target,
            )
        except Exception as exc:  # stale edges are rewritten below
            logger.warning("Could not clear %s edges of record %s: %s", target, record.id, exc)

    resolver = _PersonResolver(record, store)
    seen: set[tuple[str, str, str]] = set()
    edges: list[dict] = []
    now = get_epoch_timestamp_in_ms()
    for link in links:
        if len(edges) >= MAX_LINKED_PEOPLE:
            logger.info("Record %s names more than %d people; linking the first", record.id, MAX_LINKED_PEOPLE)
            break
        if not any(resolver.identity(link)):
            continue
        if not resolver.known(link) and resolver.lookups >= MAX_PERSON_LOOKUPS:
            logger.info("Record %s names more than %d people; looked up the first", record.id, MAX_PERSON_LOOKUPS)
            break
        target = await resolver.resolve(link)
        if target is None or (*target, link.edge_type.value) in seen:
            continue
        seen.add((*target, link.edge_type.value))
        edge = {
            "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
            "_to": f"{target[0]}/{target[1]}",
            "edgeType": link.edge_type.value,
            "origin": EntityOrigin.INFERRED.value,
            "source": record.connector_id,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        if link.source_timestamp is not None:
            edge["sourceTimestamp"] = link.source_timestamp
        edges.append(edge)

    if resolver.failed:
        logger.warning("Record %s: %d person lookups failed", record.id, resolver.failed)
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
