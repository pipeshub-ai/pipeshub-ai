"""Unit tests for the `FilteredSearch` toolset (`search_jira_issues`,
`search_confluence_content`, `search_slack_messages`, `describe_filter_schema`):
connector resolution failure modes, connector-type-mismatch guarding, and
delegation to `run_filtered_search`/adapter `discover_custom_fields`.

Also asserts the exact registered tool names — the model calling the wrong
tool (or an old removed `search_by_app_filters`) is exactly the failure
mode this native-query split was meant to make impossible to construct."""

import json
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.actions.filtered_search import tools as tools_module
from app.agents.actions.filtered_search.adapters.confluence import ConfluenceFilterAdapter
from app.agents.actions.filtered_search.adapters.jira import JiraFilterAdapter
from app.agents.actions.filtered_search.adapters.slack import SlackFilterAdapter
from app.agents.actions.filtered_search.models import CustomFieldDef, FilterCapabilityDescriptor
from app.agents.actions.filtered_search.tools import FilteredSearch
from app.agents.actions.filtered_search.vocabulary_tools import FilterVocabulary
from app.models.entities import RecordGroupType


def _make_tool(state) -> FilteredSearch:
    tool = FilteredSearch.__new__(FilteredSearch)
    tool.state = state
    return tool


def _tool_name(meta_path: str) -> str:
    app_name = meta_path.rsplit("/", 2)[-2]
    short_name = meta_path.rsplit("/", 1)[-1]
    return f"{app_name}__{short_name}"


def test_registered_tool_names_are_exactly_the_expected_set() -> None:
    names = set()
    for cls in (FilteredSearch, FilterVocabulary):
        for attr in vars(cls).values():
            meta = getattr(attr, TOOL_META_ATTR, None)
            if meta is not None:
                names.add(_tool_name(meta.path))

    assert names == {
        "filtered_search__search_jira_issues",
        "filtered_search__search_confluence_content",
        "filtered_search__search_slack_messages",
        "filtered_search__describe_filter_schema",
        "filter_vocabulary__list_filter_values",
        "filter_vocabulary__people_search",
    }


class TestSearchJiraIssues:
    async def test_no_state_returns_error(self) -> None:
        tool = _make_tool(None)
        ok, payload = await tool.search_jira_issues(connector_id="c1", jql="project = X")
        assert ok is False
        assert "not initialized" in json.loads(payload)["error"]

    async def test_unresolvable_connector_returns_not_found_error(self, monkeypatch) -> None:
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=None))
        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.search_jira_issues(connector_id="bad-id", jql="project = X")
        assert ok is False
        assert "bad-id" in json.loads(payload)["error"]

    async def test_connector_type_without_adapter_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("KB", MagicMock())))
        monkeypatch.setattr(tools_module.FilterAdapterRegistry, "get", lambda ct: None)
        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.search_jira_issues(connector_id="c1", jql="project = X")
        assert ok is False
        assert "does not support filter search" in json.loads(payload)["error"]

    async def test_wrong_connector_type_for_this_tool_returns_actionable_error(self, monkeypatch) -> None:
        """Calling search_jira_issues against a Confluence connector_id must
        be rejected with a pointer to the right tool, not silently attempt
        Jira's client methods against a Confluence client."""
        monkeypatch.setattr(
            tools_module, "resolve_client_for_connector", AsyncMock(return_value=("CONFLUENCE", MagicMock())),
        )
        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.search_jira_issues(connector_id="c1", jql="project = X")
        assert ok is False
        error = json.loads(payload)["error"]
        assert "CONFLUENCE" in error
        assert "search_confluence_content" in error

    async def test_valid_call_delegates_to_run_filtered_search_with_jql(self, monkeypatch) -> None:
        client = MagicMock()
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("JIRA", client)))
        run_mock = AsyncMock(return_value=(True, json.dumps({"records": []})))
        monkeypatch.setattr(tools_module, "run_filtered_search", run_mock)

        tool = _make_tool({"org_id": "o1"})
        ok, _ = await tool.search_jira_issues(
            connector_id="c1", jql='project = "ES"', content_query="token refresh", limit=25,
        )

        assert ok is True
        run_mock.assert_awaited_once()
        kwargs = run_mock.call_args.kwargs
        assert kwargs["connector_id"] == "c1"
        assert kwargs["connector_type"] == "JIRA"
        assert kwargs["client"] is client
        assert kwargs["query"] == 'project = "ES"'
        assert kwargs["limit"] == 25
        # `content_query` must never reach `run_filtered_search` — that is
        # the POST hook's job, not the tool's.
        assert "content_query" not in kwargs


