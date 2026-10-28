"""The demo sync names people and accounts the way the real connectors do.

Runs ``DemoConnector.run_sync`` over the bundled fixture (with its
``acme-corp.d`` extensions) against a recording processor and data store, then
reads each emitted record back through ``record_people.person_links`` (the
same extractor production uses) and checks the result against
``expected_graph``: every expected person link is on the record's own fields,
and nothing else is, except mail recipients outside the org, which
``record_people`` deliberately drops.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, RecordRelations
from app.connectors.core.base.data_processor.record_people import person_links
from app.connectors.sources.demo import connector as demo_connector
from app.connectors.sources.demo.fixture_loader import load_fixture
from app.connectors.sources.demo.ground_truth import expected_graph
from app.connectors.sources.demo.systems import ADDRESSED_TO, INVOLVED_IN
from app.models.entities import MessageRecord, RecordType

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.models.entities import AppUser, Record, RecordGroup

ORG_ID = "org-demo"
CONNECTOR_ID = "demo-1"


class RecordingProcessor:
    """What the demo sync hands the entity processor, kept for inspection."""

    def __init__(self) -> None:
        self.org_id = ORG_ID
        self.app_users: list[AppUser] = []
        self.record_groups: list[RecordGroup] = []
        self.records: list[Record] = []

    async def get_user_by_email(self, email: str) -> object:
        return SimpleNamespace(is_active=True, email=email)

    async def get_user_by_user_id(self, user_id: str) -> None:
        return None

    async def on_new_app_users(self, users: list[AppUser]) -> None:
        self.app_users.extend(users)

    async def on_new_user_groups(self, memberships: list) -> None:
        return None

    async def on_new_record_groups(self, batch: list) -> None:
        # As the processor does: a group keeps the id it was first stored under.
        stored = {g.external_group_id: g.id for g in self.record_groups}
        for group, _ in batch:
            group.id = stored.get(group.external_group_id) or str(uuid.uuid4())
            self.record_groups.append(group)

    async def on_new_records(self, batch: list) -> None:
        self.records.extend(record for record, _ in batch)

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Record | None:
        return next((r for r in self.records if r.external_record_id == external_id), None)

    async def create_record_relation(self, from_id: str, to_id: str, relation: str) -> None:
        assert relation in {r.value for r in RecordRelations}


class RecordingGraph:
    """The graph calls the sync makes directly, as the Salesforce connector does."""

    def __init__(self) -> None:
        self.orgs: dict[str, dict] = {}
        self.edges: dict[str, list[dict]] = {}
        self.deleted: list[tuple] = []

    async def get_all_orgs(self, is_external: bool | None = None) -> list[dict]:
        return list(self.orgs.values())

    async def batch_upsert_orgs(self, orgs: list[dict]) -> None:
        for org in orgs:
            self.orgs[org["_key"]] = org

    async def batch_create_edges(self, edges: list[dict], collection: str) -> None:
        self.edges.setdefault(collection, []).extend(edges)

    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str) -> None:
        self.deleted.append(("from", from_id, collection))
        self.edges[collection] = [e for e in self.edges.get(collection, []) if e["from_id"] != from_id]

    async def delete_edges_to(self, to_id: str, to_collection: str, collection: str) -> None:
        self.deleted.append(("to", to_id, collection))
        self.edges[collection] = [e for e in self.edges.get(collection, []) if e["to_id"] != to_id]


class RecordingStore:
    def __init__(self) -> None:
        self.graph = RecordingGraph()

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[RecordingGraph]:
        yield self.graph


async def _sync(fx: dict[str, Any] | None = None) -> tuple[demo_connector.DemoConnector, RecordingProcessor, RecordingStore]:
    processor, store = RecordingProcessor(), RecordingStore()
    connector = demo_connector.DemoConnector(
        logging.getLogger("demo-coverage"), processor, store, MagicMock(), CONNECTOR_ID,
    )
    if fx is not None:
        connector._fixture = fx
        connector._bodies = demo_connector.render_bodies(fx)
    await connector.run_sync()
    return connector, processor, store


@pytest.fixture(scope="module")
def fx() -> dict[str, Any]:
    return load_fixture()


@pytest.fixture
async def synced() -> tuple[demo_connector.DemoConnector, RecordingProcessor, RecordingStore]:
    return await _sync()


def _person_of(fx: dict[str, Any]) -> dict[str, str]:
    """Person id by every handle a record field uses: the fixture id (the source user id) and the email."""
    handles: dict[str, str] = {}
    for p in fx["people"]:
        handles[p["id"]] = p["id"]
        handles[p["email"].lower()] = p["id"]
    return handles


def _emitted_people(records: list[Record], fx: dict[str, Any]) -> set[tuple[str, str, str]]:
    who = _person_of(fx)
    emitted: set[tuple[str, str, str]] = set()
    for record in records:
        for link in person_links(record):
            handle = link.source_id or (link.email or "").lower()
            if not handle:
                continue  # an empty role (a ticket nobody is assigned to); record_people skips it too
            emitted.add((record.external_record_id, link.edge_type.value, who[handle]))
        if isinstance(record, MessageRecord):
            emitted.update(
                (record.external_record_id, INVOLVED_IN, who[sid]) for sid in record.involved_user_source_ids or []
            )
    return emitted


async def test_every_person_link_the_fixture_implies_is_on_the_record(synced, fx) -> None:
    _, processor, _ = synced
    truth = expected_graph(fx)
    emitted = _emitted_people(processor.records, fx)

    assert truth.person_edges - emitted == set()
    # record_people drops these: an outside recipient gets no person node.
    extra = emitted - truth.person_edges
    assert all(edge == ADDRESSED_TO and pid not in truth.members for _, edge, pid in extra), sorted(extra)


async def test_the_sample_covers_every_role_of_every_new_system(fx) -> None:
    truth = expected_graph(fx)
    by_record = {(rid, edge) for rid, edge, _ in truth.person_edges}
    assert {
        ("confluence-e0-ledger-adr", "AUTHORED_BY"), ("confluence-e0-ledger-adr", "LAST_MODIFIED_BY"),
        ("gmail-e0-vandelay-renewal", "AUTHORED_BY"), ("gmail-e0-vandelay-renewal", "ADDRESSED_TO"),
        ("sf-e0-vandelay-renewal", "OWNED_BY"),
        ("sf-e0-vandelay-case", "CREATED_BY"), ("sf-e0-vandelay-case", "ASSIGNED_TO"),
        ("sf-e0-vandelay-case", "REPORTED_BY"),
        ("kb-e0-export-faq", "CREATED_BY"),
        ("gh-pr-e0-export-batch", "REVIEWED_BY"), ("gh-pr-e0-export-batch", "ASSIGNED_TO"),
        ("jira-e0-sup-140", "REPORTED_BY"), ("jira-e0-sup-140", "ASSIGNED_TO"),
        ("drive-e0-export-rollout", "OWNED_BY"), ("drive-e0-export-rollout", "LAST_MODIFIED_BY"),
    } <= by_record
    # The external contact reaches the graph as the case's reporter, not as a mail recipient.
    assert ("sf-e0-vandelay-case", "REPORTED_BY", "gina") in truth.person_edges
    assert ("gmail-e0-vandelay-renewal", "ADDRESSED_TO", "gina") not in truth.person_edges
    assert "gina" in truth.external_people
    assert {("sf-e0-vandelay-renewal", "vandelay"), ("sf-e0-vandelay-case", "vandelay")} <= truth.account_links
    assert "vandelay" in truth.organizations
    assert ("confluence-e0-ledger-adr", "initech") in truth.mentioned_organizations


async def test_external_people_are_never_app_users(synced, fx) -> None:
    _, processor, _ = synced
    externals = {p["email"] for p in fx["people"] if p.get("kind") == "external"}
    members = {p["email"] for p in fx["people"] if p.get("kind", "member") == "member"}
    synced_emails = {u.email for u in processor.app_users}
    assert externals and not externals & synced_emails
    assert members <= synced_emails


async def test_a_servicenow_record_names_nobody(synced, fx) -> None:
    # The real ServiceNow connector syncs knowledge articles and puts no people on them.
    _, processor, _ = synced
    servicenow = [r for r in processor.records if r.connector_name == Connectors.SERVICENOW]
    assert servicenow
    assert not [link for r in servicenow for link in person_links(r) if link.email or link.source_id]


async def test_crm_accounts_become_external_organisations_linked_to_their_record_group(synced, fx) -> None:
    _, processor, store = synced
    truth = expected_graph(fx)
    names = {o["id"]: o["name"] for o in fx["organizations"]}
    orgs_by_name = {o["name"]: o for o in store.graph.orgs.values()}
    assert set(orgs_by_name) == {names[o] for o in truth.organizations}
    assert all(o["isExternal"] is True and o["parentOrgId"] == ORG_ID for o in orgs_by_name.values())

    groups = {g.external_group_id: g for g in processor.record_groups}
    deal_of = {(e["from_id"], e["to_id"]) for e in store.graph.edges[CollectionNames.DEAL_OF.value]}
    assert deal_of == {
        (groups[container].id, orgs_by_name[names[org]]["_key"]) for container, org in truth.account_groups
    }
    prospects = store.graph.edges[CollectionNames.PROSPECT.value]
    assert {(e["from_id"], e["to_id"]) for e in prospects} == {
        (ORG_ID, orgs_by_name[names[org]]["_key"]) for org in truth.organizations
    }

    by_id = {r.external_record_id: r for r in processor.records}
    for record_id, org in truth.account_links:
        record = by_id[record_id]
        assert record.record_type in {RecordType.DEAL, RecordType.CASE}
        assert (record.record_group_id, orgs_by_name[names[org]]["_key"]) in deal_of


async def test_a_resync_keeps_each_account_organisation_and_its_single_link(fx) -> None:
    connector, processor, store = await _sync()
    keys = set(store.graph.orgs)
    await connector.run_sync()
    assert set(store.graph.orgs) == keys
    deal_of = store.graph.edges[CollectionNames.DEAL_OF.value]
    assert len(deal_of) == len({(e["from_id"], e["to_id"]) for e in deal_of}) == len(expected_graph(fx).account_groups)


def _body(fx: dict[str, Any], record_id: str) -> str:
    return demo_connector.render_bodies(fx)[record_id]


@pytest.mark.parametrize("record_id", ["drive-e0-export-rollout", "drive-billing-resilience"])
def test_a_drive_document_header_does_not_print_an_author_line(fx, record_id: str) -> None:
    # A document's own text may name its author; the header the demo adds must not.
    rec = next(r for r in fx["records"] if r["id"] == record_id)
    header = _body(fx, record_id).replace(rec["body"].rstrip(), "")
    assert "**Author:**" not in header


def test_a_mail_shows_its_from_to_and_cc_headers(fx) -> None:
    body = _body(fx, "gmail-e0-vandelay-renewal")
    assert "**From:** Omar Haddad <omar@acme-demo.example>" in body
    assert "**To:** Gina Moretti <gina.moretti@vandelay.example>" in body
    assert "**Cc:** Lena Fischer <lena@acme-demo.example>" in body


def test_a_jira_ticket_shows_its_reporter_and_assignee(fx) -> None:
    body = _body(fx, "jira-e0-sup-140")
    assert "**Reporter:** Bob Okafor" in body
    assert "**Assignee:** Priya Natarajan" in body


def test_a_pull_request_shows_its_author_and_reviewers(fx) -> None:
    body = _body(fx, "gh-pr-e0-export-batch")
    assert "**Author:** Priya Natarajan" in body
    assert "**Reviewers:** Dana Okafor, Marcus Webb" in body


def test_golden_answers_still_appear_in_the_records_they_cite(fx) -> None:
    # The golden questions' required names and figures must stay in the text of
    # the records they cite, whatever the per-system header now shows.
    bodies = demo_connector.render_bodies(fx)
    questions = list(fx["questions"]) + [q for qs in fx["pack_questions"].values() for q in qs]
    for q in questions:
        cited = [*q.get("must_cite", []), *q.get("must_cite_any_of", []), *q.get("stretch_cite_any_of", [])]
        text = "\n".join(bodies[r] for r in cited if r in bodies).lower()
        missing = [t for t in q.get("answer_must_mention", []) if t.lower() not in text]
        assert not missing, (q["id"], missing)
