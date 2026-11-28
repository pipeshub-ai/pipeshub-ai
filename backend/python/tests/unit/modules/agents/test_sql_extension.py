"""The default SQL hooks (`SqlExtension`) keep SQL tables and query results as they were before the hooks
existed: a query's full rows become a CSV file of the conversation, saved in the background, and a fetched
SQL table is given the tables its foreign keys link it to."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.agents.sql_extension import (
    SqlExtension,
    default_sql_extension,
    get_sql_extension,
)
from app.utils.fetch_full_record import _RecordResolver

EXT = SqlExtension()


def test_the_default_is_a_plain_sql_extension() -> None:
    assert type(default_sql_extension()) is SqlExtension


def test_the_hooks_in_force_are_the_editions(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = SqlExtension()
    monkeypatch.setattr("app.edition_services.sql_extension", lambda: installed)
    assert get_sql_extension() is installed


class TestQueryResults:
    @staticmethod
    async def _keep(result: dict, **overrides: object) -> MagicMock:
        kwargs: dict = {
            "columns": ["x"], "rows": [(1,)], "source_name": "PostgreSQL", "blob_store": MagicMock(),
            "graph_provider": MagicMock(), "config_service": MagicMock(), "org_id": "org-1", "user_id": "u-alice",
            "artifact_owner_user_id": None, "conversation_id": "conv-1", "run_id": "run-9",
            "logger": logging.getLogger("test"),
        }
        with patch("app.utils.execute_query.register_task") as register, patch(
            "app.sandbox.artifact_upload.save_query_result_csv", new_callable=AsyncMock,
        ) as save:
            await EXT.keep_query_result(result, **(kwargs | overrides))
            if register.called:
                await register.call_args.args[1]
        return save

    async def test_the_rows_are_saved_as_a_csv_of_the_conversation_in_the_background(self) -> None:
        result = {"ok": True, "rows_cut_at": None}
        save = await self._keep(result)

        call = save.await_args.kwargs
        assert (call["conversation_id"], call["org_id"], call["user_id"]) == ("conv-1", "org-1", "u-alice")
        assert (call["columns"], call["rows"], call["run_id"]) == (["x"], [(1,)], "run-9")
        assert call["source_tool"] == "sql.execute_sql_query"
        assert call["file_name"].startswith("query_result_") and call["file_name"].endswith(".csv")
        assert result == {"ok": True, "rows_cut_at": None}

    async def test_the_csv_belongs_to_the_artifact_owner(self) -> None:
        save = await self._keep({"ok": True}, artifact_owner_user_id="u-bob")
        assert save.await_args.kwargs["user_id"] == "u-bob"

    @pytest.mark.parametrize(
        ("result", "overrides"),
        [({"ok": False}, {}), ({"ok": True}, {"rows": []}), ({"ok": True}, {"conversation_id": None})],
        ids=["the query failed", "no rows", "no conversation"],
    )
    async def test_nothing_is_saved_without_its_prerequisites(self, result: dict, overrides: dict) -> None:
        save = await self._keep(result, **overrides)
        save.assert_not_awaited()


class TestSqlTables:
    async def test_no_table_is_given_concept_context(self) -> None:
        graph = MagicMock()
        assert await EXT.concept_context("t1", graph, org_id="org-1", user_id="u1") == ""
        assert graph.mock_calls == []

    async def test_a_table_is_given_its_foreign_key_tables_and_nothing_else(self) -> None:
        enriched = {"id": "t1", "fk_parent_relations": [{"record_id": "t2"}]}
        blob_store = AsyncMock()
        with patch(
            "app.utils.fetch_full_record._enrich_sql_table_with_fk_relations", AsyncMock(return_value=enriched),
        ) as fk:
            out = await EXT.enrich_sql_table(
                {"id": "t1"}, MagicMock(), user_id="u1", org_id="org-1", connection_sources=None,
                blob_store=blob_store,
            )

        assert out is enriched
        assert fk.await_args.args[2:] == ("u1", "org-1")
        blob_store.assert_not_awaited()

    async def test_the_fetch_tool_asks_the_editions_hook_for_a_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        asked: list[dict] = []

        class _Recording(SqlExtension):
            async def enrich_sql_table(self, record: dict, graph_provider: object, **kwargs: object) -> dict:
                asked.append({"record": record, "graph_provider": graph_provider, **kwargs})
                return {**record, "told": True}

        monkeypatch.setattr("app.edition_services.sql_extension", lambda: _Recording())
        graph = MagicMock()
        sources = {"conn-1": ["t1"]}
        resolver = _RecordResolver(
            virtual_record_id_to_result={}, graph_provider=graph, blob_store=MagicMock(), config_service=None,
            org_id="org-1", user_id="u1", frontend_url=None, gate=None, connection_sources=sources,
        )
        with patch("app.utils.fetch_full_record._apply_live_ticket_context_metadata", AsyncMock()):
            out = await resolver._enrich({"id": "t1", "record_type": "SQL_TABLE"})
            plain = await resolver._enrich({"id": "f1", "record_type": "FILE"})

        assert out["told"] is True
        (call,) = asked
        assert (call["graph_provider"], call["user_id"], call["org_id"]) == (graph, "u1", "org-1")
        assert call["connection_sources"] is sources
        assert call["blob_store"] == resolver._ensure_blob_store
        assert plain == {"id": "f1", "record_type": "FILE"}
