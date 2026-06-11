"""Unit tests for app.utils.fetch_slack_thread."""

# ruff: noqa: ANN201, ANN202, ANN401

from __future__ import annotations

import importlib
import sys
import types
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors

# conftest.py may register a MagicMock for ``langchain_core`` when the package is
# absent, which breaks ``@tool`` on fetch_slack_thread. Provide a minimal stub.
_LC_SHIMMED = False
if isinstance(sys.modules.get("langchain_core"), MagicMock):

    def _minimal_lc_tool(tool_name: str, args_schema: Any = None) -> object:
        def _decorator(fn: Any) -> object:
            class _AsyncToolShim:
                name = tool_name

                async def ainvoke(
                    self, tool_input: dict[str, Any], config: Any = None,
                ) -> Any:
                    return await fn(**tool_input)

            return _AsyncToolShim()

        return _decorator

    _lc_pkg = types.ModuleType("langchain_core")
    _lc_tools = types.ModuleType("langchain_core.tools")
    _lc_tools.tool = _minimal_lc_tool
    _lc_pkg.tools = _lc_tools
    sys.modules["langchain_core"] = _lc_pkg
    sys.modules["langchain_core.tools"] = _lc_tools
    _LC_SHIMMED = True

if _LC_SHIMMED and "app.utils.fetch_slack_thread" in sys.modules:
    fst = importlib.reload(sys.modules["app.utils.fetch_slack_thread"])
else:
    from app.utils import fetch_slack_thread as fst


class _DummyRgType(Enum):
    """Non-str record_group_type with .value for branch coverage."""

    SLACK_THREAD = "SLACK_THREAD"


class _ModelDumpJsonFail:
    def model_dump(self, mode=None):
        if mode == "json":
            raise ValueError("json mode unsupported")
        return {"nested": 1}


class _ModelDumpPlain:
    def model_dump(self, mode=None):
        return {"plain": True}


@pytest.mark.asyncio
class TestHasSlackConnectorConfigured:
    async def test_returns_true_when_configured_slack_instance(self):
        graph = AsyncMock()
        graph.get_user_connector_instances = AsyncMock(
            return_value=[
                {"type": Connectors.SLACK.value, "isConfigured": True},
            ]
        )
        assert await fst.has_slack_connector_configured(graph, "u1", "o1") is True
        graph.get_user_connector_instances.assert_awaited_once()

    async def test_returns_true_for_slack_workspace_case_insensitive(self):
        graph = AsyncMock()
        graph.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "slack workspace", "isConfigured": 1}]
        )
        assert await fst.has_slack_connector_configured(graph, "u", "o") is True

    async def test_returns_false_when_not_configured_or_wrong_type(self):
        graph = AsyncMock()
        graph.get_user_connector_instances = AsyncMock(
            return_value=[
                {"type": "GMAIL", "isConfigured": True},
                {"type": "SLACK", "isConfigured": False},
            ]
        )
        assert await fst.has_slack_connector_configured(graph, "u", "o") is False

    async def test_returns_false_on_exception(self):
        graph = AsyncMock()
        graph.get_user_connector_instances = AsyncMock(side_effect=RuntimeError("db down"))
        assert await fst.has_slack_connector_configured(graph, "u", "o") is False

    async def test_handles_none_instances(self):
        graph = AsyncMock()
        graph.get_user_connector_instances = AsyncMock(return_value=None)
        assert await fst.has_slack_connector_configured(graph, "u", "o") is False


class TestAgentKnowledgeHasSlackConnector:
    def test_false_when_empty_or_none(self):
        assert fst.agent_knowledge_has_slack_connector(None) is False
        assert fst.agent_knowledge_has_slack_connector([]) is False

    def test_true_when_slack_type_present(self):
        knowledge = [{"type": Connectors.SLACK_WORKSPACE.value, "id": "x"}]
        assert fst.agent_knowledge_has_slack_connector(knowledge) is True

    def test_case_insensitive_and_skips_non_dict(self):
        knowledge = [None, "x", {"type": "slack"}, {"type": "DRIVE"}]
        assert fst.agent_knowledge_has_slack_connector(knowledge) is True

    def test_false_when_no_slack(self):
        assert fst.agent_knowledge_has_slack_connector([{"type": "NOTION"}]) is False


class TestFetchSlackThreadArgs:
    def test_defaults(self):
        args = fst.FetchSlackThreadArgs(record_id="rid-1")
        assert args.record_id == "rid-1"
        assert "Slack thread" in args.reason


