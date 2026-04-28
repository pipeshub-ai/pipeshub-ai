"""
Unit tests for app.modules.agents.capability_summary

Covers:
  - classify_knowledge_sources  (return type is now list[dict] for KB sources)
  - build_connector_routing_rules  (collection_ids + connector_ids examples)

Test-case matrix
────────────────
Config                       | classify | routing
KB only (no collection_ids)  |   ✓      |   ✓
KB only (with collection_ids)|   ✓      |   ✓
Connector only               |   ✓      |   ✓
Multi-connector              |   ✓      |   ✓
KB + connector               |   ✓      |   ✓
KB (w/ ids) + multi-connector|   ✓      |   ✓
Empty                        |   ✓      |   ✓
"""

import pytest

from app.modules.agents.capability_summary import (
    build_connector_routing_rules,
    classify_knowledge_sources,
)

# ---------------------------------------------------------------------------
# Shared raw knowledge-list fixtures
# ---------------------------------------------------------------------------

KB_ENTRY  = {"displayName": "Company Wiki", "type": "KB"}
KB_ENTRY_WITH_IDS = {
    "displayName": "Vishwjeet's Private",
    "type": "KB",
    "filters": {"recordGroups": ["rg-id-aaa", "rg-id-bbb"]},
}
KB_ENTRY2 = {"name": "HR Policies", "type": "KB"}

JIRA  = {"displayName": "Jira Project", "type": "jira",       "connectorId": "jira-cid-1"}
CONF  = {"displayName": "Confluence",   "type": "confluence", "connectorId": "conf-cid-2"}
SLACK = {"displayName": "Slack WS",     "type": "slack",      "connectorId": "slack-cid-3"}

# Pre-classified connector dicts (return value of classify_knowledge_sources)
_C_JIRA  = {"label": "Jira Project", "type_key": "jira",       "connector_id": "jira-cid-1"}
_C_CONF  = {"label": "Confluence",   "type_key": "confluence", "connector_id": "conf-cid-2"}
_C_SLACK = {"label": "Slack WS",     "type_key": "slack",      "connector_id": "slack-cid-3"}
_C_UNK   = {"label": "MyApp",        "type_key": "myapp",      "connector_id": "myapp-cid-9"}

# Pre-classified KB source dicts
_KB_NO_IDS   = {"label": "Company Wiki",        "collection_ids": []}
_KB_WITH_IDS = {"label": "Vishwjeet's Private", "collection_ids": ["rg-id-aaa", "rg-id-bbb"]}


# ===========================================================================
# classify_knowledge_sources
# ===========================================================================

