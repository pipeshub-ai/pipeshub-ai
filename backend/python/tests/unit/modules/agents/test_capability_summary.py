"""Unit tests for app.modules.agents.capability_summary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.agents.capability_summary import (
    _build_actions_section,
    _build_auth_status_section,
    _extract_domain_note,
    _get_all_tool_domains,
    build_capability_summary,
    classify_knowledge_sources,
    fetch_connector_configs,
    format_connector_filter_lines,
)

# ---------------------------------------------------------------------------
# Shared raw knowledge-list fixtures
# ---------------------------------------------------------------------------

KB_ENTRY  = {"displayName": "Company Wiki", "type": "KB"}
KB_ENTRY_WITH_IDS = {
    "displayName": "Vishwjeet's Private",
    "type": "KB",
    "connectorId": "kb-app-id-1",
}
KB_ENTRY2 = {"name": "HR Policies", "type": "KB"}

JIRA  = {"displayName": "Jira Project", "type": "jira",       "connectorId": "jira-cid-1"}
CONF  = {"displayName": "Confluence",   "type": "confluence", "connectorId": "conf-cid-2"}
SLACK = {"displayName": "Slack WS",     "type": "slack",      "connectorId": "slack-cid-3"}

# Pre-classified connector dicts (return value of classify_knowledge_sources)
_C_JIRA  = {"label": "Jira Project", "type_key": "jira",       "connector_id": "jira-cid-1",  "source_type": "app"}
_C_CONF  = {"label": "Confluence",   "type_key": "confluence", "connector_id": "conf-cid-2",  "source_type": "app"}
_C_SLACK = {"label": "Slack WS",     "type_key": "slack",      "connector_id": "slack-cid-3", "source_type": "app"}
_C_UNK   = {"label": "MyApp",        "type_key": "myapp",      "connector_id": "myapp-cid-9", "source_type": "app"}

# Pre-classified KB source dicts
_KB_NO_IDS   = {"label": "Company Wiki",        "connector_id": "",            "type_key": "", "source_type": "kb"}
_KB_WITH_IDS = {"label": "Vishwjeet's Private", "connector_id": "kb-app-id-1", "type_key": "", "source_type": "kb"}


def _split(sources: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the unified classify_knowledge_sources() list back into
    (kb, apps) purely for test readability."""
    kb = [s for s in sources if s["source_type"] == "kb"]
    apps = [s for s in sources if s["source_type"] == "app"]
    return kb, apps


# ===========================================================================
# classify_knowledge_sources
# ===========================================================================

