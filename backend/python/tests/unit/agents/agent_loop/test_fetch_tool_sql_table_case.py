"""SQLDESC-1: the fetch tool tells only a request that can query a database about tables.

Both editions share this description, and the golden journals hash every description a run gets: a sentence about
SQL queries in every request changed all of them, and told a model with no database to answer with a SQL query.
The chat bridges set ``allowed_sql_connector_ids`` before the first turn, so the description stays fixed for the
request.
"""

from __future__ import annotations

from typing import Any

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.citations import (
    _ILLUSTRATIONS_END,  # pyright: ignore[reportPrivateUsage]
    _SQL_TABLE_CASE,  # pyright: ignore[reportPrivateUsage]
    CitationCollector,
    _FetchFullRecordTool,  # pyright: ignore[reportPrivateUsage]
)


def _description(**state: Any) -> str:  # noqa: ANN401
    context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com")
    context.tool_state.update(state)
    return _FetchFullRecordTool(CitationCollector(context), context).description


def test_a_request_without_sql_connectors_hears_nothing_about_tables() -> None:
    for state in ({}, {"allowed_sql_connector_ids": []}, {"allowed_sql_connector_ids": None}):
        text = _description(**state)
        assert "database table" not in text and "SQL" not in text


def test_a_request_with_sql_connectors_hears_the_table_case_once_after_the_illustrations() -> None:
    text = _description(allowed_sql_connector_ids=["pg-1"])
    assert text.count(_SQL_TABLE_CASE) == 1
    assert _ILLUSTRATIONS_END + _SQL_TABLE_CASE in text


def test_the_anchor_is_in_the_description_so_the_case_can_never_silently_drop() -> None:
    assert _description().count(_ILLUSTRATIONS_END) == 1