class TestClassifyKnowledgeSources:

    # ── Empty / null inputs ──────────────────────────────────────────────────

    def test_empty_list(self):
        kb, apps = classify_knowledge_sources([])
        assert kb == [] and apps == []

    def test_none_input(self):
        kb, apps = classify_knowledge_sources(None)
        assert kb == [] and apps == []

    def test_non_dict_entries_are_skipped(self):
        kb, apps = classify_knowledge_sources(["not-a-dict", 42, None])
        assert kb == [] and apps == []

    # ── KB-only (no collection_ids) ──────────────────────────────────────────

    def test_single_kb_entry_is_dict(self):
        """KB sources are now returned as dicts, not plain strings."""
        kb, apps = classify_knowledge_sources([KB_ENTRY])
        assert len(kb) == 1
        assert isinstance(kb[0], dict)
        assert kb[0]["label"] == "Company Wiki"
        assert kb[0]["collection_ids"] == []
        assert apps == []

    def test_multiple_kb_entries_labels(self):
        kb, _ = classify_knowledge_sources([KB_ENTRY, KB_ENTRY2])
        labels = [k["label"] for k in kb]
        assert "Company Wiki" in labels
        assert "HR Policies" in labels

    def test_kb_fallback_label(self):
        kb, _ = classify_knowledge_sources([{"type": "KB"}])
        assert kb[0]["label"] == "Knowledge Base"
        assert kb[0]["collection_ids"] == []

    def test_kb_uses_name_field_when_no_display_name(self):
        kb, _ = classify_knowledge_sources([{"name": "Legal KB", "type": "KB"}])
        assert kb[0]["label"] == "Legal KB"

    # ── KB-only (with collection_ids from filters.recordGroups) ─────────────

    def test_kb_extracts_collection_ids_from_filters(self):
        kb, _ = classify_knowledge_sources([KB_ENTRY_WITH_IDS])
        assert len(kb) == 1
        assert kb[0]["label"] == "Vishwjeet's Private"
        assert "rg-id-aaa" in kb[0]["collection_ids"]
        assert "rg-id-bbb" in kb[0]["collection_ids"]

    def test_kb_with_json_string_filters(self):
        import json
        entry = {
            "displayName": "Docs KB",
            "type": "KB",
            "filters": json.dumps({"recordGroups": ["rg-json-id"]}),
        }
        kb, _ = classify_knowledge_sources([entry])
        assert kb[0]["collection_ids"] == ["rg-json-id"]

    def test_kb_with_empty_record_groups(self):
        entry = {"displayName": "Empty KB", "type": "KB", "filters": {"recordGroups": []}}
        kb, _ = classify_knowledge_sources([entry])
        assert kb[0]["collection_ids"] == []

    # ── Connector-only ───────────────────────────────────────────────────────

    def test_single_connector(self):
        kb, apps = classify_knowledge_sources([JIRA])
        assert kb == []
        assert len(apps) == 1
        assert apps[0] == {"label": "Jira Project", "type_key": "jira", "connector_id": "jira-cid-1"}

    def test_multiple_connectors_order_preserved(self):
        _, apps = classify_knowledge_sources([JIRA, CONF, SLACK])
        assert [a["type_key"] for a in apps] == ["jira", "confluence", "slack"]

    def test_connector_type_key_lowercased_and_first_word(self):
        _, apps = classify_knowledge_sources(
            [{"displayName": "Jira Cloud", "type": "JIRA Cloud", "connectorId": "c1"}]
        )
        assert apps[0]["type_key"] == "jira"

    def test_connector_without_connector_id_is_skipped(self):
        _, apps = classify_knowledge_sources(
            [{"displayName": "Broken", "type": "jira", "connectorId": ""}]
        )
        assert apps == []

    def test_connector_label_fallback_to_type_key_capitalize(self):
        _, apps = classify_knowledge_sources([{"type": "slack", "connectorId": "s1"}])
        assert apps[0]["label"] == "Slack"

    # ── KB + connector(s) ───────────────────────────────────────────────────

    def test_kb_no_ids_and_single_connector(self):
        kb, apps = classify_knowledge_sources([KB_ENTRY, JIRA])
        assert kb[0]["label"] == "Company Wiki"
        assert kb[0]["collection_ids"] == []
        assert apps[0]["type_key"] == "jira"

    def test_kb_with_ids_and_multiple_connectors(self):
        kb, apps = classify_knowledge_sources([KB_ENTRY_WITH_IDS, JIRA, CONF])
        assert "rg-id-aaa" in kb[0]["collection_ids"]
        assert {a["type_key"] for a in apps} == {"jira", "confluence"}

    def test_multiple_kbs_and_multiple_connectors(self):
        kb, apps = classify_knowledge_sources([KB_ENTRY, KB_ENTRY2, JIRA, CONF])
        assert len(kb) == 2 and len(apps) == 2

    # ── Mixed valid + invalid entries ────────────────────────────────────────

    def test_mixed_valid_invalid(self):
        entries = ["not-a-dict", KB_ENTRY, {"type": "jira", "connectorId": ""}, CONF]
        kb, apps = classify_knowledge_sources(entries)
        assert kb[0]["label"] == "Company Wiki"
        assert len(apps) == 1 and apps[0]["type_key"] == "confluence"


# ===========================================================================
# build_connector_routing_rules
# ===========================================================================

class TestBuildConnectorRoutingRulesEmpty:

    def test_no_sources_returns_empty(self):
        assert build_connector_routing_rules([]) == ""

    def test_no_sources_with_fallback_note(self):
        note = "KB-only fallback"
        assert build_connector_routing_rules([], kb_only_note=note) == note

    def test_empty_kb_sources_and_no_connectors(self):
        assert build_connector_routing_rules([], kb_sources=[]) == ""