class TestClassifyKnowledgeSources:

    # ── Empty / null inputs ──────────────────────────────────────────────────

    def test_empty_list(self):
        assert classify_knowledge_sources([]) == []

    def test_none_input(self):
        assert classify_knowledge_sources(None) == []

    def test_non_dict_entries_are_skipped(self):
        assert classify_knowledge_sources(["not-a-dict", 42, None]) == []

    # ── KB-only (no connector_id) ────────────────────────────────────────────

    def test_single_kb_entry_is_dict(self):
        """KB sources are dicts tagged source_type == 'kb', not plain strings."""
        kb, apps = _split(classify_knowledge_sources([KB_ENTRY]))
        assert len(kb) == 1
        assert isinstance(kb[0], dict)
        assert kb[0]["label"] == "Company Wiki"
        assert kb[0]["connector_id"] == ""
        assert apps == []

    def test_multiple_kb_entries_labels(self):
        kb, _ = _split(classify_knowledge_sources([KB_ENTRY, KB_ENTRY2]))
        labels = [k["label"] for k in kb]
        assert "Company Wiki" in labels
        assert "HR Policies" in labels

    def test_kb_fallback_label(self):
        kb, _ = _split(classify_knowledge_sources([{"type": "KB"}]))
        assert kb[0]["label"] == "Knowledge Base"
        assert kb[0]["connector_id"] == ""

    def test_kb_uses_name_field_when_no_display_name(self):
        kb, _ = _split(classify_knowledge_sources([{"name": "Legal KB", "type": "KB"}]))
        assert kb[0]["label"] == "Legal KB"

    # ── KB-only (with connector_id from the KB's own connectorId) ───────────

    def test_kb_extracts_connector_id_from_connector_id_field(self):
        kb, _ = _split(classify_knowledge_sources([KB_ENTRY_WITH_IDS]))
        assert len(kb) == 1
        assert kb[0]["label"] == "Vishwjeet's Private"
        assert kb[0]["connector_id"] == "kb-app-id-1"

    def test_kb_legacy_record_groups_filter_is_ignored(self):
        """KB id now comes from connectorId; legacy filters.recordGroups is no longer read."""
        entry = {
            "displayName": "Docs KB",
            "type": "KB",
            "connectorId": "kb-app-id-2",
            "filters": {"recordGroups": ["stale-legacy-rg"]},
        }
        kb, _ = _split(classify_knowledge_sources([entry]))
        assert kb[0]["connector_id"] == "kb-app-id-2"

    def test_kb_without_connector_id_has_empty_connector_id(self):
        entry = {"displayName": "Empty KB", "type": "KB"}
        kb, _ = _split(classify_knowledge_sources([entry]))
        assert kb[0]["connector_id"] == ""

    def test_kb_source_type_and_type_key(self):
        kb, _ = _split(classify_knowledge_sources([KB_ENTRY]))
        assert kb[0]["source_type"] == "kb"
        assert kb[0]["type_key"] == ""

    # ── Connector-only ───────────────────────────────────────────────────────

    def test_single_connector(self):
        kb, apps = _split(classify_knowledge_sources([JIRA]))
        assert kb == []
        assert len(apps) == 1
        assert apps[0] == {
            "label": "Jira Project", "type_key": "jira",
            "connector_id": "jira-cid-1", "source_type": "app",
        }

    def test_multiple_connectors_order_preserved(self):
        _, apps = _split(classify_knowledge_sources([JIRA, CONF, SLACK]))
        assert [a["type_key"] for a in apps] == ["jira", "confluence", "slack"]

    def test_connector_type_key_lowercased_and_first_word(self):
        _, apps = _split(classify_knowledge_sources(
            [{"displayName": "Jira Cloud", "type": "JIRA Cloud", "connectorId": "c1"}]
        ))
        assert apps[0]["type_key"] == "jira"

    def test_connector_without_connector_id_is_skipped(self):
        _, apps = _split(classify_knowledge_sources(
            [{"displayName": "Broken", "type": "jira", "connectorId": ""}]
        ))
        assert apps == []

    def test_connector_label_fallback_to_type_key_capitalize(self):
        _, apps = _split(classify_knowledge_sources([{"type": "slack", "connectorId": "s1"}]))
        assert apps[0]["label"] == "Slack"

    # ── KB + connector(s) ───────────────────────────────────────────────────

    def test_kb_no_ids_and_single_connector(self):
        kb, apps = _split(classify_knowledge_sources([KB_ENTRY, JIRA]))
        assert kb[0]["label"] == "Company Wiki"
        assert kb[0]["connector_id"] == ""
        assert apps[0]["type_key"] == "jira"

    def test_kb_with_ids_and_multiple_connectors(self):
        kb, apps = _split(classify_knowledge_sources([KB_ENTRY_WITH_IDS, JIRA, CONF]))
        assert kb[0]["connector_id"] == "kb-app-id-1"
        assert {a["type_key"] for a in apps} == {"jira", "confluence"}

    def test_multiple_kbs_and_multiple_connectors(self):
        kb, apps = _split(classify_knowledge_sources([KB_ENTRY, KB_ENTRY2, JIRA, CONF]))
        assert len(kb) == 2 and len(apps) == 2

    def test_order_within_unified_list_is_input_order(self):
        """The unified list preserves the input agent_knowledge order —
        callers that care about kb-vs-app grouping filter by source_type
        themselves rather than relying on positional grouping."""
        sources = classify_knowledge_sources([KB_ENTRY, JIRA, KB_ENTRY2])
        assert [s["label"] for s in sources] == ["Company Wiki", "Jira Project", "HR Policies"]

    # ── Mixed valid + invalid entries ────────────────────────────────────────

    def test_mixed_valid_invalid(self):
        entries = ["not-a-dict", KB_ENTRY, {"type": "jira", "connectorId": ""}, CONF]
        kb, apps = _split(classify_knowledge_sources(entries))
        assert kb[0]["label"] == "Company Wiki"
        assert len(apps) == 1 and apps[0]["type_key"] == "confluence"

    def test_connector_configs_attached_when_present(self):
        configs = {"jira-cid-1": {"sync": {"x": 1}, "indexing": {}}}
        sources = classify_knowledge_sources([JIRA], connector_configs=configs)
        assert "filters" in sources[0]
        assert sources[0]["filters"]["sync"] == {"x": 1}

    def test_connector_configs_empty_dict_not_attached(self):
        configs = {"jira-cid-1": {}}
        sources = classify_knowledge_sources([JIRA], connector_configs=configs)
        assert "filters" not in sources[0]

    def test_connector_configs_missing_id_no_filters(self):
        configs = {"other-id": {"sync": {}}}
        sources = classify_knowledge_sources([JIRA], connector_configs=configs)
        assert "filters" not in sources[0]

    def test_displayname_takes_priority_over_name(self):
        entry = {"type": "KB", "displayName": "Display", "name": "Raw"}
        kb, _ = _split(classify_knowledge_sources([entry]))
        assert kb[0]["label"] == "Display"

    def test_whitespace_connector_id_trimmed_to_empty(self):
        sources = classify_knowledge_sources([
            {"type": "Slack", "connectorId": "  "},
        ])
        assert sources == []

    def test_whitespace_type_with_connector_id(self):
        sources = classify_knowledge_sources([
            {"type": "   ", "connectorId": "c1"},
        ])
        assert len(sources) == 1
        assert sources[0]["source_type"] == "app"
        assert sources[0]["type_key"] == ""

    def test_kb_case_insensitive(self):
        kb, _ = _split(classify_knowledge_sources([{"type": "kb", "name": "Lower"}]))
        assert kb[0]["source_type"] == "kb"


