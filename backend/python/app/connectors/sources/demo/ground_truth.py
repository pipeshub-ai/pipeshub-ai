"""The knowledge-graph facts a demo sync must produce, derived from the fixture alone.

``expected_graph(fx)`` is what the graph should hold once the demo connector
has synced ``fx`` through the entity processor, so a test or the evaluation
harness can compare the two. Records are named by fixture id (a Slack thread
by its thread id), people and organisations by fixture id.

It assumes every fixture member has an org account or the user row the sync
writes for them; a sign-in persona with no account is left out of the sync
and would show up as a missing edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

try:
    from app.connectors.sources.demo.fixture_loader import split_threads
    from app.connectors.sources.demo.systems import (
        ACCOUNT_RECORD_TYPES,
        ADDRESSED_TO,
        INVOLVED_IN,
        shape_of,
        thread_slots,
    )
except ImportError:  # run as a script by the harness, with this folder on the path
    from fixture_loader import split_threads  # type: ignore[no-redef]
    from systems import (  # type: ignore[no-redef]
        ACCOUNT_RECORD_TYPES,
        ADDRESSED_TO,
        INVOLVED_IN,
        shape_of,
        thread_slots,
    )

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from app.connectors.sources.demo.systems import Slots

# Edges that only ever reach an org member: record_people gives no person node
# to an outside mail recipient, and a Slack participant is resolved by the
# connector's user id alone.
MEMBER_ONLY_EDGES = frozenset({ADDRESSED_TO, INVOLVED_IN})


@dataclass(frozen=True)
class ExpectedGraph:
    # (record id, entityRelations edge type, person id). INVOLVED_IN is stored
    # from the member to the record; every other edge from the record.
    person_edges: frozenset[tuple[str, str, str]]
    # (record id, organisation id): the record's FOR_ACCOUNT edge.
    account_links: frozenset[tuple[str, str]]
    # (container id, organisation id): the account record group's dealOf edge.
    account_groups: frozenset[tuple[str, str]]
    # Fixture members: each has a user the sync links to.
    members: frozenset[str]
    # External people some record names in a role that gives them a person node.
    external_people: frozenset[str]
    # CRM organisations synced as external organisations.
    organizations: frozenset[str]
    # (record id, organisation id) the record's text names. Extraction's to
    # find, not the sync's: no structured field carries it.
    mentioned_organizations: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class EmittedRecord:
    """One record the connector emits: a standalone fixture record or a whole thread."""

    id: str
    type: str
    container: Mapping[str, Any]
    slots: Slots
    mentions_orgs: tuple[str, ...]


def emitted_records(fx: Mapping[str, Any]) -> Iterator[EmittedRecord]:
    containers = {c["id"]: c for c in fx["containers"]}
    singles, threads = split_threads(fx)
    for rec in singles:
        container = containers[rec["container"]]
        shape = shape_of(container["system"], rec["type"])
        yield EmittedRecord(
            rec["id"], rec["type"], container, shape.slots(rec) if shape else {},
            tuple(rec.get("mentions_orgs", [])),
        )
    thread_info = {t["id"]: t for t in fx.get("threads", [])}
    for tid, messages in threads.items():
        thread = thread_info[tid]
        mentions = dict.fromkeys(org for m in messages for org in m.get("mentions_orgs", []))
        yield EmittedRecord(tid, "MESSAGE", containers[thread["container"]], thread_slots(messages), tuple(mentions))


def expected_graph(fx: Mapping[str, Any]) -> ExpectedGraph:
    """The people, account and organisation facts a sync of ``fx`` writes."""
    people = {p["id"]: p for p in fx["people"]}
    members = frozenset(pid for pid, p in people.items() if p.get("kind", "member") == "member")
    edges: set[tuple[str, str, str]] = set()
    accounts: set[tuple[str, str]] = set()
    mentions: set[tuple[str, str]] = set()
    for rec in emitted_records(fx):
        mentions.update((rec.id, org) for org in rec.mentions_orgs)
        edges.update(_person_edges(rec, members))
        organization = rec.container.get("organization")
        if organization and rec.type in ACCOUNT_RECORD_TYPES:
            accounts.add((rec.id, organization))
    crm = frozenset(o["id"] for o in fx.get("organizations", []) if o.get("crm"))
    return ExpectedGraph(
        person_edges=frozenset(edges),
        account_links=frozenset(accounts),
        account_groups=frozenset(
            (c["id"], c["organization"]) for c in fx["containers"] if c.get("organization") in crm
        ),
        members=members,
        external_people=frozenset(pid for _, _, pid in edges if pid not in members),
        organizations=crm,
        mentioned_organizations=frozenset(mentions),
    )


def _person_edges(rec: EmittedRecord, members: frozenset[str]) -> Iterator[tuple[str, str, str]]:
    shape = shape_of(rec.container["system"], rec.type)
    edge_of = shape.edges if shape else {}
    for slot, person_ids in rec.slots.items():
        edge = edge_of[slot]
        for pid in person_ids:
            if pid in members or edge not in MEMBER_ONLY_EDGES:
                yield rec.id, edge, pid
