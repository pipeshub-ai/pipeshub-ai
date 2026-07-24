"""Unit tests for ``app.utils.lookup_record`` — the ``lookup_record`` tool."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.config.constants.arangodb import ProgressStatus
from app.utils.lookup_record import (
    LookupRecordArgs,
    _describe_lookup_record,
    _dedupe_records,
    create_lookup_record_tool,
)
from app.utils.record_tool_helpers import NOT_FOUND_ERROR


def _record(record_id: str, **extra) -> SimpleNamespace:
    return SimpleNamespace(id=record_id, **extra)


def _make_provider(**overrides) -> MagicMock:
    provider = MagicMock()
    provider.get_org_apps = AsyncMock(return_value=[{"_key": "conn-jira", "type": "JIRA", "name": "Jira"}])
    provider.get_record_by_weburl = AsyncMock(return_value=None)
    provider.get_record_by_issue_key = AsyncMock(return_value=None)
    provider.get_record_by_external_id = AsyncMock(return_value=None)
    provider.find_slack_burst_record_by_ts = AsyncMock(return_value=None)
    provider.check_record_access_with_details = AsyncMock(return_value={"allowed": True})
    provider.get_document = AsyncMock(return_value=None)
    for key, value in overrides.items():
        setattr(provider, key, value)
    return provider


def _doc(record_id: str, **extra) -> dict:
    base = {
        "id": record_id,
        "_key": record_id,
        "recordName": "Payment outage",
        "recordType": "TICKET",
        "connectorName": "Jira",
        "webUrl": "https://pipeshub.atlassian.net/browse/PA-1787",
        "indexingStatus": ProgressStatus.COMPLETED.value,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# LookupRecordArgs
# ---------------------------------------------------------------------------


class TestLookupRecordArgs:
    def test_requires_identifiers(self) -> None:
        with pytest.raises(ValidationError):
            LookupRecordArgs()

    def test_defaults(self) -> None:
        args = LookupRecordArgs(identifiers=["PA-1787"])
        assert args.connector_name is None
        assert "Resolving" in args.reason

    def test_accepts_multiple(self) -> None:
        args = LookupRecordArgs(identifiers=["PA-1", "PA-2", "PA-3"])
        assert len(args.identifiers) == 3


# ---------------------------------------------------------------------------
# _describe_lookup_record (SSE tool_call description)
# ---------------------------------------------------------------------------


class TestDescribeLookupRecord:
    def test_describes_single_identifier(self) -> None:
        desc = _describe_lookup_record({"identifiers": ["PA-1787"]})
        assert "PA-1787" in desc

    def test_describes_with_connector_hint(self) -> None:
        desc = _describe_lookup_record({"identifiers": ["PA-1787"], "connector_name": "JIRA"})
        assert "JIRA" in desc

    def test_empty_identifiers_generic_message(self) -> None:
        assert "records" in _describe_lookup_record({"identifiers": []}).lower()

    def test_long_identifier_truncated(self) -> None:
        long_url = "https://example.com/" + "x" * 100
        desc = _describe_lookup_record({"identifiers": [long_url]})
        assert len(desc) < len(long_url) + 30

    def test_multiple_identifiers_listed(self) -> None:
        desc = _describe_lookup_record({"identifiers": ["PA-1", "PA-2", "PA-3"]})
        assert "PA-1" in desc and "PA-2" in desc


# ---------------------------------------------------------------------------
# _dedupe_records
# ---------------------------------------------------------------------------


class TestDedupeRecords:
    def test_removes_duplicates_by_id(self) -> None:
        records = [_record("r1"), _record("r1"), _record("r2")]
        result = _dedupe_records(records)
        assert [r.id for r in result] == ["r1", "r2"]

    def test_skips_records_without_id(self) -> None:
        records = [_record("r1"), SimpleNamespace()]
        result = _dedupe_records(records)
        assert len(result) == 1

    def test_empty_list(self) -> None:
        assert _dedupe_records([]) == []


# ---------------------------------------------------------------------------
# create_lookup_record_tool — end-to-end resolution scenarios
# ---------------------------------------------------------------------------


class TestLookupRecordToolAvailability:
    @pytest.mark.asyncio
    async def test_unavailable_without_graph_provider(self) -> None:
        tool = create_lookup_record_tool(graph_provider=None, org_id="org-1", user_id="user-1")
        result = await tool.ainvoke({"identifiers": ["PA-1787"]})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_unavailable_without_org_id(self) -> None:
        provider = _make_provider()
        tool = create_lookup_record_tool(graph_provider=provider, org_id=None, user_id="user-1")
        result = await tool.ainvoke({"identifiers": ["PA-1787"]})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_empty_identifier_returns_error(self) -> None:
        provider = _make_provider()
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
        result = await tool.ainvoke({"identifiers": ["   "]})
        assert result["ok"] is False


class TestLookupRecordToolResolution:
    @pytest.mark.asyncio
    async def test_jira_url_resolves_via_issue_key_canonical_ref(self) -> None:
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1")),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["https://pipeshub.atlassian.net/browse/PA-1787"]})

        assert result["ok"] is True
        assert result["record_info"]["name"] == "Payment outage"
        assert "navigate(node_id=" in result["content"][0]["text"]
        assert "fetch_full_record(record_ids=" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_bare_issue_key_resolves(self) -> None:
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1")),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["PA-1787"]})

        assert result["ok"] is True
        provider.get_record_by_issue_key.assert_awaited()

    @pytest.mark.asyncio
    async def test_bare_external_id_resolves_over_org_connectors(self) -> None:
        provider = _make_provider(
            get_record_by_external_id=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1")),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["450625553"]})

        assert result["ok"] is True
        provider.get_record_by_external_id.assert_awaited()

    @pytest.mark.asyncio
    async def test_url_falls_back_to_weburl_when_no_canonical_extractor(self) -> None:
        """A Confluence short link has no canonical extractor -> falls back to
        get_record_by_weburl (raw, then normalized)."""
        provider = _make_provider(
            get_record_by_weburl=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1")),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["https://pipeshub.atlassian.net/wiki/x/AbCdEf"]})

        assert result["ok"] is True
        provider.get_record_by_weburl.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_candidates_returns_not_found(self) -> None:
        provider = _make_provider()
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["999999999"]})

        assert result["ok"] is False
        assert result["error"] == NOT_FOUND_ERROR

    @pytest.mark.asyncio
    async def test_permission_denied_looks_identical_to_not_found(self) -> None:
        """A record that resolves but is denied by check_record_access_with_details
        must return the exact same error as no candidates at all."""
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            check_record_access_with_details=AsyncMock(return_value=None),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["PA-1787"]})

        assert result["ok"] is False
        assert result["error"] == NOT_FOUND_ERROR

    @pytest.mark.asyncio
    async def test_multiple_accessible_matches_returns_candidate_list(self) -> None:
        """Numeric IDs can collide across connectors (two Jira instances) — never
        silently pick one."""
        provider = _make_provider(
            get_org_apps=AsyncMock(return_value=[
                {"_key": "conn-jira-1", "type": "JIRA", "name": "Jira 1"},
                {"_key": "conn-jira-2", "type": "JIRA", "name": "Jira 2"},
            ]),
            get_record_by_external_id=AsyncMock(side_effect=[_record("r1"), _record("r2")]),
            get_document=AsyncMock(side_effect=[_doc("r1"), _doc("r2", recordName="Other doc")]),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["450625553"]})

        assert result["ok"] is True
        assert result["multiple_matches"] is True
        assert len(result["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_incomplete_indexing_status_shows_navigate_hint_not_content(self) -> None:
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1", indexingStatus="IN_PROGRESS")),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["PA-1787"]})

        assert result["ok"] is True
        assert result["result_type"] == "content"
        assert "not yet fully indexed" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_hidden_weburl_is_not_surfaced(self) -> None:
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1", hideWeburl=True)),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["PA-1787"]})

        assert result["record_info"]["webUrl"] is None

    @pytest.mark.asyncio
    async def test_completed_record_with_blob_store_loads_content(self) -> None:
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1", virtualRecordId="vrid-1")),
        )
        blob_store = MagicMock()
        vrid_map: dict = {}

        async def _fake_get_record(vrid, vmap, *_args, **_kwargs) -> None:
            vmap[vrid] = {"content": "full text", "id": "r1"}

        import app.utils.lookup_record as lookup_record_module
        original_get_record = lookup_record_module.get_record
        lookup_record_module.get_record = AsyncMock(side_effect=_fake_get_record)
        try:
            tool = create_lookup_record_tool(
                graph_provider=provider,
                org_id="org-1",
                user_id="user-1",
                blob_store=blob_store,
                virtual_record_id_to_result=vrid_map,
            )
            result = await tool.ainvoke({"identifiers": ["PA-1787"]})
        finally:
            lookup_record_module.get_record = original_get_record

        assert result["ok"] is True
        assert result["result_type"] == "records"
        assert result["records"][0]["virtual_record_id"] == "vrid-1"

    @pytest.mark.asyncio
    async def test_node_ref_mapper_reused_across_calls_within_conversation(self) -> None:
        from app.utils.record_tool_helpers import NodeRefMapper

        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(return_value=_record("r1")),
            get_document=AsyncMock(return_value=_doc("r1")),
        )
        mapper = NodeRefMapper()
        tool = create_lookup_record_tool(
            graph_provider=provider, org_id="org-1", user_id="user-1", node_ref_mapper=mapper,
        )

        result = await tool.ainvoke({"identifiers": ["PA-1787"]})

        assert result["record_info"]["ref"] == "n1"
        assert mapper.resolve("n1") == "r1"


class TestLookupRecordBatch:
    @pytest.mark.asyncio
    async def test_batch_resolves_multiple_identifiers_concurrently(self) -> None:
        async def _issue_key_side_effect(connector_id, key):
            return _record(f"r-{key}")

        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(side_effect=_issue_key_side_effect),
            get_document=AsyncMock(side_effect=lambda rid, _col: _doc(rid, recordName=f"Ticket {rid}")),
        )
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({
            "identifiers": ["PA-1", "PA-2", "PA-3"],
            "connector_name": "JIRA",
        })

        assert result["ok"] is True
        assert len(result["results"]) == 3
        assert "3/3" in result["summary"]
        assert all(r["ok"] for r in result["results"])

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self) -> None:
        provider = _make_provider(
            get_record_by_issue_key=AsyncMock(side_effect=[_record("r1"), None]),
            get_document=AsyncMock(side_effect=[_doc("r1"), None]),
        )
        provider.get_org_apps = AsyncMock(return_value=[{"_key": "conn-jira", "type": "JIRA", "name": "Jira"}])
        tool = create_lookup_record_tool(graph_provider=provider, org_id="org-1", user_id="user-1")

        result = await tool.ainvoke({"identifiers": ["PA-1", "PA-2"], "connector_name": "JIRA"})

        assert result["ok"] is True
        assert "1/2" in result["summary"]
        ok_results = [r for r in result["results"] if r["ok"]]
        fail_results = [r for r in result["results"] if not r["ok"]]
        assert len(ok_results) == 1
        assert len(fail_results) == 1