# ===========================================================================
# fetch_connector_configs
# ===========================================================================

class TestFetchConnectorConfigs:
    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty(self):
        svc = AsyncMock()
        assert await fetch_connector_configs(svc, []) == {}

    @pytest.mark.asyncio
    async def test_none_ids_returns_empty(self):
        svc = AsyncMock()
        assert await fetch_connector_configs(svc, None) == {}

    @pytest.mark.asyncio
    async def test_none_service_returns_empty(self):
        assert await fetch_connector_configs(None, ["c1"]) == {}

    @pytest.mark.asyncio
    async def test_filters_non_string_ids(self):
        svc = AsyncMock()
        assert await fetch_connector_configs(svc, [123, None, ""]) == {}

    @pytest.mark.asyncio
    async def test_single_connector_success(self):
        svc = AsyncMock()
        svc.get_config = AsyncMock(return_value={
            "filters": {
                "sync": {"values": {"project_keys": ["P1"]}},
                "indexing": {"values": {"issues": True}},
            }
        })
        result = await fetch_connector_configs(svc, ["conn-1"])
        assert result["conn-1"]["sync"] == {"project_keys": ["P1"]}
        assert result["conn-1"]["indexing"] == {"issues": True}

    @pytest.mark.asyncio
    async def test_multiple_connectors(self):
        async def mock_get(path, **kw):
            if "conn-1" in path:
                return {"filters": {"sync": {"values": {"a": 1}}, "indexing": {"values": {"b": 2}}}}
            return {"filters": {"sync": {"values": {"c": 3}}, "indexing": {"values": {"d": 4}}}}

        svc = AsyncMock()
        svc.get_config = AsyncMock(side_effect=mock_get)
        result = await fetch_connector_configs(svc, ["conn-1", "conn-2"])
        assert len(result) == 2
        assert result["conn-1"]["sync"] == {"a": 1}
        assert result["conn-2"]["sync"] == {"c": 3}

    @pytest.mark.asyncio
    async def test_one_fails_others_succeed(self):
        async def mock_get(path, **kw):
            if "fail" in path:
                raise RuntimeError("boom")
            return {"filters": {"sync": {"values": {"x": 1}}, "indexing": {"values": {}}}}

        svc = AsyncMock()
        svc.get_config = AsyncMock(side_effect=mock_get)
        result = await fetch_connector_configs(svc, ["ok-1", "fail-1"])
        assert result["ok-1"]["sync"] == {"x": 1}
        assert result["fail-1"] == {}

    @pytest.mark.asyncio
    async def test_none_config_response(self):
        svc = AsyncMock()
        svc.get_config = AsyncMock(return_value=None)
        result = await fetch_connector_configs(svc, ["c1"])
        assert result["c1"] == {"sync": {}, "indexing": {}}

    @pytest.mark.asyncio
    async def test_config_without_filters_key(self):
        svc = AsyncMock()
        svc.get_config = AsyncMock(return_value={"other": "data"})
        result = await fetch_connector_configs(svc, ["c1"])
        assert result["c1"] == {"sync": {}, "indexing": {}}


