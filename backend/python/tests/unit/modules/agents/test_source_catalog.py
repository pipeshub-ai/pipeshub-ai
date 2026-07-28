"""Tests for `SourceCatalog` and `build_routing_guidance`.

Covers the plan's test requirements:
  - Rendering for kb-only / app-only / mixed / no-ID KB / duplicate app types
  - `list_files_param` correctness per kind
  - `live_only_apps` returns Slack when Slack has tools but no knowledge,
    and NOT when Slack is indexed (regression guard for hardcoded list)
  - `ids_actionable=False` emits no UUID anywhere (chat-route regression, Finding H)
  - `build_routing_guidance` basic behavior
"""

from __future__ import annotations

import re

import pytest

from app.modules.agents.context.source_catalog import (
    SourceCatalog,
    SourceKind,
)
from app.modules.agents.context.retrieval_routing import build_routing_guidance


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

KB_ID   = "aabbccdd-1111-1111-1111-aabbccdd0001"
CONF_ID = "aabbccdd-2222-2222-2222-aabbccdd0002"
JIRA_ID = "aabbccdd-3333-3333-3333-aabbccdd0003"
JIRA_ID_2 = "aabbccdd-5555-5555-5555-aabbccdd0005"
SLACK_ID = "aabbccdd-6666-6666-6666-aabbccdd0006"


def _make_kb_entry(label: str, cid: str = "") -> dict:
    return {"displayName": label, "type": "KB", "connectorId": cid}


def _make_app_entry(label: str, app_type: str, cid: str, filters: dict | None = None) -> dict:
    entry: dict = {"displayName": label, "type": app_type, "connectorId": cid}
    return entry


def _catalog_from_knowledge(entries: list[dict]) -> SourceCatalog:
    """Build a catalog directly from raw knowledge entries."""
    state: dict = {"agent_knowledge": entries}
    return SourceCatalog.from_state(state)


def _no_uuid_in(text: str) -> bool:
    """Returns True if the text contains no UUID-like tokens."""
    return not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.I)


# ---------------------------------------------------------------------------
# SourceCatalog.from_state
# ---------------------------------------------------------------------------

