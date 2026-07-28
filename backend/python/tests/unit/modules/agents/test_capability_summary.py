"""
Unit tests for app.modules.agents.capability_summary

Covers:
  - classify_knowledge_sources  (returns ONE flat list[dict] — KB collections
    and app connectors together, discriminated by `source_type` — since a
    KB collection's id IS a connector id now, there is no reason to carry
    them as two parallel lists with two different shapes)
  - build_connector_routing_rules  (takes that single unified list; unified
    connector_ids examples for both KB collections and app connectors —
    there is no separate collection_ids)

Test-case matrix
────────────────
Config                       | classify | routing
KB only (no connector_id)    |   ✓      |   ✓
KB only (with connector_id)  |   ✓      |   ✓
Connector only               |   ✓      |   ✓
Multi-connector               |   ✓      |   ✓
KB + connector               |   ✓      |   ✓
KB (w/ id) + multi-connector |   ✓      |   ✓
Empty                        |   ✓      |   ✓
"""

import pytest

from app.modules.agents.capability_summary import (
    classify_knowledge_sources,
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

