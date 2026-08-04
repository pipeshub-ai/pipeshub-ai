"""Unit tests for the lookup_record tool — generic routing matrix and scoping."""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _lookup_result_summary,
)
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType, Status, TicketRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_id_for(state: dict, full_id: str) -> str:
    """The short "R<n>" label `RecordIdShortener` assigned `full_id` in a
    prior lookup_record()/navigate() call on this `state` — TEMPORARY
    token-savings experiment (see `RecordIdShortener` in
    `utils/chat_helpers.py`). Idempotent — returns the existing mapping."""
    shortener = state.get("record_id_shortener")
    assert shortener is not None, "record_id_shortener not set on state — call a KG tool first"
    return shortener.get_or_create_short_id(full_id)


def _gp_with_jira(jira_rec=None, confluence_rec=None):
    """Graph provider mock with JIRA and CONFLUENCE connectors."""
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
    gp.get_record_by_issue_key = AsyncMock(return_value=jira_rec)
    gp.get_record_by_external_id = AsyncMock(return_value=confluence_rec)
    gp.get_record_by_weburl = AsyncMock(return_value=None)
    gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
    gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})
    return gp


def _make_state(**overrides):
    """ChatState-like dict with Jira + Confluence knowledge config."""
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "graph_provider": _gp_with_jira(),
        "logger": MagicMock(),
        "agent_knowledge": [
            {"connectorId": "conn-jira", "type": "JIRA", "name": "Jira Cloud"},
            {"connectorId": "conn-conf", "type": "CONFLUENCE", "name": "Confluence Connector"},
        ],
        # Opt into the RecordIdShortener (disabled by default — see
        # `ChatQuery.enableRecordIdShortening`) so this suite continues to
        # exercise the shortened-id path it was written against.
        "enable_record_id_shortening": True,
    }
    state.update(overrides)
    return state


# Aliases for record/connector labels this test file historically used that
# are not real `RecordType`/`Connectors` enum values — kept so existing test
# call sites below did not all need editing when `_mock_record` switched
# from a camelCase `MagicMock` (which happily accepted anything) to a real
# `Record`, whose fields are validated.
_RECORD_TYPE_ALIASES = {"DOCUMENT": "FILE", "EMAIL": "MAIL"}
_CONNECTOR_ALIASES = {"GOOGLEDRIVE": "DRIVE"}


def _mock_record(id: str, name: str, record_type: str = "TICKET", connector: str = "JIRA") -> Record:
    """Build a real `Record` (or `TicketRecord` for TICKET) instance.

    Provider lookups (`get_record_by_issue_key`/`get_record_by_external_id`/
    `get_record_by_weburl`) return this Pydantic model, not a
    camelCase-attributed `MagicMock` — the bug `_record_to_match` had was
    reading `rec.recordName`/`rec.recordType`/etc., which only a `MagicMock`
    tolerates. A real instance is the regression guard: it fails loudly if
    the resolver ever goes back to reading nonexistent camelCase attributes.
    """
    rt_value = _RECORD_TYPE_ALIASES.get(record_type, record_type)
    conn_value = _CONNECTOR_ALIASES.get(connector, connector)
    common = dict(
        id=id,
        record_name=name,
        record_type=RecordType(rt_value),
        external_record_id=f"EXT-{id}",
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors(conn_value),
        connector_id=f"{connector.lower()}-conn",
        weburl=f"https://example.com/items/{id}",
        indexing_status="COMPLETED",
    )
    if rt_value == RecordType.TICKET.value:
        return TicketRecord(**common, status=Status.OPEN)
    return Record(**common)


def _access_node(id: str, name: str, record_type: str = "TICKET"):
    return {"id": id, "name": name, "nodeType": "record", "subType": record_type, "connector": "JIRA"}


# ---------------------------------------------------------------------------
# Basic error paths
# ---------------------------------------------------------------------------


