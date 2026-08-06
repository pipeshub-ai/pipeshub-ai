"""Unit tests for `preload_native_filter_search`: renders exactly one line
per filter-capable connector (name, id, tool) with no record-group dump,
and never raises (a preload failure must never break prompt assembly)."""

from unittest.mock import AsyncMock, MagicMock

from app.agents.actions.filtered_search import prompt_preload
from app.agents.actions.filtered_search.adapters.jira import JiraFilterAdapter
from app.agents.actions.knowledge_graph.catalog import ConnectorInfo


async def test_no_graph_provider_leaves_state_key_unset() -> None:
    context = MagicMock()
    context.tool_state = {}
    await prompt_preload.preload_native_filter_search(context)
    assert prompt_preload.STATE_KEY not in context.tool_state


async def test_no_filter_capable_connectors_leaves_state_key_unset(monkeypatch) -> None:
    monkeypatch.setattr(prompt_preload, "get_user_key", AsyncMock(return_value="k1"))
    catalog = MagicMock()
    catalog.is_empty.return_value = False
    catalog.connectors = [ConnectorInfo(id="c1", name="Some KB", type="KB")]
    monkeypatch.setattr(
        "app.agents.actions.knowledge_graph.catalog.ConnectorCatalog.build", AsyncMock(return_value=catalog),
    )
    context = MagicMock()
    context.tool_state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}

    await prompt_preload.preload_native_filter_search(context)

    assert prompt_preload.STATE_KEY not in context.tool_state


async def test_renders_one_line_per_filter_capable_connector(monkeypatch) -> None:
    monkeypatch.setattr(prompt_preload, "get_user_key", AsyncMock(return_value="k1"))
    catalog = MagicMock()
    catalog.is_empty.return_value = False
    catalog.connectors = [ConnectorInfo(id="c1", name="Jira Cloud", type="JIRA")]
    monkeypatch.setattr(
        "app.agents.actions.knowledge_graph.catalog.ConnectorCatalog.build", AsyncMock(return_value=catalog),
    )
    monkeypatch.setattr(
        prompt_preload.FilterAdapterRegistry, "get",
        lambda ct: JiraFilterAdapter if ct == "JIRA" else None,
    )

    context = MagicMock()
    context.tool_state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}

    await prompt_preload.preload_native_filter_search(context)

    text = context.tool_state[prompt_preload.STATE_KEY]
    assert "Jira Cloud" in text
    assert "c1" in text
    assert "search_jira_issues" in text
    # No vocabulary dump left in this section.
    assert "Supported filters" not in text
    assert "Entity types" not in text


async def test_multiple_connectors_render_one_line_each(monkeypatch) -> None:
    monkeypatch.setattr(prompt_preload, "get_user_key", AsyncMock(return_value="k1"))
    catalog = MagicMock()
    catalog.is_empty.return_value = False
    catalog.connectors = [
        ConnectorInfo(id="c1", name="Jira Cloud", type="JIRA"),
        ConnectorInfo(id="c2", name="Confluence Cloud", type="CONFLUENCE"),
        ConnectorInfo(id="c3", name="Some KB", type="KB"),
    ]
    monkeypatch.setattr(
        "app.agents.actions.knowledge_graph.catalog.ConnectorCatalog.build", AsyncMock(return_value=catalog),
    )

    context = MagicMock()
    context.tool_state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}

    await prompt_preload.preload_native_filter_search(context)

    text = context.tool_state[prompt_preload.STATE_KEY]
    assert "search_jira_issues" in text
    assert "search_confluence_content" in text
    assert "Some KB" not in text
    assert text.count("connector_id") == 0  # rendered as `id` inline, not this literal key
    assert len([line for line in text.splitlines() if line.startswith("- ")]) == 2


async def test_render_exception_is_swallowed_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(prompt_preload, "get_user_key", AsyncMock(side_effect=RuntimeError("boom")))
    context = MagicMock()
    context.tool_state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}

    await prompt_preload.preload_native_filter_search(context)  # must not raise

    assert prompt_preload.STATE_KEY not in context.tool_state