class TestModelToDict:
    def test_none(self):
        assert fst._model_to_dict(None) == {}

    def test_dict_passthrough(self):
        d = {"a": 1}
        assert fst._model_to_dict(d) is d

    def test_model_dump_json_mode(self):
        assert fst._model_to_dict(_ModelDumpPlain()) == {"plain": True}

    def test_model_dump_fallback_when_json_raises(self):
        assert fst._model_to_dict(_ModelDumpJsonFail()) == {"nested": 1}

    def test_dict_constructor_fallback(self):
        assert fst._model_to_dict([("k", "v")]) == {"k": "v"}


@pytest.mark.asyncio
class TestResolveThreadRecordGroup:
    async def test_get_record_raises(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(side_effect=ConnectionError("x"))
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_empty_record(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(return_value=None)
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_slack_thread_case_with_rg_id(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": True,
                "record_group_id": "rg1",
                "external_record_group_id": "ext",
                "connector_id": "c1",
                "org_id": "o1",
            }
        )
        out = await fst._resolve_thread_record_group("rid", graph)
        assert out == {
            "record_group_id": "rg1",
            "external_record_group_id": "ext",
            "connector_id": "c1",
            "org_id": "o1",
        }

    async def test_legacy_slack_thread_rg_type_fallback(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "record_group_type": _DummyRgType.SLACK_THREAD,
                "record_group_id": "rg-legacy",
                "connector_id": "c",
                "org_id": "o",
            }
        )
        out = await fst._resolve_thread_record_group("rid", graph)
        assert out["record_group_id"] == "rg-legacy"

    async def test_slack_thread_missing_rg_id_returns_none(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": True,
                "record_group_id": "",
                "connector_id": "c",
            }
        )
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_slack_channel_without_replies(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": False,
                "connector_id": "c",
            }
        )
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_slack_channel_with_replies_resolves_via_external_id(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": True,
                "thread_id": "1.234",
                "external_record_group_id": "C123",
                "connector_id": "conn",
                "org_id": "org",
            }
        )
        graph.get_record_group_by_external_id = AsyncMock(
            return_value={"id": "thread-rg-key"}
        )
        out = await fst._resolve_thread_record_group("rid", graph)
        assert out == {
            "record_group_id": "thread-rg-key",
            "external_record_group_id": "thread_C123_1.234",
            "connector_id": "conn",
            "org_id": "org",
        }
        graph.get_record_group_by_external_id.assert_awaited_once_with(
            connector_id="conn",
            external_id="thread_C123_1.234",
        )

    async def test_slack_channel_uses_thread_id_and_key_fallback(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": True,
                "thread_id": "9.9",
                "external_record_group_id": "chan",
                "connector_id": "c2",
                "org_id": "o2",
            }
        )
        graph.get_record_group_by_external_id = AsyncMock(
            return_value={"_key": "k-from-arangodb"}
        )
        out = await fst._resolve_thread_record_group("rid", graph)
        assert out["record_group_id"] == "k-from-arangodb"

    async def test_slack_channel_uses_external_record_id_when_no_thread_id(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": True,
                "external_record_id": "7.7",
                "external_record_group_id": "ch2",
                "connector_id": "cx",
                "org_id": "ox",
            }
        )
        graph.get_record_group_by_external_id = AsyncMock(return_value={"id": "rgx"})
        out = await fst._resolve_thread_record_group("rid", graph)
        assert out["external_record_group_id"] == "thread_ch2_7.7"

    async def test_slack_channel_lookup_raises(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": True,
                "thread_id": "1",
                "external_record_group_id": "C",
                "connector_id": "c",
                "org_id": "o",
            }
        )
        graph.get_record_group_by_external_id = AsyncMock(side_effect=OSError("graph"))
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_slack_channel_no_thread_rg_found(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": True,
                "thread_id": "1",
                "external_record_group_id": "C",
                "connector_id": "c",
                "org_id": "o",
            }
        )
        graph.get_record_group_by_external_id = AsyncMock(return_value=None)
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_slack_channel_thread_rg_without_id(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "is_reply": False,
                "has_replies": True,
                "thread_id": "1",
                "external_record_group_id": "C",
                "connector_id": "c",
                "org_id": "o",
            }
        )
        graph.get_record_group_by_external_id = AsyncMock(return_value={})
        assert await fst._resolve_thread_record_group("rid", graph) is None

    async def test_not_a_thread_record(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={"is_reply": False, "has_replies": False, "record_group_id": "x"}
        )
        assert await fst._resolve_thread_record_group("rid", graph) is None


