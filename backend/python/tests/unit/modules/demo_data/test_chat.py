"""Switched-off demo data stays out of a chat or agent run."""

from __future__ import annotations

from app.modules.demo_data.chat import (
    exclude_from_query,
    exclude_from_state,
    excluded_app_ids,
)
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY

OFF = frozenset({"demo-1"})


def test_nothing_changes_when_the_demo_is_on() -> None:
    query = {"knowledge": [{"connectorId": "demo-1"}], "filters": {"apps": ["demo-1"]}}
    assert exclude_from_query(query, frozenset()) is query


def test_the_demo_leaves_the_knowledge_and_source_filters() -> None:
    query = {
        "knowledge": [{"connectorId": "demo-1", "type": "Demo"}, {"connectorId": "jira-1", "type": "JIRA"}],
        "filters": {"apps": ["demo-1", "jira-1"], "kb": ["kb-1"]},
    }
    out = exclude_from_query(query, OFF)
    assert [k["connectorId"] for k in out["knowledge"]] == ["jira-1"]
    assert out["filters"]["apps"] == ["jira-1"]
    assert STRICT_SCOPE_FILTER_KEY not in out["filters"]
    assert query["filters"]["apps"] == ["demo-1", "jira-1"], "the caller's query is not changed"


def test_an_agent_built_on_the_demo_alone_finds_nothing_rather_than_everything() -> None:
    out = exclude_from_query({"knowledge": [{"connectorId": "demo-1"}], "filters": {"apps": ["demo-1"]}}, OFF)
    assert out["knowledge"] == []
    assert out["filters"][STRICT_SCOPE_FILTER_KEY] is True


def test_an_unscoped_chat_stays_unscoped() -> None:
    query = {"filters": {}}
    assert exclude_from_query(query, OFF) is query


def test_the_run_remembers_the_exclusion_and_the_catalog_drops_the_demo() -> None:
    state = {"available_connectors": [{"id": "demo-1", "type": "Demo"}, {"id": "jira-1", "type": "JIRA"}]}
    exclude_from_state(state, OFF)
    assert excluded_app_ids(state) == OFF
    assert [c["id"] for c in state["available_connectors"]] == ["jira-1"]
    assert excluded_app_ids({}) == frozenset()