class TestBuildConnectorRoutingRulesKBOnly:
    """KB sources only — collection_ids path, no app connectors."""

    def test_kb_with_ids_shows_collection_ids_in_output(self):
        result = build_connector_routing_rules([], kb_sources=[_KB_WITH_IDS])
        assert "rg-id-aaa" in result
        assert "collection_ids" in result

    def test_kb_without_ids_shows_omit_guidance(self):
        result = build_connector_routing_rules([], kb_sources=[_KB_NO_IDS])
        # Either "omit" OR just "collection_ids" guidance
        assert "omit" in result.lower() or "collection_ids" in result

    def test_kb_only_routing_block_header(self):
        result = build_connector_routing_rules([], kb_sources=[_KB_WITH_IDS])
        assert "KB-only configuration" in result

    def test_kb_only_example_uses_collection_ids(self):
        result = build_connector_routing_rules([], kb_sources=[_KB_WITH_IDS],
                                               call_format="planner")
        assert "rg-id-aaa" in result
        assert "collection_ids" in result

    def test_kb_only_orchestrator_task_uses_collection_ids(self):
        result = build_connector_routing_rules([], kb_sources=[_KB_WITH_IDS],
                                               call_format="orchestrator")
        assert "retrieval_kb_" in result
        assert "collection_ids" in result
        assert "rg-id-aaa" in result


class TestBuildConnectorRoutingRulesSingleConnector:
    """Single app connector — connector_ids path."""

    def test_identity_block_contains_connector_info(self):
        result = build_connector_routing_rules([_C_JIRA])
        assert "Jira Project" in result
        assert "jira-cid-1" in result

    def test_parameter_rule_connector_ids_present(self):
        result = build_connector_routing_rules([_C_JIRA])
        assert "connector_ids" in result
        assert "Parameter rules" in result

    def test_routing_decision_present(self):
        result = build_connector_routing_rules([_C_JIRA])
        assert "How to route retrieval" in result

    def test_planner_example_uses_connector_ids(self):
        result = build_connector_routing_rules([_C_JIRA], call_format="planner")
        assert '"connector_ids"' in result
        assert "jira-cid-1" in result
        assert "retrieval.search_internal_knowledge" in result

    def test_orchestrator_example_uses_connector_ids(self):
        result = build_connector_routing_rules([_C_JIRA], call_format="orchestrator")
        assert "connector_ids" in result
        assert "jira-cid-1" in result
        assert "task_id" in result


class TestBuildConnectorRoutingRulesMultiConnector:
    """Multiple app connectors — all-or-specific branching."""

    def test_all_sources_example_present(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF], call_format="planner")
        assert "All sources" in result

    def test_specific_source_example_present(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF], call_format="planner")
        assert "Specific source" in result

    def test_all_connector_ids_in_output(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF], call_format="planner")
        assert "jira-cid-1" in result
        assert "conf-cid-2" in result

    def test_orchestrator_task_ids_named_by_type_key(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF], call_format="orchestrator")
        assert "retrieval_jira" in result
        assert "retrieval_confluence" in result

    def test_default_search_all_rule(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF])
        assert "Default when truly uncertain" in result

    def test_routing_decision_present(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF])
        assert "How to route retrieval" in result


class TestBuildConnectorRoutingRulesMixed:
    """KB collections + app connectors — the real-world mixed case."""

    def test_collection_ids_in_identity_block(self):
        result = build_connector_routing_rules([_C_JIRA], kb_sources=[_KB_WITH_IDS])
        assert "rg-id-aaa" in result
        assert "KB Collections" in result

    def test_connector_ids_in_identity_block(self):
        result = build_connector_routing_rules([_C_JIRA], kb_sources=[_KB_WITH_IDS])
        assert "jira-cid-1" in result
        assert "App Connectors" in result

    def test_all_sources_planner_example_has_both_kb_and_connector(self):
        result = build_connector_routing_rules(
            [_C_JIRA, _C_CONF], kb_sources=[_KB_WITH_IDS], call_format="planner"
        )
        assert "rg-id-aaa" in result      # KB call with collection_ids
        assert "jira-cid-1" in result     # Jira connector call
        assert "conf-cid-2" in result     # Confluence connector call

    def test_all_sources_orchestrator_example_has_kb_task_with_collection_ids(self):
        result = build_connector_routing_rules(
            [_C_JIRA], kb_sources=[_KB_WITH_IDS], call_format="orchestrator"
        )
        assert "retrieval_kb_" in result   # KB task
        assert "collection_ids" in result  # Uses collection_ids not connector_ids
        assert "retrieval_jira" in result  # Connector task also present

    def test_total_source_count_in_header(self):
        # 1 KB + 2 connectors = 3 total
        result = build_connector_routing_rules(
            [_C_JIRA, _C_CONF], kb_sources=[_KB_WITH_IDS]
        )
        assert "3 source" in result

    def test_parameter_rule_never_mix_message(self):
        result = build_connector_routing_rules([_C_JIRA], kb_sources=[_KB_WITH_IDS])
        assert "NEVER" in result or "never" in result.lower()

    def test_parameter_rule_distinguishes_both_params(self):
        result = build_connector_routing_rules([_C_JIRA], kb_sources=[_KB_WITH_IDS])
        assert "collection_ids" in result
        assert "connector_ids" in result
        assert "Parameter rules" in result

    def test_no_signal_map_keywords_in_output(self):
        """Mixed routing must not contain hard-coded resource-noun signals."""
        result = build_connector_routing_rules([_C_JIRA], kb_sources=[_KB_WITH_IDS])
        for term in ["story points", "epics", "DMs"]:
            assert term not in result


