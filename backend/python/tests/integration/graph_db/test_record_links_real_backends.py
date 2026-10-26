"""Records found by the web address another record pastes, on real Neo4j and ArangoDB.

A Slack message that pastes a pull request's address is joined to the pull
request (get_records_by_weburls), and a pull request indexed after the message
finds the message through the message's link record
(get_records_linking_to_weburls). Both read the (webUrl, orgId) index.

Seeded per test: the pull request; a Jira ticket and an attachment that shares
its address; a link record, a placeholder, a deleted record and another org's
record at the same addresses; and a Slack message holding a link record.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordRelations,
)
from app.models.entities import (
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    MessageRecord,
    PullRequestRecord,
    Record,
    RecordType,
    TicketRecord,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "record_links_it"
PR = "https://github.com/acme/api/pull/3081"
JIRA = "https://acme.atlassian.net/browse/PA-1718"
SLACK = Connectors.SLACK_WORKSPACE.value

logger = logging.getLogger("record-links-it")


@dataclass
class _Graph:
    graph: IGraphDBProvider
    org_id: str
    ids: dict[str, str]


def _base(org_id: str, name: str, connector: Connectors, weburl: str | None) -> dict:
    return {
        "id": str(uuid.uuid4()), "org_id": org_id, "record_name": name,
        "external_record_id": f"ext-{name}-{uuid.uuid4().hex[:6]}", "version": 1,
        "origin": OriginTypes.CONNECTOR, "connector_name": connector,
        "connector_id": f"conn-{connector.name}", "weburl": weburl,
        "mime_type": MimeTypes.BLOCKS.value,
    }


def _records(org_id: str, other_org: str) -> dict[str, Record]:
    def link(name: str, url: str, connector: Connectors = Connectors.SLACK_WORKSPACE) -> LinkRecord:
        return LinkRecord(
            **{**_base(org_id, name, connector, url), "mime_type": MimeTypes.MARKDOWN.value},
            record_type=RecordType.LINK, url=url, is_public=LinkPublicStatus.UNKNOWN,
            is_dependent_node=True,
        )

    return {
        "pr": PullRequestRecord(**_base(org_id, "pr", Connectors.GITHUB, PR), record_type=RecordType.PULL_REQUEST),
        "ticket": TicketRecord(**_base(org_id, "ticket", Connectors.JIRA, JIRA), record_type=RecordType.TICKET),
        "attachment": FileRecord(
            **_base(org_id, "attachment", Connectors.JIRA, JIRA), record_type=RecordType.FILE,
            is_file=True, is_dependent_node=True,
        ),
        "placeholder": TicketRecord(
            **_base(org_id, "placeholder", Connectors.JIRA, PR), record_type=RecordType.TICKET,
            is_placeholder=True,
        ),
        "deleted": TicketRecord(
            **_base(org_id, "deleted", Connectors.JIRA, PR), record_type=RecordType.TICKET,
            is_deleted=True,
        ),
        "other_org": PullRequestRecord(
            **_base(other_org, "other", Connectors.GITHUB, PR), record_type=RecordType.PULL_REQUEST,
        ),
        "message": MessageRecord(
            **_base(org_id, "message", Connectors.SLACK_WORKSPACE, "https://acme.slack.com/archives/C1/p1"),
            record_type=RecordType.MESSAGE,
        ),
        "link": link("link", PR + "/files"),
        "other_link": link("other_link", PR + "/files", Connectors.LINEAR),
        "server_ticket": TicketRecord(
            **_base(org_id, "server_ticket", Connectors.JIRA_DATA_CENTER, "https://jira.corp.example/jira/browse/OPS-9"),
            record_type=RecordType.TICKET,
        ),
        "github_issue": TicketRecord(
            **_base(org_id, "github_issue", Connectors.GITHUB, "https://github.com/acme/api/issues/9"),
            record_type=RecordType.TICKET,
        ),
        "other_org_ticket": TicketRecord(
            **_base(other_org, "other_org_ticket", Connectors.JIRA, "https://elsewhere.atlassian.net/browse/X-1"),
            record_type=RecordType.TICKET,
        ),
        "other_holder": MessageRecord(
            **_base(org_id, "other_holder", Connectors.LINEAR, None), record_type=RecordType.MESSAGE,
        ),
    }


@pytest.fixture(params=["neo4j", "arango"])
async def seeded(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Graph]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id = f"org-links-{run}"
        records = _records(org_id, f"org-links-other-{run}")
        ids = {name: record.id for name, record in records.items()}

        async def remove() -> None:
            # The type documents too: some tests write tickets and pull requests.
            for collection in (
                CollectionNames.RECORDS.value, CollectionNames.PULLREQUESTS.value, CollectionNames.TICKETS.value,
            ):
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(list(ids.values()), collection)

        cleanup.push_async_callback(remove)
        for record in records.values():
            assert await graph.batch_upsert_nodes([record.to_arango_base_record()], CollectionNames.RECORDS.value)
        await graph.batch_upsert_node_relations([
            {
                "_from": f"{CollectionNames.RECORDS.value}/{ids[holder]}",
                "_to": f"{CollectionNames.RECORDS.value}/{ids[child]}",
                "relationshipType": RecordRelations.PARENT_CHILD.value,
                "constraintName": "",
            }
            for holder, child in (("message", "link"), ("other_holder", "other_link"))
        ])
        yield _Graph(graph, org_id, ids)


async def test_finds_the_records_living_at_the_addresses(seeded: _Graph) -> None:
    rows = await seeded.graph.get_records_by_weburls([PR, JIRA, "https://nowhere.example"], seeded.org_id)

    found = {row["id"]: row for row in rows}
    assert set(found) == {seeded.ids["pr"], seeded.ids["ticket"], seeded.ids["attachment"]}
    assert found[seeded.ids["pr"]]["webUrl"] == PR
    assert found[seeded.ids["pr"]]["recordType"] == RecordType.PULL_REQUEST.value
    assert found[seeded.ids["ticket"]]["isDependentNode"] is False
    assert found[seeded.ids["attachment"]]["isDependentNode"] is True


async def test_a_tight_limit_keeps_the_ticket_over_its_attachment(seeded: _Graph) -> None:
    rows = await seeded.graph.get_records_by_weburls([JIRA], seeded.org_id, per_address=1)

    assert [row["id"] for row in rows] == [seeded.ids["ticket"]]


async def test_one_address_never_crowds_out_another(seeded: _Graph) -> None:
    rows = await seeded.graph.get_records_by_weburls([JIRA, PR], seeded.org_id, per_address=1)

    assert sorted(row["id"] for row in rows) == sorted([seeded.ids["ticket"], seeded.ids["pr"]])


async def test_link_records_are_not_where_an_address_lives(seeded: _Graph) -> None:
    rows = await seeded.graph.get_records_by_weburls([PR + "/files"], seeded.org_id)

    assert rows == []


async def test_finds_the_message_holding_a_link_to_the_address(seeded: _Graph) -> None:
    holders = await seeded.graph.get_records_linking_to_weburls([PR, PR + "/files"], seeded.org_id, [SLACK])

    assert holders == [seeded.ids["message"]]


async def test_holders_are_paged_in_id_order(seeded: _Graph) -> None:
    both = [Connectors.SLACK_WORKSPACE.value, Connectors.LINEAR.value]
    first = await seeded.graph.get_records_linking_to_weburls([PR + "/files"], seeded.org_id, both, limit=1)
    rest = await seeded.graph.get_records_linking_to_weburls(
        [PR + "/files"], seeded.org_id, both, limit=1, after=first[0]
    )

    assert first + rest == sorted([seeded.ids["message"], seeded.ids["other_holder"]])


async def test_only_the_named_connectors_and_org_are_read(seeded: _Graph) -> None:
    assert await seeded.graph.get_records_linking_to_weburls(
        [PR + "/files"], f"{seeded.org_id}-x", [SLACK]
    ) == []
    assert await seeded.graph.get_records_linking_to_weburls(
        [PR + "/files"], seeded.org_id, [Connectors.LINEAR.value]
    ) == [seeded.ids["other_holder"]]


async def test_empty_input_reads_nothing(seeded: _Graph) -> None:
    assert await seeded.graph.get_records_by_weburls([], seeded.org_id) == []
    assert await seeded.graph.get_records_linking_to_weburls([PR], seeded.org_id, []) == []


async def test_issue_sites_are_read_from_the_orgs_jira_tickets(seeded: _Graph) -> None:
    jira = [Connectors.JIRA.value, Connectors.JIRA_DATA_CENTER.value]
    sites = await seeded.graph.get_issue_sites(seeded.org_id, jira)

    # The live ticket's site and a server under a path; not the deleted ticket's
    # duplicate, not GitHub's issues, not another org's site.
    assert sites == ["https://acme.atlassian.net", "https://jira.corp.example/jira"]
    assert await seeded.graph.get_issue_sites(seeded.org_id, []) == []


async def test_address_links_in_both_directions_and_only_by_address(seeded: _Graph) -> None:
    ids = seeded.ids
    rel = CollectionNames.RECORDS.value
    await seeded.graph.batch_upsert_node_relations([
        {"_from": f"{rel}/{ids['message']}", "_to": f"{rel}/{ids['pr']}",
         "relationshipType": RecordRelations.LINKED_TO.value, "matchedBy": "url", "constraintName": ""},
        {"_from": f"{rel}/{ids['pr']}", "_to": f"{rel}/{ids['ticket']}",
         "relationshipType": RecordRelations.LINKED_TO.value, "matchedBy": "key", "constraintName": ""},
        # A link the source system made itself: not a mention.
        {"_from": f"{rel}/{ids['other_holder']}", "_to": f"{rel}/{ids['pr']}",
         "relationshipType": RecordRelations.LINKED_TO.value, "constraintName": ""},
        # A trashed record that pasted the address: not shown.
        {"_from": f"{rel}/{ids['deleted']}", "_to": f"{rel}/{ids['pr']}",
         "relationshipType": RecordRelations.LINKED_TO.value, "matchedBy": "url", "constraintName": ""},
    ])

    links = await seeded.graph.get_address_links_batch([ids["pr"], ids["ticket"]], seeded.org_id)

    assert links[ids["pr"]] == {"mentioned_in": [ids["message"]], "links_to": [ids["ticket"]]}
    assert links[ids["ticket"]] == {"mentioned_in": [ids["pr"]], "links_to": []}
    assert await seeded.graph.get_address_links_batch([ids["pr"]], f"{seeded.org_id}-x") == {
        ids["pr"]: {"mentioned_in": [], "links_to": []},
    }


async def test_address_links_count_records_not_edges(seeded: _Graph) -> None:
    ids = seeded.ids
    rel = CollectionNames.RECORDS.value
    await seeded.graph.batch_upsert_node_relations([
        {"_from": f"{rel}/{ids[source]}", "_to": f"{rel}/{ids['ticket']}",
         "relationshipType": RecordRelations.LINKED_TO.value, "matchedBy": matched, "constraintName": tag}
        for source, matched, tag in (("message", "url", ""), ("message", "key", "dup"), ("pr", "key", ""))
    ])

    links = await seeded.graph.get_address_links_batch([ids["ticket"]], seeded.org_id, limit_per_record=2)

    assert links[ids["ticket"]]["mentioned_in"] == sorted([ids["message"], ids["pr"]])
    unlimited = await seeded.graph.get_address_links_batch([ids["ticket"]], seeded.org_id)
    assert unlimited[ids["ticket"]]["mentioned_in"] == sorted([ids["message"], ids["pr"]])


async def test_linked_records_carry_their_merge_and_resolution_dates(seeded: _Graph) -> None:
    """The dates stored with a pull request and a ticket come back through the
    reads that build a linked record's status: the base records, then one read
    per type collection. Arango's strict schemas accept the new fields."""
    from app.utils.chat_helpers import status_summaries_by_id

    ids = seeded.ids
    merged, resolved = 1790379000000, 1780876800000  # 2026-09-25, 2026-06-08 UTC
    pr = PullRequestRecord(
        **_base(seeded.org_id, "pr", Connectors.GITHUB, PR), record_type=RecordType.PULL_REQUEST,
        status="DONE", merged_at=merged, closed_at=merged, merged_by="alice",
    )
    pr.id = ids["pr"]
    ticket = TicketRecord(
        **_base(seeded.org_id, "ticket", Connectors.JIRA, JIRA), record_type=RecordType.TICKET,
        status="DONE", source_status="Done", resolved_at=resolved, assignee="Bo",
    )
    ticket.id = ids["ticket"]
    assert await seeded.graph.batch_upsert_nodes([pr.to_arango_record()], CollectionNames.PULLREQUESTS.value)
    assert await seeded.graph.batch_upsert_nodes([ticket.to_arango_record()], CollectionNames.TICKETS.value)

    docs = await seeded.graph.get_records_by_record_ids(
        [ids["pr"], ids["ticket"], ids["message"]], seeded.org_id,
    )
    by_id = {doc.get("id") or doc.get("_key"): doc for doc in docs}

    assert await status_summaries_by_id(seeded.graph, by_id) == {
        ids["pr"]: "Merged 2026-09-25 by alice",
        ids["ticket"]: "DONE, resolved 2026-06-08, assignee Bo",
    }
    stored = await seeded.graph.get_nodes_by_field_in(CollectionNames.PULLREQUESTS.value, "id", [ids["pr"]])
    assert (stored[0]["mergedAtTimestamp"], stored[0]["closedAtTimestamp"]) == (merged, merged)