class TestFromState:

    def test_empty_knowledge_and_connectors_yields_empty(self) -> None:
        catalog = SourceCatalog.from_state({})
        assert catalog.is_empty()
        assert catalog.ids_actionable is False

    def test_agent_knowledge_yields_actionable(self) -> None:
        catalog = _catalog_from_knowledge([_make_kb_entry("KB", KB_ID)])
        assert catalog.ids_actionable is True

    def test_available_connectors_only_yields_not_actionable(self) -> None:
        state = {
            "agent_knowledge": [],
            "available_connectors": [{"type": "confluence"}],
        }
        catalog = SourceCatalog.from_state(state)
        assert not catalog.is_empty()
        assert catalog.ids_actionable is False

    def test_kb_entry_classified_as_kb(self) -> None:
        catalog = _catalog_from_knowledge([_make_kb_entry("My KB", KB_ID)])
        assert len(catalog.sources) == 1
        assert catalog.sources[0].kind == SourceKind.KB

    def test_app_entry_classified_as_app(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Confluence", "confluence", CONF_ID)])
        assert len(catalog.sources) == 1
        assert catalog.sources[0].kind == SourceKind.APP
        assert catalog.sources[0].app == "confluence"

    def test_mixed_kb_and_app(self) -> None:
        entries = [
            _make_kb_entry("Private KB", KB_ID),
            _make_app_entry("Jira", "jira", JIRA_ID),
        ]
        catalog = _catalog_from_knowledge(entries)
        assert len(catalog.kb_sources()) == 1
        assert len(catalog.app_sources()) == 1

    def test_app_without_connector_id_skipped(self) -> None:
        entries = [_make_app_entry("Broken", "jira", "")]  # empty connectorId
        catalog = _catalog_from_knowledge(entries)
        assert catalog.is_empty()

    def test_kb_without_id_included(self) -> None:
        """A KB with no id is still a valid source (omit connector_ids to search it)."""
        catalog = _catalog_from_knowledge([_make_kb_entry("Company Wiki", "")])
        assert not catalog.is_empty()
        assert catalog.sources[0].source_id == ""


# ---------------------------------------------------------------------------
# SourceCatalog.render — IDs appear exactly once
# ---------------------------------------------------------------------------

class TestRender:

    def test_empty_catalog_returns_empty_string(self) -> None:
        catalog = SourceCatalog.from_state({})
        assert catalog.render() == ""

    def test_kb_with_id_rendered_once(self) -> None:
        catalog = _catalog_from_knowledge([_make_kb_entry("Private KB", KB_ID)])
        rendered = catalog.render()
        assert rendered.count(KB_ID) == 1, f"Expected 1 occurrence, got {rendered.count(KB_ID)}"

    def test_app_id_rendered_once(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Confluence", "confluence", CONF_ID)])
        rendered = catalog.render()
        assert rendered.count(CONF_ID) == 1

    def test_multiple_sources_each_id_once(self) -> None:
        entries = [
            _make_kb_entry("KB", KB_ID),
            _make_app_entry("Confluence", "confluence", CONF_ID),
            _make_app_entry("Jira", "jira", JIRA_ID),
        ]
        catalog = _catalog_from_knowledge(entries)
        rendered = catalog.render()
        for cid in (KB_ID, CONF_ID, JIRA_ID):
            assert rendered.count(cid) == 1, f"{cid} appears {rendered.count(cid)} times"

    def test_kb_no_id_shows_omit_guidance(self) -> None:
        catalog = _catalog_from_knowledge([_make_kb_entry("Company Wiki", "")])
        rendered = catalog.render()
        assert "omit" in rendered.lower() or "connector_ids" in rendered

    def test_render_includes_list_files_param_note(self) -> None:
        """The source_ids parameter must be mentioned in the render output."""
        entries = [
            _make_kb_entry("KB", KB_ID),
            _make_app_entry("Confluence", "confluence", CONF_ID),
        ]
        catalog = _catalog_from_knowledge(entries)
        rendered = catalog.render()
        assert "source_ids" in rendered


# ---------------------------------------------------------------------------
# ids_actionable=False: no UUIDs on the chat route (Finding H regression)
# ---------------------------------------------------------------------------

class TestIdsNotActionable:

    def test_chat_route_emits_no_uuids(self) -> None:
        """When ids_actionable=False, render() must produce zero UUID-like tokens."""
        state = {
            "agent_knowledge": [],
            "available_connectors": [
                {"type": "confluence"},
                {"type": "jira"},
            ],
        }
        catalog = SourceCatalog.from_state(state)
        rendered = catalog.render()
        assert _no_uuid_in(rendered), (
            f"Chat-route render contains UUIDs: {re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}', rendered, re.I)}"
        )

    def test_chat_route_still_renders_type_names(self) -> None:
        state = {
            "agent_knowledge": [],
            "available_connectors": [{"type": "confluence"}, {"type": "jira"}],
        }
        catalog = SourceCatalog.from_state(state)
        rendered = catalog.render()
        assert "CONFLUENCE" in rendered or "confluence" in rendered


# ---------------------------------------------------------------------------
# list_files_param property
# ---------------------------------------------------------------------------

class TestListFilesParam:

    def test_kb_source_uses_record_group_ids(self) -> None:
        catalog = _catalog_from_knowledge([_make_kb_entry("KB", KB_ID)])
        kb = catalog.kb_sources()[0]
        assert kb.list_files_param == "record_group_ids"

    def test_app_source_uses_connector_ids(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Jira", "jira", JIRA_ID)])
        app = catalog.app_sources()[0]
        assert app.list_files_param == "connector_ids"


# ---------------------------------------------------------------------------
# duplicate_apps
# ---------------------------------------------------------------------------

class TestDuplicateApps:

    def test_no_duplicates(self) -> None:
        entries = [
            _make_app_entry("Confluence", "confluence", CONF_ID),
            _make_app_entry("Jira", "jira", JIRA_ID),
        ]
        catalog = _catalog_from_knowledge(entries)
        assert catalog.duplicate_apps() == ()

    def test_same_type_returns_type_key(self) -> None:
        entries = [
            _make_app_entry("Jira Prod", "jira", JIRA_ID),
            _make_app_entry("Jira Staging", "jira", JIRA_ID_2),
        ]
        catalog = _catalog_from_knowledge(entries)
        assert "jira" in catalog.duplicate_apps()


# ---------------------------------------------------------------------------
# live_only_apps
# ---------------------------------------------------------------------------

class TestLiveOnlyApps:

    def test_slack_live_only_when_no_slack_knowledge(self) -> None:
        """Slack is live-only when it has toolset tools but no indexed entry."""
        entries = [_make_app_entry("Jira", "jira", JIRA_ID)]  # only Jira indexed
        catalog = _catalog_from_knowledge(entries)
        # Slack has toolsets but no knowledge
        toolset_apps = frozenset(["slack", "jira"])
        live_only = catalog.live_only_apps(toolset_apps)
        assert "slack" in live_only

    def test_slack_not_live_only_when_indexed(self) -> None:
        """Slack is NOT live-only when it IS indexed as a knowledge source."""
        entries = [_make_app_entry("Slack", "slack", SLACK_ID)]
        catalog = _catalog_from_knowledge(entries)
        toolset_apps = frozenset(["slack"])
        live_only = catalog.live_only_apps(toolset_apps)
        assert "slack" not in live_only

    def test_empty_toolset_apps_returns_empty(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Jira", "jira", JIRA_ID)])
        assert catalog.live_only_apps(frozenset()) == ()


# ---------------------------------------------------------------------------
# build_routing_guidance
# ---------------------------------------------------------------------------

class TestCanonicalKnowledgeBlock:
    """Phase 7: Verify the canonical knowledge-system block emits the
    'ranked sample' wording unconditionally on the agent route (ids_actionable=True),
    regardless of whether knowledgegraph__fetch_record has been granted."""

    def test_ranked_sample_present_without_fetch_record_granted(self) -> None:
        """Turn 1: only search is granted — ranked sample must still appear."""
        catalog = _catalog_from_knowledge([_make_app_entry("Confluence", "confluence", CONF_ID)])
        rendered = catalog.render(tool_names=["knowledgegraph__search"])
        assert "ranked sample" in rendered.lower()

    def test_ranked_sample_present_after_fetch_record_granted(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Confluence", "confluence", CONF_ID)])
        rendered = catalog.render(tool_names=["knowledgegraph__search", "knowledgegraph__fetch_record"])
        assert "ranked sample" in rendered.lower()

    def test_search_described_as_content_tool(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Jira", "jira", JIRA_ID)])
        rendered = catalog.render()
        # The canonical block says search gives content
        assert "content" in rendered.lower()
        assert "metadata" in rendered.lower()

    def test_canonical_block_always_rendered(self) -> None:
        """The canonical knowledge-system block is rendered for all routes."""
        state = {
            "agent_knowledge": [],
            "available_connectors": [{"type": "confluence"}],
        }
        catalog = SourceCatalog.from_state(state)
        rendered = catalog.render()
        assert "knowledge system" in rendered.lower()

    def test_combining_them_section_present_on_agent_route(self) -> None:
        """The 'Combining them' paragraph must be in the canonical block."""
        catalog = _catalog_from_knowledge([_make_app_entry("Confluence", "confluence", CONF_ID)])
        rendered = catalog.render()
        assert "Combining them" in rendered


class TestBuildRoutingGuidance:
    """`build_routing_guidance` now states ONLY the duplicate-connector
    disambiguation note — parallel-per-source and naming-narrows-not-skips
    are each stated exactly once elsewhere now: the first in
    `retrieval.search_internal_knowledge`'s own schema, the second as the
    single generalized precedence rule in `finding_information`
    (`prompt_builder.py`)."""

    def test_empty_catalog_returns_empty(self) -> None:
        catalog = SourceCatalog.from_state({})
        assert build_routing_guidance(catalog) == ""

    def test_no_duplicates_returns_empty(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Jira", "jira", JIRA_ID)])
        assert build_routing_guidance(catalog) == ""

    def test_duplicate_app_note_included(self) -> None:
        entries = [
            _make_app_entry("Jira Prod", "jira", JIRA_ID),
            _make_app_entry("Jira Staging", "jira", JIRA_ID_2),
        ]
        catalog = _catalog_from_knowledge(entries)
        guidance = build_routing_guidance(catalog)
        assert "jira" in guidance.lower()
        assert "same type" in guidance.lower() or "multiple connectors" in guidance.lower()


class TestRenderEmbedsRoutingGuidance:
    """`render()` is the sole call site for `build_routing_guidance` now —
    no separate "## Retrieval Routing" section exists to keep in sync."""

    def test_render_includes_duplicate_note_when_duplicates_exist(self) -> None:
        entries = [
            _make_app_entry("Jira Prod", "jira", JIRA_ID),
            _make_app_entry("Jira Staging", "jira", JIRA_ID_2),
        ]
        catalog = _catalog_from_knowledge(entries)
        rendered = catalog.render()
        assert "Multiple connectors of the same type" in rendered

    def test_render_omits_note_without_duplicates(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Jira", "jira", JIRA_ID)])
        rendered = catalog.render()
        assert "Multiple connectors of the same type" not in rendered
