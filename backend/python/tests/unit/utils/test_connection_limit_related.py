"""A connected app with a limit learns nothing about records outside it from
what it reads: no related record, link, foreign-key table or source parent it
may not open, and no source path that names the folders above a file.

The limit check itself (``outside_connection``) is stubbed: these tests are
about what is asked and what is left out, and that nothing is asked without a
limit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.entities import TicketRecord
from app.utils.chat_helpers import (
    describe_search_records,
    enrich_records_with_address_links,
    enrich_virtual_record_id_to_result_with_fk_children,
    keep_graph_docs_inside_connection,
    keep_record_text_inside_connection,
    keep_row_summaries_inside_connection,
)
from app.utils.fetch_full_record import (
    _apply_live_ticket_context_metadata,
    _enrich_sql_table_with_fk_relations,
)
from tests.support.edition_defaults import FK_CHILD_FIELD

OUTSIDE = "app.modules.retrieval.selection_scope.outside_connection"
FILE_LIMIT = {"apps": [], "recordGroups": [], "records": ["hit"]}
WHOLE_APP = {"apps": ["app-1"], "recordGroups": [], "records": []}


def _outside(*ids: str) -> AsyncMock:
    """The limit check, leaving out ``ids``."""
    return AsyncMock(side_effect=lambda _g, _u, _o, asked, _s: set(asked) & set(ids))


def _text(*extra: str) -> str:
    return "\n".join([
        "Record ID: hit", "Name: budget.xlsx", "Connector: DRIVE", "Type: FILE",
        "External Parent ID: ext-folder", *extra,
    ])


class TestRecordText:
    @pytest.mark.asyncio
    async def test_without_a_limit_nothing_is_asked_or_changed(self) -> None:
        graph = AsyncMock()
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": _text(), "file_path": "src/a.py"}

        await keep_record_text_inside_connection([rec], graph, "u", "o", None)

        assert rec["context_metadata"] == _text() and rec["file_path"] == "src/a.py"
        graph.get_records_by_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_source_parent_outside_the_limit_is_left_out(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "hit", "parentNodeId": "folder"}])
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": _text(), "parent_external_record_id": "x"}

        with patch(OUTSIDE, _outside("folder")):
            await keep_record_text_inside_connection([rec], graph, "u", "o", FILE_LIMIT)

        assert "External Parent ID: N/A" in rec["context_metadata"] and "ext-folder" not in rec["context_metadata"]
        assert rec["parent_external_record_id"] is None

    @pytest.mark.asyncio
    async def test_a_source_parent_inside_the_limit_stays(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "hit", "parentNodeId": "folder"}])
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": _text()}

        with patch(OUTSIDE, _outside()):
            await keep_record_text_inside_connection([rec], graph, "u", "o", FILE_LIMIT)

        assert "External Parent ID: ext-folder" in rec["context_metadata"]

    @pytest.mark.asyncio
    async def test_a_source_parent_with_no_node_to_judge_it_by_is_left_out(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "hit"}])
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": _text()}

        with patch(OUTSIDE, _outside()):
            await keep_record_text_inside_connection([rec], graph, "u", "o", FILE_LIMIT)

        assert "ext-folder" not in rec["context_metadata"]

    @pytest.mark.asyncio
    async def test_linked_records_deal_lines_and_the_source_path_outside_the_limit_go(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "hit", "parentNodeId": "folder"}])
        text = _text(
            "Path: src/billing/secret/a.py",
            "* Linked Record ID: linked-out", "* Linked Record ID: linked-in",
            "* [1] from records/org-out; stage: won", "* [2] from records/product-in; product: Widget",
        )
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": text, "file_path": "src/billing/secret/a.py"}

        with patch(OUTSIDE, _outside("linked-out", "org-out")):
            await keep_record_text_inside_connection([rec], graph, "u", "o", FILE_LIMIT)

        shown = rec["context_metadata"]
        assert "linked-out" not in shown and "org-out" not in shown and "src/billing" not in shown
        assert "* Linked Record ID: linked-in" in shown and "product: Widget" in shown
        assert rec["file_path"] is None

    @pytest.mark.asyncio
    async def test_a_record_whose_whole_app_is_shared_is_untouched(self) -> None:
        graph = AsyncMock()
        text = _text("Path: src/a.py", "* Linked Record ID: other")
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": text, "file_path": "src/a.py"}

        check = _outside("other")
        with patch(OUTSIDE, check):
            await keep_record_text_inside_connection([rec], graph, "u", "o", WHOLE_APP)

        assert rec["context_metadata"] == text and rec["file_path"] == "src/a.py"
        check.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_when_the_check_fails_every_name_goes(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_record_ids = AsyncMock(side_effect=RuntimeError("graph down"))
        rec = {"id": "hit", "connector_id": "app-1", "context_metadata": _text("* Linked Record ID: x")}

        await keep_record_text_inside_connection([rec], graph, "u", "o", FILE_LIMIT)

        assert "ext-folder" not in rec["context_metadata"] and "Linked Record ID" not in rec["context_metadata"]


class TestGraphDocs:
    @pytest.mark.asyncio
    async def test_a_grep_hit_document_loses_its_source_parent_and_path(self) -> None:
        doc = {"id": "hit", "connectorId": "app-1", "parentNodeId": "folder", "externalParentId": "ext",
               "filePath": "src/secret/a.py", "webUrl": "https://src/a"}

        with patch(OUTSIDE, _outside("folder")):
            await keep_graph_docs_inside_connection([doc], AsyncMock(), "u", "o", FILE_LIMIT)

        assert doc["externalParentId"] is None and doc["filePath"] is None and doc["webUrl"] == "https://src/a"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, WHOLE_APP])
    async def test_without_a_limit_or_under_a_whole_app_one_the_document_is_kept(self, limit) -> None:
        doc = {"id": "hit", "connectorId": "app-1", "parentNodeId": "folder", "externalParentId": "ext",
               "filePath": "src/a.py"}

        check = _outside("folder")
        with patch(OUTSIDE, check):
            await keep_graph_docs_inside_connection([doc], AsyncMock(), "u", "o", limit)

        assert doc["externalParentId"] == "ext" and doc["filePath"] == "src/a.py"
        check.assert_not_awaited()


def _links_graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_address_links_batch = AsyncMock(return_value={
        "hit": {"mentioned_in": ["slack-in", "slack-out"], "links_to": ["jira-out"]},
    })
    graph.filter_accessible_record_ids = AsyncMock(side_effect=lambda ids, _u, _o: set(ids))
    graph.get_records_by_record_ids = AsyncMock(side_effect=lambda ids, _o: [
        {"id": i, "recordName": f"name of {i}", "connectorName": "X"} for i in ids
    ])
    graph.get_nodes_by_field_in = AsyncMock(return_value=[])
    return graph


class TestLinks:
    @pytest.mark.asyncio
    async def test_a_link_outside_the_limit_is_not_named(self) -> None:
        hits = {"v": {"id": "hit"}}

        with patch(OUTSIDE, _outside("slack-out", "jira-out")):
            await enrich_records_with_address_links(
                hits, _links_graph(), "o", user_id="u", connection_sources=FILE_LIMIT,
            )

        links = hits["v"]["address_links"]
        assert [row["record_id"] for row in links["mentioned_in"]] == ["slack-in"]
        assert not links.get("links_to")

    @pytest.mark.asyncio
    async def test_without_a_limit_every_readable_link_is_named_and_no_limit_is_checked(self) -> None:
        hits = {"v": {"id": "hit"}}

        check = _outside("slack-out")
        with patch(OUTSIDE, check):
            await enrich_records_with_address_links(hits, _links_graph(), "o", user_id="u")

        assert [row["record_id"] for row in hits["v"]["address_links"]["mentioned_in"]] == ["slack-in", "slack-out"]
        check.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_search_apis_linked_records_keep_to_the_limit(self) -> None:
        records = [{"_key": "hit", "recordType": "FILE"}]

        with patch(OUTSIDE, _outside("slack-out", "jira-out")):
            await describe_search_records(records, _links_graph(), "o", user_id="u", connection_sources=FILE_LIMIT)

        assert [row["recordId"] for row in records[0]["linkedRecords"]] == ["slack-in"]
        assert "name of slack-out" not in str(records)


def _fk_graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_child_record_ids_by_relation_type = AsyncMock(
        side_effect=lambda rid, _rel: [{"record_id": "t-in"}, {"record_id": "t-out"}] if rid == "t-hit" else [],
    )
    graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
    graph.get_virtual_record_ids_for_record_ids = AsyncMock(return_value={})
    return graph


class TestForeignKeyTables:
    @staticmethod
    def _readable() -> tuple[Any, Any]:
        return (
            patch("app.utils.chat_helpers.accessible_node_ids_for_enrichment",
                  AsyncMock(side_effect=lambda _g, ids, _u, _o: set(ids))),
            patch("app.utils.chat_helpers.live_record_ids", AsyncMock(side_effect=lambda _g, ids, _o: set(ids))),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("limit", "shown"), [(FILE_LIMIT, ["t-in"]), (None, ["t-in", "t-out"])])
    async def test_a_related_table_outside_the_limit_is_not_named_in_chat(self, limit, shown) -> None:
        records = {"v": {"record_type": "SQL_TABLE", "id": "t-hit"}}
        flattened = [{"virtual_record_id": "v"}]
        readable, live = self._readable()

        with readable, live, patch(OUTSIDE, _outside("t-out")):
            await enrich_virtual_record_id_to_result_with_fk_children(
                records, MagicMock(), "o", _fk_graph(), flattened, user_id="u", connection_sources=limit,
            )

        assert [rel["record_id"] for rel in flattened[0]["fk_child_relations"]] == shown

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("limit", "shown"), [(FILE_LIMIT, ["t-in"]), (None, ["t-in", "t-out"])])
    async def test_a_related_table_outside_the_limit_is_not_named_when_read_by_id(self, limit, shown) -> None:
        with patch("app.utils.fetch_full_record.accessible_node_ids",
                   AsyncMock(side_effect=lambda _g, ids, _u, _o: set(ids))), \
             patch("app.utils.fetch_full_record.live_record_ids",
                   AsyncMock(side_effect=lambda _g, ids, _o: set(ids))), \
             patch(OUTSIDE, _outside("t-out")):
            enriched = await _enrich_sql_table_with_fk_relations(
                {"id": "t-hit"}, _fk_graph(), "u", "o", connection_sources=limit,
            )

        assert [rel["record_id"] for rel in enriched[FK_CHILD_FIELD]] == shown


class TestForeignKeyTablesWhenTheCheckFails:
    @pytest.mark.asyncio
    async def test_a_failed_limit_check_names_no_related_table_and_the_turn_goes_on(self) -> None:
        records = {"v": {"record_type": "SQL_TABLE", "id": "t-hit"}}
        flattened = [{"virtual_record_id": "v"}]
        readable, live = TestForeignKeyTables._readable()

        with readable, live, patch(OUTSIDE, AsyncMock(side_effect=RuntimeError("graph down"))):
            await enrich_virtual_record_id_to_result_with_fk_children(
                records, MagicMock(), "o", _fk_graph(), flattened, user_id="u", connection_sources=FILE_LIMIT,
            )

        assert flattened[0]["fk_child_relations"] == []


class TestLiveJiraFields:
    @staticmethod
    async def _upgrade(limit: dict | None, app_id: str = "jira-app") -> str:
        base = "Record ID: t\nTicket Information:\n* Status: Open"
        live = "\n".join([
            base, "* Sprint: 12", "* Parent: PROJ-1 — Secret plans", "* Linked Issues: blocks PROJ-9",
            "* Sub-tasks: PROJ-4", "* Epic Link: PROJ-2", "* Project: Moonshot",
        ])
        ticket = MagicMock(spec=TicketRecord)
        ticket.to_llm_context_with_live_fields = AsyncMock(return_value=live)
        record = {"id": "t", "record_type": "TICKET", "connector_id": app_id, "context_metadata": base}
        with patch("app.utils.fetch_full_record.create_record_instance_from_dict", return_value=ticket):
            await _apply_live_ticket_context_metadata(
                record, config_service=MagicMock(), graph_provider=None, frontend_url=None,
                connection_sources=limit,
            )
        return record["context_metadata"]

    @pytest.mark.asyncio
    async def test_a_naming_line_the_record_already_held_goes_too(self) -> None:
        base = "Record ID: t\nTicket Information:\n* Status: Open\n* Parent: PROJ-1 — Secret plans"
        ticket = MagicMock(spec=TicketRecord)
        ticket.to_llm_context_with_live_fields = AsyncMock(return_value=base)
        record = {"id": "t", "record_type": "TICKET", "connector_id": "jira-app", "context_metadata": base}
        with patch("app.utils.fetch_full_record.create_record_instance_from_dict", return_value=ticket):
            await _apply_live_ticket_context_metadata(
                record, config_service=MagicMock(), graph_provider=None, frontend_url=None,
                connection_sources=FILE_LIMIT,
            )

        assert "Secret plans" not in record["context_metadata"]
        assert "* Status: Open" in record["context_metadata"]

    @pytest.mark.asyncio
    async def test_under_a_narrow_limit_lines_naming_other_issues_go(self) -> None:
        text = await self._upgrade(FILE_LIMIT)

        assert "* Sprint: 12" in text and "* Status: Open" in text
        for gone in ("Secret plans", "PROJ-9", "PROJ-4", "PROJ-2", "Moonshot"):
            assert gone not in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, {"apps": ["jira-app"], "recordGroups": [], "records": []}])
    async def test_without_a_limit_or_under_the_whole_app_every_live_line_stays(self, limit) -> None:
        text = await self._upgrade(limit)

        assert "Secret plans" in text and "Moonshot" in text


class TestRowSummaries:
    @pytest.mark.asyncio
    async def test_a_row_names_no_linked_record_or_deal_party_outside_the_limit(self) -> None:
        row = MagicMock(id="hit", context_summary="Stage: won, [1] from records/org-out; stage: won, "
                                                  "Linked Record ID: linked-in, Amount: 1,250")
        graph = AsyncMock()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "hit", "connectorId": "app-1"}])

        with patch(OUTSIDE, _outside("org-out")):
            await keep_row_summaries_inside_connection([row], graph, "u", "o", FILE_LIMIT)

        assert row.context_summary == "Stage: won, Linked Record ID: linked-in, Amount: 1,250"

    @pytest.mark.asyncio
    async def test_without_a_limit_a_row_is_untouched(self) -> None:
        row = MagicMock(id="hit", context_summary="Linked Record ID: anywhere")

        await keep_row_summaries_inside_connection([row], AsyncMock(), "u", "o", None)

        assert row.context_summary == "Linked Record ID: anywhere"