class TestSearchConfluenceContent:
    async def test_valid_call_uses_confluence_adapter(self, monkeypatch) -> None:
        client = MagicMock()
        monkeypatch.setattr(
            tools_module, "resolve_client_for_connector", AsyncMock(return_value=("CONFLUENCE", client)),
        )
        run_mock = AsyncMock(return_value=(True, json.dumps({"records": []})))
        monkeypatch.setattr(tools_module, "run_filtered_search", run_mock)

        tool = _make_tool({"org_id": "o1"})
        ok, _ = await tool.search_confluence_content(connector_id="c1", cql='space = "ENG"')

        assert ok is True
        assert run_mock.call_args.kwargs["query"] == 'space = "ENG"'

    async def test_wrong_connector_type_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("JIRA", MagicMock())))
        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.search_confluence_content(connector_id="c1", cql='space = "ENG"')
        assert ok is False
        assert "search_jira_issues" in json.loads(payload)["error"]


class TestSearchSlackMessages:
    async def test_valid_call_uses_slack_adapter(self, monkeypatch) -> None:
        client = MagicMock()
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("SLACK", client)))
        run_mock = AsyncMock(return_value=(True, json.dumps({"records": []})))
        monkeypatch.setattr(tools_module, "run_filtered_search", run_mock)

        tool = _make_tool({"org_id": "o1"})
        ok, _ = await tool.search_slack_messages(connector_id="c1", query="in:<#C1>")

        assert ok is True
        assert run_mock.call_args.kwargs["query"] == "in:<#C1>"

    async def test_wrong_connector_type_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("JIRA", MagicMock())))
        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.search_slack_messages(connector_id="c1", query="in:<#C1>")
        assert ok is False
        assert "search_jira_issues" in json.loads(payload)["error"]


class TestDescribeFilterSchema:
    async def test_no_state_returns_error(self) -> None:
        tool = _make_tool(None)
        ok, _ = await tool.describe_filter_schema(connector_id="c1")
        assert ok is False

    async def test_unresolvable_connector_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=None))
        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.describe_filter_schema(connector_id="bad-id")
        assert ok is False
        assert "bad-id" in json.loads(payload)["error"]

    async def test_connector_without_custom_fields_returns_empty_with_message(self, monkeypatch) -> None:
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("SLACK", MagicMock())))
        monkeypatch.setattr(tools_module.FilterAdapterRegistry, "get", lambda ct: SlackFilterAdapter)

        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.describe_filter_schema(connector_id="c1")

        assert ok is True
        data = json.loads(payload)
        assert data["fields"] == []
        assert "SLACK" in data["message"]

    async def test_discovers_custom_fields_when_supported(self, monkeypatch) -> None:
        adapter_instance = MagicMock()
        adapter_instance.discover_custom_fields = AsyncMock(return_value=[
            CustomFieldDef(field_id="customfield_1", name="Story Points", field_type="number"),
        ])
        adapter_cls = MagicMock()
        adapter_cls.capabilities.return_value = FilterCapabilityDescriptor(
            connector_type="JIRA", record_group_noun="project", container_group_types=[RecordGroupType.PROJECT],
            group_reference="SHORT_NAME", supports_custom_fields=True,
        )
        adapter_cls.return_value = adapter_instance
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("JIRA", MagicMock())))
        monkeypatch.setattr(tools_module.FilterAdapterRegistry, "get", lambda ct: adapter_cls)

        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.describe_filter_schema(connector_id="c1")

        assert ok is True
        data = json.loads(payload)
        assert data["fields"][0]["field_id"] == "customfield_1"

    async def test_not_implemented_returns_empty_fields_not_error(self, monkeypatch) -> None:
        adapter_instance = MagicMock()
        adapter_instance.discover_custom_fields = AsyncMock(side_effect=NotImplementedError)
        adapter_cls = MagicMock()
        adapter_cls.capabilities.return_value = MagicMock(supports_custom_fields=True)
        adapter_cls.return_value = adapter_instance
        monkeypatch.setattr(tools_module, "resolve_client_for_connector", AsyncMock(return_value=("SLACK", MagicMock())))
        monkeypatch.setattr(tools_module.FilterAdapterRegistry, "get", lambda ct: adapter_cls)

        tool = _make_tool({"org_id": "o1"})
        ok, payload = await tool.describe_filter_schema(connector_id="c1")

        assert ok is True
        assert json.loads(payload)["fields"] == []


def test_native_query_param_by_path_matches_tool_signatures() -> None:
    """Guards the PRE hook's assumption: for each filtered-search tool path,
    the mapped key must be an actual parameter name the tool declares."""
    assert tools_module.NATIVE_QUERY_PARAM_BY_PATH[tools_module.SEARCH_JIRA_ISSUES_PATH] == "jql"
    assert tools_module.NATIVE_QUERY_PARAM_BY_PATH[tools_module.SEARCH_CONFLUENCE_CONTENT_PATH] == "cql"
    assert tools_module.NATIVE_QUERY_PARAM_BY_PATH[tools_module.SEARCH_SLACK_MESSAGES_PATH] == "query"


def test_tool_path_by_adapter_covers_all_three_adapters() -> None:
    assert set(tools_module.TOOL_PATH_BY_ADAPTER) == {
        JiraFilterAdapter, ConfluenceFilterAdapter, SlackFilterAdapter,
    }