# ===========================================================================
# format_connector_filter_lines
# ===========================================================================

class TestFormatConnectorFilterLines:
    def test_none_returns_empty(self):
        assert format_connector_filter_lines(None) == []

    def test_empty_dict_returns_empty(self):
        assert format_connector_filter_lines({}) == []

    def test_sync_list_with_labels(self):
        filters = {
            "sync": {
                "project_keys": {
                    "type": "list",
                    "value": [
                        {"label": "ProjectA", "id": "pa"},
                        {"label": "ProjectB", "id": "pb"},
                    ],
                }
            }
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 1
        assert "Scoped to:" in lines[0]
        assert "ProjectA" in lines[0]
        assert "ProjectB" in lines[0]
        assert "project keys" in lines[0]

    def test_sync_list_with_id_fallback(self):
        filters = {"sync": {"repos": {"type": "list", "value": [{"id": "repo-1"}]}}}
        lines = format_connector_filter_lines(filters)
        assert "repo-1" in lines[0]

    def test_sync_list_with_plain_strings(self):
        filters = {"sync": {"channels": {"type": "list", "value": ["general", "random"]}}}
        lines = format_connector_filter_lines(filters)
        assert "general" in lines[0] and "random" in lines[0]

    def test_sync_non_list_type_skipped(self):
        filters = {"sync": {"date": {"type": "date", "value": "2024-01-01"}}}
        assert format_connector_filter_lines(filters) == []

    def test_sync_list_no_value_skipped(self):
        assert format_connector_filter_lines({"sync": {"p": {"type": "list"}}}) == []

    def test_sync_list_empty_value_skipped(self):
        assert format_connector_filter_lines({"sync": {"p": {"type": "list", "value": []}}}) == []

    def test_sync_skips_none_and_empty_items(self):
        filters = {"sync": {"i": {"type": "list", "value": [None, "", {"label": "Good"}]}}}
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 1 and "Good" in lines[0]

    def test_sync_skips_dict_without_label_or_id(self):
        filters = {"sync": {"i": {"type": "list", "value": [{"desc": "no label"}]}}}
        assert format_connector_filter_lines(filters) == []

    def test_indexing_boolean_true_only(self):
        filters = {
            "indexing": {
                "issues": {"type": "boolean", "value": True},
                "attachments": {"type": "boolean", "value": False},
                "pages": {"type": "boolean", "value": True},
            }
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 1
        assert "Content indexed:" in lines[0]
        assert "issues" in lines[0] and "pages" in lines[0]
        assert "attachments" not in lines[0]

    def test_indexing_skips_enable_prefix(self):
        filters = {
            "indexing": {
                "enable_manual_sync": {"type": "boolean", "value": True},
                "issues": {"type": "boolean", "value": True},
            }
        }
        lines = format_connector_filter_lines(filters)
        assert "enable" not in lines[0] and "issues" in lines[0]

    def test_indexing_non_boolean_skipped(self):
        assert format_connector_filter_lines({"indexing": {"x": {"type": "string", "value": "y"}}}) == []

    def test_mixed_sync_and_indexing(self):
        filters = {
            "sync": {"proj": {"type": "list", "value": [{"label": "X"}]}},
            "indexing": {"issues": {"type": "boolean", "value": True}},
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 2
        assert "Scoped to:" in lines[0] and "Content indexed:" in lines[1]

    def test_sync_not_dict_skipped(self):
        assert format_connector_filter_lines({"sync": "str"}) == []

    def test_indexing_not_dict_skipped(self):
        assert format_connector_filter_lines({"indexing": 42}) == []

    def test_sync_entry_not_dict_skipped(self):
        assert format_connector_filter_lines({"sync": {"k": "not-a-dict"}}) == []

    def test_sync_value_not_list_skipped(self):
        assert format_connector_filter_lines({"sync": {"k": {"type": "list", "value": "s"}}}) == []

    def test_multiple_sync_list_filters_joined(self):
        filters = {
            "sync": {
                "project_keys": {"type": "list", "value": [{"label": "P1"}]},
                "channels": {"type": "list", "value": [{"label": "C1"}]},
            }
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 1
        assert "P1" in lines[0] and "C1" in lines[0] and ";" in lines[0]


# ===========================================================================
# _extract_domain_note
# ===========================================================================

class TestExtractDomainNote:
    def test_empty_string(self):
        assert _extract_domain_note("") == ""

    def test_use_this_to_marker(self):
        result = _extract_domain_note("Tool for math. Use this to calculate expressions. More.")
        assert result == "calculate expressions"

    def test_use_only_for_marker(self):
        result = _extract_domain_note("Use ONLY for running SQL queries against databases.")
        assert "running SQL queries against databases" in result

    def test_useful_for_marker(self):
        result = _extract_domain_note("Useful for date math and timezone conversions. More.")
        assert result == "date math and timezone conversions"

    def test_first_sentence_fallback(self):
        result = _extract_domain_note("Performs calculations on numbers. More details.")
        assert result == "Performs calculations on numbers"

    def test_truncation_at_max_chars(self):
        result = _extract_domain_note("Use this to " + "x" * 200, max_chars=20)
        assert len(result) <= 20

    def test_newline_separator_in_marker(self):
        assert _extract_domain_note("Use this to do X\nMore stuff") == "do X"

    def test_plain_description_returns_truncated(self):
        desc = "Just a plain description without any markers or sentences"
        assert _extract_domain_note(desc)

    def test_period_newline_sentence(self):
        assert _extract_domain_note("Searches knowledge.\nMore details.") == "Searches knowledge"

    def test_period_space_sentence(self):
        assert _extract_domain_note("Fetches records from the database. Returns JSON.") == "Fetches records from the database"

    def test_trailing_period_stripped_from_marker(self):
        result = _extract_domain_note("Use this to parse JSON data.")
        assert result == "parse JSON data"
        assert not result.endswith(".")


# ===========================================================================
# _build_actions_section
# ===========================================================================

class TestBuildActionsSection:
    def test_empty_domains(self):
        parts = []
        _build_actions_section(domains={}, domain_notes={}, parts=parts)
        assert "No tools configured" in "\n".join(parts)

    def test_single_domain(self):
        parts = []
        _build_actions_section(
            domains={"slack": ["send message", "list channels"]},
            domain_notes={},
            parts=parts,
        )
        combined = "\n".join(parts)
        assert "Slack" in combined
        assert "send message" in combined

    def test_domain_with_note(self):
        parts = []
        _build_actions_section(
            domains={"calculator": ["calculate"]},
            domain_notes={"calculator": "math operations"},
            parts=parts,
        )
        assert "math operations" in "\n".join(parts)

    def test_domains_sorted(self):
        parts = []
        _build_actions_section(
            domains={"z_tool": ["a"], "a_tool": ["b"]},
            domain_notes={},
            parts=parts,
        )
        a_idx = next(i for i, p in enumerate(parts) if "A Tool" in p)
        z_idx = next(i for i, p in enumerate(parts) if "Z Tool" in p)
        assert a_idx < z_idx

    def test_header_always_present(self):
        parts = []
        _build_actions_section(domains={"x": ["y"]}, domain_notes={}, parts=parts)
        assert "### Available Actions" in parts


# ===========================================================================
# _build_auth_status_section
# ===========================================================================

class TestBuildAuthStatusSection:
    def test_no_failures(self):
        parts = []
        _build_auth_status_section(state={}, parts=parts)
        assert parts == []

    def test_non_auth_failures_no_output(self):
        parts = []
        _build_auth_status_section(
            state={"toolset_load_failures": {"slack": "import_error"}},
            parts=parts,
        )
        assert parts == []

    def test_unauthenticated_listed(self):
        parts = []
        _build_auth_status_section(
            state={"toolset_load_failures": {
                "slack": "not_authenticated",
                "jira": "not_authenticated",
                "gmail": "import_error",
            }},
            parts=parts,
        )
        combined = "\n".join(parts)
        assert "Needs Authentication" in combined
        assert "- jira" in combined and "- slack" in combined
        assert "gmail" not in combined

    def test_sorted_alphabetically(self):
        parts = []
        _build_auth_status_section(
            state={"toolset_load_failures": {
                "z_tool": "not_authenticated",
                "a_tool": "not_authenticated",
            }},
            parts=parts,
        )
        a_idx = next(i for i, p in enumerate(parts) if "a_tool" in p)
        z_idx = next(i for i, p in enumerate(parts) if "z_tool" in p)
        assert a_idx < z_idx

    def test_none_failures_no_output(self):
        parts = []
        _build_auth_status_section(state={"toolset_load_failures": None}, parts=parts)
        assert parts == []


# ===========================================================================
# _get_all_tool_domains
# ===========================================================================

class TestGetAllToolDomains:
    def test_empty_state(self):
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, notes = _get_all_tool_domains({})
        assert domains == {} and notes == {}

    def test_dotted_tools_from_state(self):
        state = {"tools": ["slack.send_message", "slack.list_channels", "jira.get_issue"]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, notes = _get_all_tool_domains(state)
        assert "slack" in domains and "jira" in domains
        assert "send message" in domains["slack"]
        assert notes == {}

    def test_deduplicates_tools(self):
        state = {"tools": ["s.a", "s.a", "s.b"]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert domains["s"] == ["a", "b"]

    @staticmethod
    def _mock_tool_system(tools):
        """Mock the lazily-imported tool_system module so ``_get_all_tool_domains``
        picks up our fake ``get_agent_tools_with_schemas`` via its local import."""
        import sys
        from unittest.mock import MagicMock
        mod = MagicMock()
        mod.get_agent_tools_with_schemas = MagicMock(return_value=tools)
        return patch.dict(sys.modules, {"app.modules.agents.qna.tool_system": mod})

    def test_runtime_tools_primary_path(self):
        tool1 = SimpleNamespace(
            name="slack.send_message", _original_name="slack.send_message",
            description="Send a Slack message",
        )
        tool2 = SimpleNamespace(
            name="calculator", _original_name="calculator",
            description="Use this to calculate math expressions. More stuff.",
        )
        with self._mock_tool_system([tool1, tool2]):
            state = {"tools": ["slack.send_message"]}
            domains, notes = _get_all_tool_domains(state)
        assert "slack" in domains and "utility" in domains
        assert "calculate math expressions" in notes["utility"]

    def test_runtime_tools_skips_empty_names(self):
        tool = SimpleNamespace(name="", _original_name="", description="desc")
        with self._mock_tool_system([tool]):
            domains, _ = _get_all_tool_domains({})
        assert domains == {}

    def test_runtime_tools_dedup(self):
        t1 = SimpleNamespace(name="s.a", _original_name="s.a", description="")
        t2 = SimpleNamespace(name="s.a", _original_name="s.a", description="")
        with self._mock_tool_system([t1, t2]):
            domains, _ = _get_all_tool_domains({})
        assert domains["s"] == ["a"]

    def test_agent_toolsets_fallback(self):
        state = {
            "tools": [],
            "agent_toolsets": [{
                "name": "slack",
                "tools": [{"fullName": "slack.send_message", "name": "send_message"}],
                "selectedTools": ["slack.list_channels"],
            }],
        }
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert "send message" in domains["slack"] and "list channels" in domains["slack"]

    def test_agent_toolsets_tool_name_only(self):
        state = {"tools": [], "agent_toolsets": [{"name": "jira", "tools": [{"name": "get_issue"}]}]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert "get issue" in domains["jira"]

    def test_agent_toolsets_skips_non_dict(self):
        state = {"tools": [], "agent_toolsets": ["not-a-dict"]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert domains == {}

    def test_agent_toolsets_skips_empty_name(self):
        state = {"tools": [], "agent_toolsets": [{"name": "", "tools": [{"name": "x"}]}]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert domains == {}

    def test_selected_tools_no_dot(self):
        state = {"tools": [], "agent_toolsets": [{"name": "util", "selectedTools": ["no_dot_name"]}]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert "no dot name" in domains["util"]

    def test_non_dotted_state_tools_ignored(self):
        state = {"tools": ["bare_tool_name"]}
        with patch("app.modules.agents.capability_summary.get_agent_tools_with_schemas", create=True, side_effect=Exception):
            domains, _ = _get_all_tool_domains(state)
        assert domains == {}

    def test_service_domains_no_notes(self):
        tool = SimpleNamespace(
            name="slack.send_message", _original_name="slack.send_message",
            description="Use this to send a message.",
        )
        with self._mock_tool_system([tool]):
            _, notes = _get_all_tool_domains({"tools": ["slack.send_message"]})
        assert "slack" not in notes

    def test_web_tool_categorized_as_web(self):
        tool = SimpleNamespace(
            name="fetch_url", _original_name="fetch_url",
            description="Fetch a URL.",
        )
        with self._mock_tool_system([tool]):
            domains, _ = _get_all_tool_domains({})
        assert "web" in domains


# ===========================================================================
# build_capability_summary
# ===========================================================================

class TestBuildCapabilitySummary:
    def test_no_tools_returns_empty(self):
        with patch("app.modules.agents.capability_summary._get_all_tool_domains", return_value=({}, {})):
            assert build_capability_summary({}) == ""

    def test_with_tools_returns_summary(self):
        with patch("app.modules.agents.capability_summary._get_all_tool_domains", return_value=({"slack": ["send"]}, {})):
            result = build_capability_summary({})
        assert "## Capability Summary" in result and "Slack" in result

    def test_includes_auth_section(self):
        state = {"toolset_load_failures": {"jira": "not_authenticated"}}
        with patch("app.modules.agents.capability_summary._get_all_tool_domains", return_value=({"slack": ["x"]}, {})):
            result = build_capability_summary(state)
        assert "Needs Authentication" in result and "jira" in result

    def test_no_auth_section_when_empty(self):
        with patch("app.modules.agents.capability_summary._get_all_tool_domains", return_value=({"slack": ["x"]}, {})):
            result = build_capability_summary({})
        assert "Needs Authentication" not in result

    def test_domain_notes_in_output(self):
        with patch("app.modules.agents.capability_summary._get_all_tool_domains", return_value=({"calc": ["eval"]}, {"calc": "math ops"})):
            result = build_capability_summary({})
        assert "math ops" in result

    def test_multiple_domains_all_present(self):
        with patch("app.modules.agents.capability_summary._get_all_tool_domains", return_value=({"slack": ["send"], "jira": ["create"]}, {})):
            result = build_capability_summary({})
        assert "Slack" in result and "Jira" in result

