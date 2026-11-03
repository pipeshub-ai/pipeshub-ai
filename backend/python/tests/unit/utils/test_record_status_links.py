"""Linked records carry their type and status; reads and the search API carry both.

A Slack thread that pasted a pull request's link was joined to it, and the
model saw the pull request's name under "Links to". Whether it was merged, and
when, took another search. Each linked record now carries its type and status,
records read by id carry the same links, and the search API's record documents
carry their own status and their links for API callers such as the MCP server.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.chat_helpers import (
    build_address_links_info,
    describe_search_records,
    enrich_read_records_with_address_links,
    enrich_records_with_address_links,
    record_header_metadata,
    record_to_text,
    status_summary_from_graph,
)

MERGED = 1790379000000  # 2026-09-25 23:30 UTC
RESOLVED = 1780876800000  # 2026-06-08 00:00 UTC

BASE_DOCS = {
    "pr-1": {
        "_key": "pr-1", "recordName": "Fix sign-in for OneDrive-only agents",
        "recordType": "PULL_REQUEST", "connectorName": "GITHUB",
    },
    "jira-1": {
        "_key": "jira-1", "recordName": "[PROJ-12] Report failed uploads",
        "recordType": "TICKET", "connectorName": "JIRA",
    },
    "slack-1": {
        "_key": "slack-1", "recordName": "eng_2026-09-26",
        "recordType": "MESSAGE", "connectorName": "SLACK WORKSPACE",
    },
}
TYPE_DOCS = {
    "prs": {"pr-1": {"_key": "pr-1", "status": "DONE", "mergedAtTimestamp": MERGED, "mergedBy": "alice"}},
    "tickets": {"jira-1": {
        "_key": "jira-1", "status": "DONE", "sourceStatus": "Done",
        "resolvedAtTimestamp": RESOLVED, "assignee": "Dana",
    }},
}


def _graph(links: dict, readable: set[str]) -> AsyncMock:
    graph = AsyncMock()
    graph.get_address_links_batch = AsyncMock(return_value=links)
    graph.filter_accessible_record_ids = AsyncMock(return_value=readable)
    graph.get_records_by_record_ids = AsyncMock(
        side_effect=lambda ids, org_id: [BASE_DOCS[i] for i in ids if i in BASE_DOCS]
    )

    async def nodes(collection: str, field: str, ids: list[str]) -> list[dict[str, Any]]:
        return [doc for key, doc in TYPE_DOCS.get(collection, {}).items() if key in ids]

    graph.get_nodes_by_field_in = AsyncMock(side_effect=nodes)
    return graph


class TestStatusFromGraph:
    def test_pull_request(self) -> None:
        summary = status_summary_from_graph(BASE_DOCS["pr-1"], TYPE_DOCS["prs"]["pr-1"])
        assert summary == "Merged 2026-09-25 by alice"

    def test_ticket(self) -> None:
        summary = status_summary_from_graph(BASE_DOCS["jira-1"], TYPE_DOCS["tickets"]["jira-1"])
        assert summary == "DONE, resolved 2026-06-08, assignee Dana"

    def test_a_record_type_without_state(self) -> None:
        assert status_summary_from_graph(BASE_DOCS["slack-1"], {"status": "DONE"}) is None

    def test_no_type_document(self) -> None:
        assert status_summary_from_graph(BASE_DOCS["pr-1"], None) is None

    def test_one_line_and_bounded(self) -> None:
        summary = status_summary_from_graph(
            BASE_DOCS["jira-1"], {"status": "DONE", "assignee": "A\nB " + "x" * 400},
        )
        assert "\n" not in summary
        assert len(summary) <= 160


class TestLinkedRecordsCarryTheirStatus:
    @pytest.mark.asyncio
    async def test_a_thread_shows_the_pr_it_links_with_merge_date(self) -> None:
        hits = {"vrid-slack": {"id": "slack-1"}}
        graph = _graph({"slack-1": {"mentioned_in": [], "links_to": ["pr-1", "jira-1"]}}, {"pr-1", "jira-1"})

        await enrich_records_with_address_links(hits, graph, "org-1", user_id="user-1")

        rows = hits["vrid-slack"]["address_links"]["links_to"]
        assert rows[0] == {
            "record_id": "pr-1", "record_name": "Fix sign-in for OneDrive-only agents",
            "connector_name": "GITHUB", "record_type": "PULL_REQUEST",
            "status": "Merged 2026-09-25 by alice",
        }
        assert rows[1]["status"] == "DONE, resolved 2026-06-08, assignee Dana"
        text = build_address_links_info(hits["vrid-slack"])
        assert (
            '<linked_record record_id="pr-1" source="GITHUB" type="PULL_REQUEST" '
            'status="Merged 2026-09-25 by alice">'
        ) in text

    @pytest.mark.asyncio
    async def test_type_documents_are_read_once_per_collection(self) -> None:
        hits = {"a": {"id": "slack-1"}}
        graph = _graph({"slack-1": {"mentioned_in": [], "links_to": ["pr-1", "jira-1"]}}, {"pr-1", "jira-1"})

        await enrich_records_with_address_links(hits, graph, "org-1", user_id="user-1")

        collections = sorted(call.args[0] for call in graph.get_nodes_by_field_in.await_args_list)
        assert collections == ["prs", "tickets"]

    @pytest.mark.asyncio
    async def test_a_message_is_never_read_for_a_status(self) -> None:
        hits = {"a": {"id": "pr-1"}}
        graph = _graph({"pr-1": {"mentioned_in": ["slack-1"], "links_to": []}}, {"slack-1"})

        await enrich_records_with_address_links(hits, graph, "org-1", user_id="user-1")

        graph.get_nodes_by_field_in.assert_not_awaited()
        row = hits["a"]["address_links"]["mentioned_in"][0]
        assert row["record_type"] == "MESSAGE"
        assert "status" not in row

    @pytest.mark.asyncio
    async def test_a_failed_status_read_keeps_the_links(self) -> None:
        hits = {"a": {"id": "slack-1"}}
        graph = _graph({"slack-1": {"mentioned_in": [], "links_to": ["pr-1"]}}, {"pr-1"})
        graph.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("graph down"))

        await enrich_records_with_address_links(hits, graph, "org-1", user_id="user-1")

        row = hits["a"]["address_links"]["links_to"][0]
        assert row["record_id"] == "pr-1"
        assert "status" not in row

    @pytest.mark.asyncio
    async def test_no_status_is_read_for_a_record_the_user_cannot_open(self) -> None:
        hits = {"a": {"id": "slack-1"}}
        graph = _graph({"slack-1": {"mentioned_in": [], "links_to": ["pr-1"]}}, set())

        await enrich_records_with_address_links(hits, graph, "org-1", user_id="user-1")

        assert "address_links" not in hits["a"]
        graph.get_nodes_by_field_in.assert_not_awaited()

    def test_a_status_cannot_break_out_of_its_attribute(self) -> None:
        record = {"address_links": {"links_to": [{
            "record_id": "pr-1", "record_name": "x", "connector_name": "GITHUB",
            "record_type": "PULL_REQUEST", "status": 'Merged" onload="y <b>',
        }]}}
        text = build_address_links_info(record)
        assert 'status="Merged&quot; onload=&quot;y &lt;b&gt;"' in text

    def test_a_row_from_before_keeps_its_old_form(self) -> None:
        record = {"address_links": {"links_to": [
            {"record_id": "jira-1", "record_name": "PROJ-1", "connector_name": "JIRA"},
        ]}}
        assert '<linked_record record_id="jira-1" source="JIRA"><record_name>' in build_address_links_info(record)


class TestReadRecordsCarryTheirLinks:
    def test_header_metadata_appends_the_links(self) -> None:
        record = {
            "context_metadata": "Record ID: slack-1\nName: eng",
            "address_links": {"links_to": [{
                "record_id": "pr-1", "record_name": "Fix", "connector_name": "GITHUB",
                "record_type": "PULL_REQUEST", "status": "Merged 2026-09-25",
            }]},
        }
        header = record_header_metadata(record)
        assert header.startswith("Record ID: slack-1\nName: eng\n* Links to:\n  <linked_record")
        assert not header.endswith("\n")

    def test_header_metadata_without_links_is_the_metadata(self) -> None:
        assert record_header_metadata({"context_metadata": "Record ID: r"}) == "Record ID: r"
        assert record_header_metadata({}) == ""

    def test_record_to_text_shows_the_links(self) -> None:
        record = {
            "context_metadata": "Record ID: slack-1",
            "address_links": {"links_to": [{
                "record_id": "pr-1", "record_name": "Fix", "connector_name": "GITHUB",
                "record_type": "PULL_REQUEST", "status": "Merged 2026-09-25",
            }]},
            "block_containers": {"blocks": [], "block_groups": []},
        }
        assert 'status="Merged 2026-09-25"' in record_to_text(record)

    @pytest.mark.asyncio
    async def test_enrich_read_records(self) -> None:
        records = [{"id": "slack-1", "context_metadata": "Record ID: slack-1"}]
        graph = _graph({"slack-1": {"mentioned_in": [], "links_to": ["pr-1"]}}, {"pr-1"})

        await enrich_read_records_with_address_links(records, graph, "org-1", user_id="user-1")

        assert records[0]["address_links"]["links_to"][0]["status"] == "Merged 2026-09-25 by alice"

    @pytest.mark.asyncio
    async def test_enrich_read_records_needs_a_user(self) -> None:
        records = [{"id": "slack-1"}]
        graph = _graph({}, set())

        await enrich_read_records_with_address_links(records, graph, "org-1", user_id=None)

        graph.get_address_links_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_full_record_attaches_links(self) -> None:
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {"vr1": {"id": "slack-1", "record_name": "eng", "context_metadata": "Record ID: slack-1"}}
        graph = _graph({"slack-1": {"mentioned_in": [], "links_to": ["pr-1"]}}, {"pr-1"})
        graph.config_service = MagicMock()
        # A read checks the record itself first, in one batch.
        graph.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
        graph.check_access = AsyncMock(
            side_effect=lambda _user_key, _org_id, node_ids=(), **_: AccessCheck(
                node_ids=frozenset(node_ids) & {"slack-1"},
            ),
        )

        with patch("app.utils.fetch_full_record._apply_live_ticket_context_metadata", new=AsyncMock()):
            result = await _fetch_multiple_records_impl(
                ["slack-1"], records_map, graph_provider=graph, org_id="org-1", user_id="user-1",
            )

        assert result["ok"] is True
        row = result["records"][0]["address_links"]["links_to"][0]
        assert row["status"] == "Merged 2026-09-25 by alice"


class TestSearchRecords:
    @pytest.mark.asyncio
    async def test_each_record_gets_its_status_and_links(self) -> None:
        records = [dict(BASE_DOCS["pr-1"]), dict(BASE_DOCS["slack-1"])]
        graph = _graph(
            {
                "pr-1": {"mentioned_in": ["slack-1"], "links_to": ["jira-1"]},
                "slack-1": {"mentioned_in": [], "links_to": ["pr-1"]},
            },
            {"pr-1", "jira-1", "slack-1"},
        )

        await describe_search_records(records, graph, "org-1", user_id="user-1")

        pr, slack = records
        assert pr["statusSummary"] == "Merged 2026-09-25 by alice"
        assert pr["linkedRecords"] == [
            {
                "relation": "mentionedIn", "recordId": "slack-1", "recordName": "eng_2026-09-26",
                "recordType": "MESSAGE", "connector": "SLACK WORKSPACE",
            },
            {
                "relation": "linksTo", "recordId": "jira-1", "recordName": "[PROJ-12] Report failed uploads",
                "recordType": "TICKET", "connector": "JIRA",
                "status": "DONE, resolved 2026-06-08, assignee Dana",
            },
        ]
        assert "statusSummary" not in slack
        assert slack["linkedRecords"][0]["status"] == "Merged 2026-09-25 by alice"

    @pytest.mark.asyncio
    async def test_nothing_to_say_adds_no_keys(self) -> None:
        records = [dict(BASE_DOCS["slack-1"])]
        graph = _graph({}, set())

        await describe_search_records(records, graph, "org-1", user_id="user-1")

        assert "statusSummary" not in records[0]
        assert "linkedRecords" not in records[0]

    @pytest.mark.asyncio
    async def test_keys_from_an_earlier_request_are_cleared(self) -> None:
        records = [{**BASE_DOCS["slack-1"], "linkedRecords": [{"recordId": "stale"}], "statusSummary": "x"}]
        graph = _graph({}, set())

        await describe_search_records(records, graph, "org-1", user_id="user-1")

        assert "linkedRecords" not in records[0]
        assert "statusSummary" not in records[0]

    @pytest.mark.asyncio
    async def test_links_fail_closed_when_access_cannot_be_checked(self) -> None:
        records = [dict(BASE_DOCS["pr-1"])]
        graph = _graph({"pr-1": {"mentioned_in": ["slack-1"], "links_to": []}}, {"slack-1"})
        graph.filter_accessible_record_ids = AsyncMock(side_effect=RuntimeError("down"))

        await describe_search_records(records, graph, "org-1", user_id="user-1")

        assert "linkedRecords" not in records[0]
        assert records[0]["statusSummary"] == "Merged 2026-09-25 by alice"