@pytest.mark.asyncio
class TestFetchThreadRecordsImpl:
    async def test_missing_graph_provider(self):
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=None,
            blob_store=MagicMock(),
            org_id="o",
        )
        assert out["ok"] is False
        assert "graph_provider" in out["error"]

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_missing_org_after_resolve(self, mock_resolve):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "org_id": "",
        }
        graph = AsyncMock()
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="",
        )
        assert out["ok"] is False
        assert "org_id" in out["error"].lower()

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_missing_blob_without_config_service(self, mock_resolve):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "org_id": "org-from-record",
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=None,
            org_id="",
            config_service=None,
        )
        assert out["ok"] is False
        assert "config_service" in out["error"].lower()

    @patch("app.modules.transformers.blob_storage.BlobStorage")
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_lazy_blob_from_config_service(self, mock_resolve, mock_blob_cls):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "org_id": "org-from-record",
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        mock_blob_cls.return_value = MagicMock()
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=None,
            org_id="",
            config_service=MagicMock(),
        )
        assert out["ok"] is True
        mock_blob_cls.assert_called_once()

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_not_resolved(self, mock_resolve):
        mock_resolve.return_value = None
        graph, blob = AsyncMock(), MagicMock()
        out = await fst._fetch_thread_records_impl(
            "bad",
            {},
            graph_provider=graph,
            blob_store=blob,
            org_id="org",
        )
        assert out["ok"] is False
        assert "not part of a Slack thread" in out["error"]

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_get_records_by_record_group_raises(self, mock_resolve):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "external_record_group_id": "ext",
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(side_effect=ValueError("boom"))
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org",
        )
        assert out["ok"] is False
        assert "Failed to list thread records" in out["error"]

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_empty_thread_records_ok(self, mock_resolve):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "external_record_group_id": "e",
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org",
        )
        assert out["ok"] is True
        assert out["records"] == []
        assert out["record_count"] == 0

    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_hydrates_sorts_and_skips(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "external_record_group_id": "ext-thread",
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(
            return_value=[
                {"id": "r-later", "source_created_at": 200},
                {},  # no id — skipped in loop
                {"_key": "r-earlier"},
            ]
        )

        async def fetch_side_effect(rid, **kwargs):
            if rid == "r-later":
                return {"id": rid, "source_created_at": 200}
            if rid == "r-earlier":
                return {"id": rid, "source_created_at": 100}
            return None

        mock_fetch.side_effect = fetch_side_effect
        vmap: dict = {}
        out = await fst._fetch_thread_records_impl(
            "start",
            vmap,
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org1",
        )
        assert out["ok"] is True
        assert [r["id"] for r in out["records"]] == ["r-earlier", "r-later"]
        assert out["record_count"] == 2
        assert out["thread_record_group_id"] == "trg"
        assert out["thread_external_record_group_id"] == "ext-thread"
        assert set(out["skipped_record_ids"]) == set()

    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_skipped_when_fetch_returns_none(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "external_record_group_id": None,
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "ghost"}])
        mock_fetch.return_value = None
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org",
        )
        assert out["ok"] is True
        assert out["records"] == []
        assert out["skipped_record_ids"] == ["ghost"]

    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_sort_handles_missing_source_created_at(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
        }
        graph = AsyncMock()
        graph.get_records_by_record_group = AsyncMock(
            return_value=[{"id": "a"}, {"id": "b"}]
        )
        mock_fetch.side_effect = [
            {"id": "a"},
            {"id": "b", "source_created_at": 50},
        ]
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org",
        )
        assert out["ok"] is True
        assert len(out["records"]) == 2


@pytest.mark.asyncio
class TestCreateFetchSlackThreadTool:
    @patch.object(fst, "_fetch_thread_records_impl", new_callable=AsyncMock)
    async def test_tool_delegates_to_impl(self, mock_impl):
        mock_impl.return_value = {"ok": True, "records": []}
        vmap: dict = {}
        tool_fn = fst.create_fetch_slack_thread_tool(
            vmap,
            org_id="o",
            graph_provider=AsyncMock(),
            blob_store=MagicMock(),
            config_service=MagicMock(),
        )
        result = await tool_fn.ainvoke({"record_id": "rec-1", "reason": "need context"})
        assert result["ok"] is True
        mock_impl.assert_awaited_once()
        kwargs = mock_impl.await_args.kwargs
        assert kwargs["record_id"] == "rec-1"
        assert kwargs["virtual_record_id_to_result"] is vmap
        assert kwargs["config_service"] is not None

    @patch.object(fst, "_fetch_thread_records_impl", new_callable=AsyncMock)
    async def test_tool_exception_returns_error_dict(self, mock_impl):
        mock_impl.side_effect = RuntimeError("unexpected")
        tool_fn = fst.create_fetch_slack_thread_tool(
            {},
            org_id="o",
            graph_provider=AsyncMock(),
            blob_store=MagicMock(),
        )
        result = await tool_fn.ainvoke({"record_id": "r"})
        assert result["ok"] is False
        assert "Failed to fetch Slack thread" in result["error"]