async def test_a_reopened_ticket_and_pr_lose_their_dates(seeded: _Graph) -> None:
    """A date the source cleared (an issue reopened, a PR reopened) must not
    survive the next write: the status would read "resolved" on an open ticket."""
    from app.utils.chat_helpers import status_summaries_by_id

    ids = seeded.ids
    day = 1780876800000  # 2026-06-08 UTC

    def ticket(**fields: object) -> TicketRecord:
        record = TicketRecord(**_base(seeded.org_id, "ticket", Connectors.JIRA, JIRA), record_type=RecordType.TICKET, **fields)
        record.id = ids["ticket"]
        return record

    def pr(**fields: object) -> PullRequestRecord:
        record = PullRequestRecord(**_base(seeded.org_id, "pr", Connectors.GITHUB, PR), record_type=RecordType.PULL_REQUEST, **fields)
        record.id = ids["pr"]
        return record

    async def summaries() -> dict[str, str]:
        docs = await seeded.graph.get_records_by_record_ids([ids["pr"], ids["ticket"]], seeded.org_id)
        return await status_summaries_by_id(seeded.graph, {doc.get("id") or doc.get("_key"): doc for doc in docs})

    await seeded.graph.batch_upsert_records([
        ticket(status="DONE", resolved_at=day, due_date=day), pr(status="CANCELLED", closed_at=day),
    ])
    assert await summaries() == {
        ids["pr"]: "Closed without merging 2026-06-08",
        ids["ticket"]: "DONE, resolved 2026-06-08",
    }
    await seeded.graph.batch_upsert_records([ticket(status="IN_PROGRESS"), pr(status="OPEN")])

    stored = await seeded.graph.get_nodes_by_field_in(CollectionNames.TICKETS.value, "id", [ids["ticket"]])
    assert stored[0].get("resolvedAtTimestamp") is None
    assert stored[0].get("dueDateTimestamp") is None
    assert await summaries() == {
        ids["pr"]: "Open",
        ids["ticket"]: "IN_PROGRESS",
    }