class TestLookupErrorPaths:
    @pytest.mark.asyncio
    async def test_no_identifiers_error(self):
        tool = KnowledgeGraph(state=_make_state())
        success, text = await tool.lookup_record(identifiers=[])
        assert not success

    @pytest.mark.asyncio
    async def test_no_state_error(self):
        tool = KnowledgeGraph(state=None)
        success, text = await tool.lookup_record(identifiers=["PA-1"])
        assert not success

    @pytest.mark.asyncio
    async def test_no_graph_provider_error(self):
        state = _make_state(graph_provider=None)
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1"])
        assert not success

    @pytest.mark.asyncio
    async def test_single_string_accepted(self):
        """identifiers= may be a bare string from some LLMs."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers="PA-1787")
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# RC1 fix — chatbot mode: empty agent scope resolves via catalog
# ---------------------------------------------------------------------------


class TestChatbotModeScoping:
    @pytest.mark.asyncio
    async def test_chatbot_mode_empty_scope_uses_catalog(self):
        """Regression: lookup_record must NOT error when apps/kb/agent_knowledge are empty
        but has_knowledge=True (chat mode)."""
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={
            "apps": [
                {"id": "conn-jira", "name": "Jira Cloud", "type": "JIRA"},
            ]
        })
        rec = _mock_record("r1", "PA-1787 issue")
        gp.get_record_by_issue_key = AsyncMock(return_value=rec)
        gp.get_record_by_weburl = AsyncMock(return_value=None)
        gp.get_record_by_external_id = AsyncMock(return_value=None)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1787 issue"))

        state = {
            "org_id": "org-1",
            "user_id": "user-1",
            "graph_provider": gp,
            "has_knowledge": True,
            # No apps, kb, agent_knowledge — chatbot mode
        }
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1787"])

        assert success
        assert "r1" in text

    @pytest.mark.asyncio
    async def test_chatbot_mode_empty_catalog_returns_not_found(self):
        """Empty catalog → not found (not an error about missing config)."""
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})

        state = {
            "org_id": "org-1",
            "user_id": "user-1",
            "graph_provider": gp,
            "has_knowledge": True,
        }
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1787"])
        assert not success


# ---------------------------------------------------------------------------
# RC2 fix — issue key routes to JIRA connectors only, not Confluence
# ---------------------------------------------------------------------------


class TestIssueKeyRoutingToJiraOnly:
    @pytest.mark.asyncio
    async def test_jira_key_never_queries_confluence(self):
        """Regression: PA-1143 must hit get_record_by_issue_key on JIRA connectors
        only — NOT get_record_by_external_id on Confluence."""
        state = _make_state()
        gp = state["graph_provider"]
        rec = _mock_record("r1", "PA-1143 Bug")
        gp.get_record_by_issue_key = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1143 Bug"))
        gp.get_record_by_external_id = AsyncMock(return_value=None)

        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1143"])

        assert success
        # issue_key must have been called with the Jira connector, not Confluence
        issue_key_calls = gp.get_record_by_issue_key.call_args_list
        assert any("conn-jira" in str(c) for c in issue_key_calls), (
            "get_record_by_issue_key should be called with Jira connector"
        )
        # Confluence external-id lookup should NOT have been called for a Jira key
        ext_id_calls = gp.get_record_by_external_id.call_args_list
        conf_calls = [c for c in ext_id_calls if "conn-conf" in str(c)]
        assert not conf_calls, (
            "get_record_by_external_id should not be called with Confluence connector for a Jira key"
        )

    @pytest.mark.asyncio
    async def test_jira_key_not_found_shows_searched_connector(self):
        """Miss output must name which connectors were searched.

        `success=True` here (not False): zero accessible results is a
        legitimate lookup outcome, not a tool failure — see the
        `lookup_record` success-flag fix in knowledge_graph.py.
        """
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_record_by_issue_key = AsyncMock(return_value=None)
        gp.get_record_by_weburl = AsyncMock(return_value=None)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-9999"])

        assert success
        # Text must identify what was searched
        assert "Not found" in text
        assert "searched" in text.lower() or "JIRA" in text


# ---------------------------------------------------------------------------
# Confluence page id routing
# ---------------------------------------------------------------------------


class TestConfluencePageIdRouting:
    @pytest.mark.asyncio
    async def test_confluence_url_routes_to_external_id(self):
        state = _make_state()
        gp = state["graph_provider"]
        rec = _mock_record("cp1", "Release Notes", record_type="CONFLUENCE_PAGE", connector="CONFLUENCE")
        gp.get_record_by_external_id = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "cp1", "name": "Release Notes",
            "nodeType": "record", "subType": "CONFLUENCE_PAGE", "connector": "CONFLUENCE",
        })
        gp.get_record_by_issue_key = AsyncMock(return_value=None)

        url = "https://example.atlassian.net/wiki/spaces/ENG/pages/450625553/Release+Notes"
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=[url])

        assert success
        assert "cp1" in text
        # external_id lookup should have been called with the page id
        ext_calls = gp.get_record_by_external_id.call_args_list
        assert any("450625553" in str(c) for c in ext_calls)

    @pytest.mark.asyncio
    async def test_confluence_page_id_with_connector_hint(self):
        """Bare numeric with connector_name=CONFLUENCE → external_id on Confluence only."""
        state = _make_state()
        gp = state["graph_provider"]
        rec = _mock_record("cp2", "Config Guide", record_type="CONFLUENCE_PAGE", connector="CONFLUENCE")
        gp.get_record_by_external_id = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "cp2", "name": "Config Guide",
            "nodeType": "record", "subType": "CONFLUENCE_PAGE",
        })

        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(
            identifiers=["450625553"], connector_name="CONFLUENCE"
        )

        assert success
        assert "cp2" in text


# ---------------------------------------------------------------------------
# Google Drive routing
# ---------------------------------------------------------------------------


class TestGoogleDriveRouting:
    @pytest.mark.asyncio
    async def test_drive_url_routes_to_external_id(self):
        state = _make_state(agent_knowledge=[
            {"connectorId": "conn-drive", "type": "DRIVE", "name": "Google Drive"},
        ])
        gp = state["graph_provider"]
        rec = _mock_record("doc1", "Q4 Report", record_type="DOCUMENT", connector="DRIVE")
        gp.get_record_by_external_id = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "doc1", "name": "Q4 Report", "nodeType": "record", "subType": "DOCUMENT",
        })

        url = "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=[url])

        assert success
        ext_calls = gp.get_record_by_external_id.call_args_list
        assert any("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" in str(c) for c in ext_calls)

    @pytest.mark.asyncio
    async def test_bare_drive_file_id_routes_to_drive_type_first(self):
        """Bare Drive-id (25+ base64url chars) → try Drive-type connectors first."""
        state = _make_state(agent_knowledge=[
            {"connectorId": "conn-drive", "type": "GOOGLEDRIVE", "name": "Google Drive"},
            {"connectorId": "conn-jira", "type": "JIRA", "name": "Jira"},
        ])
        gp = state["graph_provider"]
        rec = _mock_record("doc2", "Spec Sheet", record_type="DOCUMENT", connector="GOOGLEDRIVE")
        gp.get_record_by_external_id = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "doc2", "name": "Spec Sheet", "nodeType": "record", "subType": "DOCUMENT",
        })
        gp.get_record_by_issue_key = AsyncMock(return_value=None)

        bare_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs"  # 33 chars, Drive-id shape
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=[bare_id])

        assert success
        ext_calls = gp.get_record_by_external_id.call_args_list
        # First call should be to the Drive-type connector
        assert any("conn-drive" in str(c) for c in ext_calls)


# ---------------------------------------------------------------------------
# Gmail / unknown URL — weburl-only fallback
# ---------------------------------------------------------------------------


class TestGmailUrlWebUrlFallback:
    @pytest.mark.asyncio
    async def test_gmail_url_falls_back_to_weburl(self):
        state = _make_state(agent_knowledge=[
            {"connectorId": "conn-gmail", "type": "GMAIL", "name": "Gmail"},
        ])
        gp = state["graph_provider"]
        rec = _mock_record("email1", "Q4 Report email", record_type="EMAIL", connector="GMAIL")
        gp.get_record_by_weburl = AsyncMock(return_value=rec)
        gp.get_record_by_external_id = AsyncMock(return_value=None)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "email1", "name": "Q4 Report email", "nodeType": "record", "subType": "EMAIL",
        })

        url = "https://mail.google.com/mail/u/0/#inbox/abc123def456"
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=[url])

        assert success
        gp.get_record_by_weburl.assert_awaited()

    @pytest.mark.asyncio
    async def test_url_canonical_miss_fires_weburl_fallback(self):
        """A URL whose canonical strategy (external_id) misses must still try webUrl."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_record_by_external_id = AsyncMock(return_value=None)
        gp.get_record_by_issue_key = AsyncMock(return_value=None)

        rec = _mock_record("p1", "Some Page", record_type="CONFLUENCE_PAGE", connector="CONFLUENCE")
        gp.get_record_by_weburl = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "p1", "name": "Some Page", "nodeType": "record", "subType": "CONFLUENCE_PAGE",
        })

        # A Confluence URL that will hit external_id first (miss), then weburl fallback
        url = "https://example.atlassian.net/wiki/x/shortlink"
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=[url])

        assert success
        gp.get_record_by_weburl.assert_awaited()