# ===========================================================================
# Generic routing language (no signal map)
# ===========================================================================

class TestGenericRoutingLanguage:
    """Verify routing text is generic (explicit name only, no keyword signals)."""

    def test_routing_uses_explicit_name_guidance(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF])
        assert "explicit" in result.lower() or "connector name" in result.lower() \
               or "Jira" in result or "Confluence" in result

    def test_routing_default_search_all_present(self):
        result = build_connector_routing_rules([_C_JIRA, _C_CONF])
        assert "Default when truly uncertain" in result

    def test_routing_no_keyword_signal_map_text(self):
        """The output must not contain hard-coded resource-noun signals."""
        result = build_connector_routing_rules([_C_JIRA, _C_CONF])
        # These were signal-map terms that should no longer appear
        for term in ["story points", "DMs", "direct messages"]:
            assert term not in result

    def test_single_connector_no_split_example(self):
        """With only 1 source there is no all-vs-specific split."""
        result = build_connector_routing_rules([_C_JIRA], call_format="planner")
        assert "Call format" in result


# ===========================================================================
# MCP Available Actions — per-server domain keys
# ===========================================================================


class TestSplitMcpDomainForActions:
    """``_split_mcp_domain_for_actions`` regroups flat ``mcp`` into ``mcp_exa``, etc."""

    def test_groups_into_per_server_domains(self):
        from app.modules.agents.capability_summary import _split_mcp_domain_for_actions

        state = {
            "agent_mcp_servers": [
                {
                    "type": "exa",
                    "tools": [{"name": "web_search_exa"}, {"name": "web_fetch_exa"}],
                },
                {"type": "jira", "tools": [{"name": "getJiraIssue"}]},
            ],
        }
        domains = {
            "mcp": [
                "mcp exa web search exa",
                "mcp exa web fetch exa",
                "mcp jira getJiraIssue",
            ],
        }
        _split_mcp_domain_for_actions(state, domains)

        assert "mcp" not in domains
        assert domains["mcp_exa"] == [
            "mcp exa web search exa",
            "mcp exa web fetch exa",
        ]
        assert domains["mcp_jira"] == ["mcp jira getJiraIssue"]

    def test_no_metadata_keeps_single_mcp_bucket_spaced(self):
        from app.modules.agents.capability_summary import _split_mcp_domain_for_actions

        state: dict = {"agent_mcp_servers": []}
        domains = {"mcp": ["mcp_foo_bar"]}
        _split_mcp_domain_for_actions(state, domains)
        assert domains == {"mcp": ["mcp foo bar"]}

    def test_unmatched_tools_remain_under_mcp(self):
        from app.modules.agents.capability_summary import _split_mcp_domain_for_actions

        state = {
            "agent_mcp_servers": [
                {"type": "exa", "tools": [{"name": "web_search_exa"}]},
            ],
        }
        domains = {"mcp": ["mcp exa web search exa", "mcp slack some tool"]}
        _split_mcp_domain_for_actions(state, domains)
        assert domains["mcp_exa"] == ["mcp exa web search exa"]
        assert domains["mcp"] == ["mcp slack some tool"]