# ---------------------------------------------------------------------------
# webUrl normalization
# ---------------------------------------------------------------------------


class TestWebUrlNormalization:
    @pytest.mark.asyncio
    async def test_normalized_url_tried_first(self):
        """Normalized URL (tracking params stripped) is queried before raw."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_record_by_external_id = AsyncMock(return_value=None)
        gp.get_record_by_issue_key = AsyncMock(return_value=None)

        rec = _mock_record("p1", "Clean Page", record_type="CONFLUENCE_PAGE")
        call_order = []

        async def mock_weburl(url, org_id=None):
            call_order.append(url)
            # Return record only for the clean URL (no tracking params)
            if "atlOrigin" not in url and "utm_source" not in url:
                return rec
            return None

        gp.get_record_by_weburl = AsyncMock(side_effect=mock_weburl)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "p1", "name": "Clean Page", "nodeType": "record", "subType": "CONFLUENCE_PAGE",
        })

        dirty_url = "https://example.atlassian.net/wiki/spaces/ENG/pages/123?atlOrigin=xxx&utm_source=slack"
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=[dirty_url])

        assert success
        # First call must be to the normalized URL (no tracking params)
        assert call_order, "get_record_by_weburl should have been called"
        assert "atlOrigin" not in call_order[0]
        assert "utm_source" not in call_order[0]


# ---------------------------------------------------------------------------
# Permission non-leak — denied == not-found text
# ---------------------------------------------------------------------------


class TestPermissionNonLeak:
    @pytest.mark.asyncio
    async def test_denied_same_as_not_found(self):
        rec = _mock_record("x1", "Secret")
        # Denied state
        state_denied = _make_state()
        gp_denied = state_denied["graph_provider"]
        gp_denied.get_record_by_issue_key = AsyncMock(return_value=rec)
        gp_denied.get_knowledge_hub_node_access = AsyncMock(return_value=None)  # denied
        gp_denied.get_record_by_weburl = AsyncMock(return_value=None)

        # Not-found state
        state_missing = _make_state()
        gp_missing = state_missing["graph_provider"]
        gp_missing.get_record_by_issue_key = AsyncMock(return_value=None)
        gp_missing.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        gp_missing.get_record_by_weburl = AsyncMock(return_value=None)

        _, denied_text = await KnowledgeGraph(state=state_denied).lookup_record(identifiers=["PA-1"])
        _, missing_text = await KnowledgeGraph(state=state_missing).lookup_record(identifiers=["PA-1"])

        assert "Not found" in denied_text or "no access" in denied_text.lower()
        assert "Not found" in missing_text or "no access" in missing_text.lower()


# ---------------------------------------------------------------------------
# Ambiguity and batch
# ---------------------------------------------------------------------------


class TestLookupBatch:
    @pytest.mark.asyncio
    async def test_batch_of_identifiers(self):
        """Multiple identifiers resolved in one call."""
        state = _make_state()
        gp = state["graph_provider"]
        records = {
            "PA-1001": _mock_record("r1", "PA-1001"),
            "PA-1002": _mock_record("r2", "PA-1002"),
        }

        async def mock_issue_key(connector_id, key):
            return records.get(key)

        gp.get_record_by_issue_key = AsyncMock(side_effect=mock_issue_key)
        gp.get_record_by_weburl = AsyncMock(return_value=None)
        gp.get_knowledge_hub_node_access = AsyncMock(side_effect=lambda **kw: {
            "id": kw["node_id"], "name": "rec", "nodeType": "record", "subType": "TICKET",
        } if kw["node_id"] in ("r1", "r2") else None)

        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1001", "PA-1002"])

        assert success
        assert "r1" in text
        assert "r2" in text


# ---------------------------------------------------------------------------
# Enriched lookup output (context_block, External ID, Next: hint)
# ---------------------------------------------------------------------------


class TestEnrichedLookupOutput:
    @pytest.mark.asyncio
    async def test_ticket_record_round_trips_with_context_block(self):
        """A TicketRecord's `to_llm_context()` block — Name, External ID,
        Web URL, and `* Status` — must reach the tool output verbatim.
        Regression guard for the snake_case/camelCase mapping bug in
        `_record_to_match` (resolver.py)."""
        state = _make_state()
        gp = state["graph_provider"]
        rec = _mock_record("r1", "PA-1787 issue")  # TicketRecord w/ Status.OPEN
        gp.get_record_by_issue_key = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1787 issue"))

        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1787"])

        assert success
        assert "PA-1787 issue" in text
        assert "External ID" in text
        assert "EXT-r1" in text
        assert "Web URL" in text
        assert "* Status" in text

    @pytest.mark.asyncio
    async def test_next_hint_present_after_match(self):
        """Same self-teaching `Next:` pattern navigate() already uses."""
        state = _make_state()
        gp = state["graph_provider"]
        rec = _mock_record("r1", "PA-1787 issue")
        gp.get_record_by_issue_key = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1787 issue"))

        tool = KnowledgeGraph(state=state)
        _, text = await tool.lookup_record(identifiers=["PA-1787"])

        short_id = _short_id_for(state, "r1")
        assert f'Next: navigate(node_id="{short_id}") to list children and linked records' in text

    @pytest.mark.asyncio
    async def test_next_hint_also_offers_the_read_step(self):
        """Resolving an identifier is only useful if the model can then act on
        it — one branch lists what is under the record, the other reads it."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_record_by_issue_key = AsyncMock(return_value=_mock_record("r1", "PA-1787 issue"))
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1787 issue"))

        tool = KnowledgeGraph(state=state)
        _, text = await tool.lookup_record(identifiers=["PA-1787"])

        short_id = _short_id_for(state, "r1")
        assert f'knowledgegraph__fetch_record(record_ids=["{short_id}"]) to read the content' in text

    @pytest.mark.asyncio
    async def test_matched_ids_are_remembered_for_the_fetch_tool(self):
        """`hooks/citations.py` registers `dynamic_fetch_full_record` off this
        signal, so without it the hint above would name an uncallable tool."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_record_by_issue_key = AsyncMock(return_value=_mock_record("r1", "PA-1787 issue"))
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1787 issue"))

        await KnowledgeGraph(state=state).lookup_record(identifiers=["PA-1787"])

        assert state["known_record_ids"] == {"r1"}

    @pytest.mark.asyncio
    async def test_nothing_remembered_when_nothing_resolved(self):
        state = _make_state()
        state["graph_provider"].get_knowledge_hub_node_access = AsyncMock(return_value=None)

        await KnowledgeGraph(state=state).lookup_record(identifiers=["PA-9999"])

        assert not state.get("known_record_ids")

    @pytest.mark.asyncio
    async def test_denied_and_missing_byte_identical(self):
        """Enriching found output must not leak into the denied/missing
        path — those two must stay byte-for-byte identical (no info leak).

        Uses a generic (non-issue-key) URL so both paths walk the exact same
        single-strategy `RefKind.WEB_URL` table entry — an issue-key input
        would make the "searched" connector list itself differ between a
        hit-then-denied run (short-circuits after the first strategy) and a
        miss (exhausts every strategy), which is a pre-existing, unrelated
        property of `_fetch_candidates`'s early-break-on-match loop, not an
        info leak.
        """
        rec = _mock_record("x1", "Secret", record_type="CONFLUENCE_PAGE", connector="CONFLUENCE")
        url = "https://example.com/secret-doc"

        state_denied = _make_state()
        gp_denied = state_denied["graph_provider"]
        gp_denied.get_record_by_weburl = AsyncMock(return_value=rec)
        gp_denied.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        state_missing = _make_state()
        gp_missing = state_missing["graph_provider"]
        gp_missing.get_record_by_weburl = AsyncMock(return_value=None)
        gp_missing.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        _, denied_text = await KnowledgeGraph(state=state_denied).lookup_record(identifiers=[url])
        _, missing_text = await KnowledgeGraph(state=state_missing).lookup_record(identifiers=[url])

        assert denied_text == missing_text


# ---------------------------------------------------------------------------
# _lookup_result_summary — ordering, empty-name guard, not-found parsing
# ---------------------------------------------------------------------------


class _StubToolResult:
    """Minimal stand-in for `ToolResult` — only `.content`/`.is_error` are
    read by `_lookup_result_summary`."""

    def __init__(self, content: str, is_error: bool = False) -> None:
        self.content = content
        self.is_error = is_error


class TestLookupResultSummary:
    def test_batch_found_and_missing_summarises_as_found(self):
        """Three hits plus one miss must summarise as found, not as
        "Not found or no access" — the check-ordering bug."""
        content = (
            "Record ID: r1\nName: PA-1001\n\n"
            "Record ID: r2\nName: PA-1002\n\n"
            "Record ID: r3\nName: PA-1003\n\n"
            "Not found (or no access): PA-9999  [searched: JIRA]"
        )
        summary = _lookup_result_summary({}, _StubToolResult(content))
        assert summary is not None
        assert "not found" not in summary.lower()
        assert "PA-1001" in summary

    def test_empty_names_never_produce_bare_separator(self):
        """A match whose context_block never rendered a `Name :` line (or a
        dict-shaped provider result) must not join into a bare `;`."""
        content = "Record ID: r1\n\nRecord ID: r2\n"
        summary = _lookup_result_summary({}, _StubToolResult(content))
        assert summary is not None
        assert summary.strip(" ;") != ""
        assert "Found 2 records" == summary

    def test_not_found_summary(self):
        content = "Not found (or no access): PA-9999  [searched: JIRA]"
        summary = _lookup_result_summary({}, _StubToolResult(content))
        assert summary == "Not found or no access"

    def test_no_content_returns_no_results(self):
        summary = _lookup_result_summary({}, _StubToolResult(""))
        assert summary == "No results"


# ---------------------------------------------------------------------------
# Coverage: edge paths in lookup_record
# ---------------------------------------------------------------------------


class TestLookupEdgePaths:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_error(self):
        """Line 594: _get_user_key returns None."""
        state = _make_state()
        state["graph_provider"].get_user_by_user_id = AsyncMock(return_value=None)
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1"])
        assert not success
        assert "User not found" in text

    @pytest.mark.asyncio
    async def test_empty_catalog_no_knowledge_returns_not_configured(self):
        """Line 606: catalog empty + has_knowledge is falsy."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})
        state["agent_knowledge"] = []
        state.pop("has_knowledge", None)
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["PA-1"])
        assert not success
        assert "No knowledge sources configured" in text

    @pytest.mark.asyncio
    async def test_resolve_many_exception_returns_not_found(self):
        """Lines 632-634: resolver.resolve_many raises."""
        state = _make_state()
        gp = state["graph_provider"]
        rec = _mock_record("r1", "PA-1787 issue")
        gp.get_record_by_issue_key = AsyncMock(return_value=rec)
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=_access_node("r1", "PA-1787"))

        tool = KnowledgeGraph(state=state)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.agents.actions.knowledge_graph.resolver.RecordResolver.resolve_many",
                AsyncMock(side_effect=RuntimeError("db timeout")),
            )
            success, text = await tool.lookup_record(identifiers=["PA-1787"])
        assert not success
        assert "Not found" in text or "no access" in text.lower()
