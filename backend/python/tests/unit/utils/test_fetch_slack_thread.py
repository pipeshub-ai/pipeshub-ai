"""Unit tests for app.utils.fetch_slack_thread."""

# ruff: noqa: ANN201, ANN202, ANN401

from __future__ import annotations

import importlib
import json
import sys
import types
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.services.graph_db.interface.graph_db_provider import AccessCheck

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
class TestFetchRecordById:
    async def test_missing_dependencies_returns_none(self):
        vmap: dict = {}
        assert await fst._fetch_record_by_id("rid", None, MagicMock(), "org", vmap) is None
        assert await fst._fetch_record_by_id("rid", AsyncMock(), None, "org", vmap) is None
        assert await fst._fetch_record_by_id("rid", AsyncMock(), MagicMock(), None, vmap) is None

    async def test_graph_record_not_found(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(return_value=None)
        out = await fst._fetch_record_by_id(
            "missing",
            graph,
            MagicMock(),
            "org",
            {},
        )
        assert out is None

    async def test_missing_virtual_record_id(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(return_value={"id": "r1"})
        out = await fst._fetch_record_by_id(
            "r1",
            graph,
            MagicMock(),
            "org",
            {},
        )
        assert out is None

    @patch("app.utils.chat_helpers.get_record", new_callable=AsyncMock)
    async def test_cache_hit_returns_existing(self, mock_get_record):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={"virtual_record_id": "vr1", "id": "r1"}
        )
        cached = {"id": "r1", "content": "cached"}
        vmap = {"vr1": cached}
        out = await fst._fetch_record_by_id(
            "r1",
            graph,
            MagicMock(),
            "org",
            vmap,
        )
        assert out is cached
        mock_get_record.assert_not_awaited()

    @patch("app.utils.chat_helpers.get_record", new_callable=AsyncMock)
    async def test_fetches_via_get_record_and_enriches(self, mock_get_record):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={
                "virtual_record_id": "vr-new",
                "id": "graph-id",
                "record_name": "Thread msg",
                "record_type": "MESSAGE",
                "connector_name": "Slack",
            }
        )
        blob = MagicMock()
        vmap: dict = {}

        async def populate_map(vrid, result_map, *args, **kwargs):
            result_map[vrid] = {"recordName": "Thread msg"}

        mock_get_record.side_effect = populate_map
        out = await fst._fetch_record_by_id(
            "graph-id",
            graph,
            blob,
            "org-1",
            vmap,
        )
        assert out is not None
        assert out["id"] == "graph-id"
        assert out["virtual_record_id"] == "vr-new"
        assert vmap["vr-new"] is out
        mock_get_record.assert_awaited_once()
        call_kwargs = mock_get_record.await_args.kwargs
        assert call_kwargs["virtual_to_record_map"]["vr-new"]["recordName"] == "Thread msg"
        assert call_kwargs["graph_provider"] is graph

    @patch("app.utils.chat_helpers.get_record", new_callable=AsyncMock)
    async def test_sets_id_when_blob_record_missing_id(self, mock_get_record):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={"virtual_record_id": "vr2", "_key": "key-only"}
        )
        vmap: dict = {}

        async def populate_map(vrid, result_map, *args, **kwargs):
            result_map[vrid] = {"recordName": "no id field"}

        mock_get_record.side_effect = populate_map
        out = await fst._fetch_record_by_id(
            "requested-id",
            graph,
            MagicMock(),
            "org",
            vmap,
        )
        assert out["id"] == "requested-id"

    @patch("app.utils.chat_helpers.get_record", new_callable=AsyncMock)
    async def test_blob_fetch_empty_returns_none(self, mock_get_record):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(
            return_value={"virtual_record_id": "vr-miss", "id": "r1"}
        )
        mock_get_record.return_value = None
        out = await fst._fetch_record_by_id(
            "r1",
            graph,
            MagicMock(),
            "org",
            {},
        )
        assert out is None

    async def test_exception_returns_none(self):
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(side_effect=RuntimeError("graph down"))
        out = await fst._fetch_record_by_id(
            "r1",
            graph,
            MagicMock(),
            "org",
            {},
        )
        assert out is None


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
class TestResolveUserKey:
    async def test_none_without_user_id(self):
        graph = AsyncMock()
        assert await fst.resolve_user_key(graph, None) is None
        graph.get_user_by_user_id.assert_not_awaited()

    async def test_prefers_key_then_id(self):
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "k1", "id": "i1"})
        assert await fst.resolve_user_key(graph, "u") == "k1"
        graph.get_user_by_user_id = AsyncMock(return_value={"id": "i1"})
        assert await fst.resolve_user_key(graph, "u") == "i1"

    async def test_unknown_user_or_error_is_none(self):
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        assert await fst.resolve_user_key(graph, "u") is None
        graph.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("db"))
        assert await fst.resolve_user_key(graph, "u") is None


@pytest.mark.asyncio
class TestUserCanAccessNode:
    async def test_true_when_node_returned(self):
        graph = AsyncMock()
        graph.get_knowledge_hub_node_access = AsyncMock(return_value={"id": "r1"})
        assert await fst.user_can_access_node(graph, "r1", "ukey", "org1") is True
        kwargs = graph.get_knowledge_hub_node_access.await_args.kwargs
        assert kwargs["node_id"] == "r1"
        assert kwargs["user_key"] == "ukey"
        assert kwargs["org_id"] == "org1"

    async def test_false_when_denied_or_error(self):
        graph = AsyncMock()
        graph.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        assert await fst.user_can_access_node(graph, "r1", "ukey", "org1") is False
        graph.get_knowledge_hub_node_access = AsyncMock(side_effect=RuntimeError("db"))
        assert await fst.user_can_access_node(graph, "r1", "ukey", "org1") is False


def _allowing(graph, allowed=None):
    """Graph mock whose batch access check admits ``allowed`` (default: all)."""
    graph.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
    graph.check_access = AsyncMock(
        side_effect=lambda user_key, org_id, node_ids=(), **_: AccessCheck(
            node_ids=frozenset(node_ids) if allowed is None else frozenset(node_ids) & allowed,
        ),
    )
    return graph


@pytest.mark.asyncio
class TestFetchThreadRecordsImplAccess:
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    @patch.object(fst, "user_can_access_node", new_callable=AsyncMock)
    @patch.object(fst, "resolve_user_key", new_callable=AsyncMock)
    async def test_denied_record_returns_error_without_content(
        self, mock_key, mock_access, mock_resolve,
    ):
        mock_key.return_value = "ukey"
        mock_access.return_value = False
        graph = AsyncMock()
        out = await fst._fetch_thread_records_impl(
            "private-rec",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org1",
            user_id="outsider",
        )
        assert out["ok"] is False
        assert "don't have access" in out["error"]
        assert "records" not in out
        mock_access.assert_awaited_once_with(graph, "private-rec", "ukey", "org1")
        mock_resolve.assert_not_awaited()
        graph.get_records_by_record_group.assert_not_awaited()

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    @patch.object(fst, "resolve_user_key", new_callable=AsyncMock)
    async def test_unresolved_user_is_rejected(self, mock_key, mock_resolve):
        mock_key.return_value = None
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=AsyncMock(),
            blob_store=MagicMock(),
            org_id="org1",
            user_id=None,
        )
        assert out["ok"] is False
        assert "user" in out["error"]
        mock_resolve.assert_not_awaited()

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    @patch.object(fst, "user_can_access_node", new_callable=AsyncMock)
    @patch.object(fst, "resolve_user_key", new_callable=AsyncMock)
    async def test_thread_listing_is_permission_filtered(
        self, mock_key, mock_access, mock_resolve,
    ):
        mock_key.return_value = "ukey"
        mock_access.return_value = True
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "org_id": "org1",
        }
        graph = _allowing(AsyncMock())
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org1",
            user_id="member",
        )
        assert out["ok"] is True
        kwargs = graph.get_records_by_record_group.await_args.kwargs
        assert kwargs["user_key"] == "ukey"
        assert kwargs["org_id"] == "org1"


    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    @patch.object(fst, "user_can_access_node", new_callable=AsyncMock)
    @patch.object(fst, "resolve_user_key", new_callable=AsyncMock)
    async def test_thread_records_outside_the_turns_scope_are_left_out(
        self, mock_key, mock_access, mock_resolve, mock_fetch,
    ):
        mock_key.return_value = "ukey"
        mock_access.return_value = True
        mock_resolve.return_value = {"record_group_id": "trg", "connector_id": "c", "org_id": "org1"}
        mock_fetch.side_effect = lambda rid, **_: {"id": rid}
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
        graph.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})
        graph.check_access = AsyncMock(side_effect=lambda user_key, org_id, node_ids=(), scopes=(), **_: AccessCheck(
            node_ids=frozenset(node_ids),
            node_ids_in_scope=frozenset(node_ids) & {"rid", "inside"} if scopes else frozenset(node_ids),
        ))
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "inside"}, {"id": "outside"}])
        state = {"filters": {"apps": [], "kb": [], "records": ["folder-a"]}}

        out = await fst._fetch_thread_records_impl(
            "rid", {}, graph_provider=graph, blob_store=MagicMock(), org_id="org1",
            user_id="member", scope_state=state,
        )

        assert [r["id"] for r in out["records"]] == ["inside"]

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    @patch.object(fst, "user_can_access_node", new_callable=AsyncMock)
    @patch.object(fst, "resolve_user_key", new_callable=AsyncMock)
    async def test_a_record_outside_the_turns_scope_is_not_a_thread(self, mock_key, mock_access, mock_resolve):
        mock_key.return_value = "ukey"
        mock_access.return_value = True
        mock_resolve.return_value = {"record_group_id": "trg", "connector_id": "c", "org_id": "org1"}
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
        graph.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})
        graph.check_access = AsyncMock(side_effect=lambda user_key, org_id, node_ids=(), scopes=(), **_: AccessCheck(
            node_ids=frozenset(node_ids), node_ids_in_scope=frozenset() if scopes else frozenset(node_ids),
        ))
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "rid"}])

        out = await fst._fetch_thread_records_impl(
            "rid", {}, graph_provider=graph, blob_store=MagicMock(), org_id="org1",
            user_id="member", scope_state={"filters": {"apps": [], "kb": [], "records": ["folder-a"]}},
        )

        assert out["ok"] is False and "records" not in out


@pytest.mark.asyncio
class TestFetchThreadRecordsImpl:
    @pytest.fixture(autouse=True)
    def _allow_access(self):
        with patch.object(
            fst, "resolve_user_key", AsyncMock(return_value="ukey"),
        ), patch.object(
            fst, "user_can_access_node", AsyncMock(return_value=True),
        ):
            yield

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
    async def test_missing_chat_org_is_not_taken_from_record(self, mock_resolve):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "org_id": "org-from-record",
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
        mock_resolve.assert_not_awaited()
        graph.get_records_by_record_group.assert_not_awaited()

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_missing_blob_without_config_service(self, mock_resolve):
        mock_resolve.return_value = {
            "record_group_id": "trg",
            "connector_id": "c",
            "org_id": "org-from-record",
        }
        graph = _allowing(AsyncMock())
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=None,
            org_id="org-from-record",
            config_service=None,
            user_id="u1",
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
        graph = _allowing(AsyncMock())
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        mock_blob_cls.return_value = MagicMock()
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=None,
            org_id="org-from-record",
            config_service=MagicMock(),
            user_id="u1",
        )
        assert out["ok"] is True
        mock_blob_cls.assert_called_once()

    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_not_resolved(self, mock_resolve):
        """A record that is not from Slack has no thread to read."""
        mock_resolve.return_value = None
        graph, blob = AsyncMock(), MagicMock()
        graph.get_record_by_id = AsyncMock(return_value={"connector_name": "JIRA", "org_id": "org"})
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
            "org_id": "org",
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
            "org_id": "org",
        }
        graph = _allowing(AsyncMock())
        graph.get_records_by_record_group = AsyncMock(return_value=[])
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org",
            user_id="u1",
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
            "org_id": "org",
        }
        graph = _allowing(AsyncMock())
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
            org_id="org",
            user_id="u1",
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
            "org_id": "org",
        }
        graph = _allowing(AsyncMock())
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "ghost"}])
        mock_fetch.return_value = None
        out = await fst._fetch_thread_records_impl(
            "rid",
            {},
            graph_provider=graph,
            blob_store=MagicMock(),
            org_id="org",
            user_id="u1",
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
            "org_id": "org",
        }
        graph = _allowing(AsyncMock())
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
            user_id="u1",
        )
        assert out["ok"] is True
        assert len(out["records"]) == 2


    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_a_record_of_another_org_is_not_a_thread(self, mock_resolve):
        mock_resolve.return_value = {"record_group_id": "trg", "connector_id": "c", "org_id": "other-org"}
        graph = _allowing(AsyncMock())
        out = await fst._fetch_thread_records_impl(
            "rid", {}, graph_provider=graph, blob_store=MagicMock(), org_id="org", user_id="u1",
        )
        assert out["ok"] is False
        assert "not part of a Slack thread" in out["error"]
        graph.get_records_by_record_group.assert_not_called()

    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_an_inaccessible_input_record_reads_as_not_a_thread(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = {"record_group_id": "trg", "connector_id": "c", "org_id": "org"}
        graph = _allowing(AsyncMock(), allowed={"r1"})
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "r1"}])
        out = await fst._fetch_thread_records_impl(
            "start", {}, graph_provider=graph, blob_store=MagicMock(), org_id="org", user_id="u1",
        )
        assert out["ok"] is False
        assert "not part of a Slack thread" in out["error"]
        mock_fetch.assert_not_called()

    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_only_accessible_thread_records_are_fetched(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = {"record_group_id": "trg", "connector_id": "c", "org_id": "org"}
        graph = _allowing(AsyncMock(), allowed={"start", "r1"})
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "r1"}, {"id": "r2"}])
        mock_fetch.side_effect = lambda rid, **kwargs: {"id": rid}
        out = await fst._fetch_thread_records_impl(
            "start", {}, graph_provider=graph, blob_store=MagicMock(), org_id="org", user_id="u1",
        )
        assert [r["id"] for r in out["records"]] == ["r1"]
        assert out["skipped_record_ids"] == []
        _, org = graph.check_access.await_args.args
        ids = graph.check_access.await_args.kwargs["node_ids"]
        assert org == "org" and set(ids) == {"start", "r1", "r2"}

    @patch.object(fst, "_fetch_record_by_id", new_callable=AsyncMock)
    @patch.object(fst, "_resolve_thread_record_group", new_callable=AsyncMock)
    async def test_no_user_sees_nothing(self, mock_resolve, mock_fetch):
        mock_resolve.return_value = {"record_group_id": "trg", "connector_id": "c", "org_id": "org"}
        graph = _allowing(AsyncMock())
        graph.get_records_by_record_group = AsyncMock(return_value=[{"id": "r1"}])
        out = await fst._fetch_thread_records_impl(
            "start", {}, graph_provider=graph, blob_store=MagicMock(), org_id="org",
        )
        assert out["ok"] is False
        mock_fetch.assert_not_called()


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
        assert kwargs["user_id"] is None

    @patch.object(fst, "_fetch_thread_records_impl", new_callable=AsyncMock)
    async def test_tool_forwards_user_id(self, mock_impl):
        mock_impl.return_value = {"ok": True, "records": []}
        tool_fn = fst.create_fetch_slack_thread_tool(
            {},
            org_id="o",
            graph_provider=AsyncMock(),
            user_id="user-1",
        )
        await tool_fn.ainvoke({"record_id": "rec-1"})
        assert mock_impl.await_args.kwargs["user_id"] == "user-1"

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


class TestResolveThreadReadErrors:
    """A failed graph read is not "this record has no thread"."""

    async def test_a_failed_read_is_none_by_default_and_raised_when_asked(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        import pytest

        from app.utils.fetch_slack_thread import _resolve_thread_record_group

        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(side_effect=RuntimeError("graph down"))

        assert await _resolve_thread_record_group("r-1", graph) is None
        with pytest.raises(RuntimeError):
            await _resolve_thread_record_group("r-1", graph, raise_on_error=True)


# ---------------------------------------------------------------------------
# Slack records in no single thread: a run of channel messages, a message
# without replies, a file shared in a thread, a reply also sent to the channel
# ---------------------------------------------------------------------------

_CONNECTOR = "conn-slack"
_ORG = "org"


def _slack_meta(**fields: Any) -> dict[str, Any]:
    return {
        "connector_name": Connectors.SLACK.value,
        "connector_id": _CONNECTOR,
        "org_id": _ORG,
        "record_type": "MESSAGE",
        "external_record_group_id": "C1",
        "is_reply": False,
        "has_replies": False,
        **fields,
    }


def _hydrated(record_id: str, *messages: tuple[str, bool], created: int = 0) -> dict[str, Any]:
    """A record as the blob read returns it: one block per message, whose
    ``source_id`` is the message's ts; ``True`` adds the "Reply Count" line."""
    return {
        "id": record_id,
        "source_created_at": created,
        "block_containers": {
            "blocks": [
                {
                    "index": i,
                    "source_id": ts,
                    "data": f"**A**: message {ts}" + ("\n\nReply Count: 2" if replies else ""),
                }
                for i, (ts, replies) in enumerate(messages)
            ],
            "block_groups": [],
        },
    }


def _slack_graph(
    records: dict[str, dict[str, Any]],
    groups: dict[str, str] | None = None,
    listings: dict[str, list[str]] | None = None,
    allowed: set[str] | None = None,
) -> AsyncMock:
    """A graph holding ``records`` (id -> graph fields), thread groups
    (external id -> group id) and the records listed in each group."""
    groups = groups or {}
    listings = listings or {}
    graph = _allowing(AsyncMock(), allowed)
    graph.get_record_by_id = AsyncMock(side_effect=lambda rid: records.get(rid))
    graph.get_record_group_by_external_id = AsyncMock(
        side_effect=lambda connector_id, external_id: (
            {"id": groups[external_id]} if external_id in groups and connector_id == _CONNECTOR else None
        ),
    )
    graph.get_records_by_record_group = AsyncMock(
        side_effect=lambda record_group_id, **_: [{"id": rid} for rid in listings.get(record_group_id, [])],
    )
    return graph


async def _read_thread(graph: AsyncMock, record_id: str, hydrated: dict[str, dict], **kwargs: Any) -> dict:
    async def fetch(rid: str, **_: Any) -> dict | None:
        return hydrated.get(rid)

    with patch.object(fst, "resolve_user_key", AsyncMock(return_value="ukey")), \
            patch.object(fst, "user_can_access_node", AsyncMock(return_value=True)), \
            patch.object(fst, "_fetch_record_by_id", AsyncMock(side_effect=fetch)) as fetched:
        out = await fst._fetch_thread_records_impl(
            record_id, {}, graph_provider=graph, blob_store=MagicMock(), org_id=_ORG,
            user_id=kwargs.pop("user_id", "u1"), **kwargs,
        )
    out["_fetched"] = [c.args[0] for c in fetched.await_args_list]
    return out


@pytest.mark.asyncio
class TestChannelRecordThreads:
    async def test_a_burst_with_thread_parents_reads_itself_then_each_thread(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_ab12")}
        hydrated = {
            "burst": _hydrated(
                "burst", ("1700000000.000100", False), ("1700000100.000200", True),
                ("1700000050.000300", True), created=1,
            ),
            "a-2": {"id": "a-2", "source_created_at": 30},
            "a-1": {"id": "a-1", "source_created_at": 20},
            "b-1": {"id": "b-1", "source_created_at": 10},
        }
        graph = _slack_graph(
            records,
            groups={
                "thread_C1_1700000100.000200": "rg-later",
                "thread_C1_1700000050.000300": "rg-earlier",
            },
            listings={"rg-earlier": ["a-2", "a-1"], "rg-later": ["b-1"]},
        )

        out = await _read_thread(graph, "burst", hydrated)

        assert out["ok"] is True
        # The record, then each thread in the order its parent was posted,
        # replies oldest first within it.
        assert [r["id"] for r in out["records"]] == ["burst", "a-1", "a-2", "b-1"]
        assert [t["thread_record_group_id"] for t in out["threads"]] == ["rg-earlier", "rg-later"]
        assert [t["record_count"] for t in out["threads"]] == [2, 1]
        assert out["threads_not_read"] == 0
        assert "3 Slack messages; 2 of them started a thread" in out["note"]
        # Every reply listing is the permission-filtered one, run as the user.
        for call in graph.get_records_by_record_group.await_args_list:
            assert call.kwargs["user_key"] == "ukey" and call.kwargs["org_id"] == _ORG

    async def test_a_burst_without_replies_returns_itself(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_cd34")}
        hydrated = {"burst": _hydrated("burst", ("1700000000.000100", False), ("1700000001.000100", False))}
        graph = _slack_graph(records)

        out = await _read_thread(graph, "burst", hydrated)

        assert out["ok"] is True
        assert [r["id"] for r in out["records"]] == ["burst"]
        assert out["threads"] == []
        assert out["note"].startswith("No replies")
        graph.get_records_by_record_group.assert_not_awaited()

    async def test_a_single_message_without_replies_returns_itself(self):
        records = {"msg": _slack_meta(external_record_id="1700000000.000100")}
        hydrated = {"msg": _hydrated("msg", ("1700000000.000100", False))}
        graph = _slack_graph(records)

        out = await _read_thread(graph, "msg", hydrated)

        assert out["ok"] is True
        assert [r["id"] for r in out["records"]] == ["msg"]
        assert out["note"].startswith("No replies")
        # Its own ts was looked up once, not once per source.
        looked_up = [c.kwargs["external_id"] for c in graph.get_record_group_by_external_id.await_args_list]
        assert looked_up == ["thread_C1_1700000000.000100"]

    async def test_a_parent_whose_flag_is_stale_still_finds_its_thread(self):
        """`has_replies` is written when the message is indexed; a thread that
        grew later is found through its group."""
        records = {"msg": _slack_meta(external_record_id="1700000000.000100", has_replies=False)}
        hydrated = {"msg": _hydrated("msg", ("1700000000.000100", False)), "r-1": {"id": "r-1"}}
        graph = _slack_graph(
            records, groups={"thread_C1_1700000000.000100": "rg"}, listings={"rg": ["r-1"]},
        )

        out = await _read_thread(graph, "msg", hydrated)

        assert [r["id"] for r in out["records"]] == ["msg", "r-1"]

    async def test_a_real_thread_reads_as_before(self):
        """A thread reply reads its thread's records only, with no note."""
        records = {
            "reply": _slack_meta(
                is_reply=True, record_group_id="rg", external_record_group_id="thread_C1_1700000000.000100",
            ),
        }
        hydrated = {"r-1": {"id": "r-1", "source_created_at": 2}, "reply": {"id": "reply", "source_created_at": 1}}
        graph = _slack_graph(records, listings={"rg": ["r-1", "reply"]})

        out = await _read_thread(graph, "reply", hydrated)

        assert [r["id"] for r in out["records"]] == ["reply", "r-1"]
        assert out["thread_record_group_id"] == "rg"
        assert "note" not in out and "threads" not in out
        graph.get_record_group_by_external_id.assert_not_awaited()

    async def test_replies_the_user_may_not_see_are_left_out_unread(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_ef56")}
        hydrated = {
            "burst": _hydrated("burst", ("1700000000.000100", True), ("1700000200.000100", True)),
            "open": {"id": "open"},
            "secret": {"id": "secret"},
            "secret-2": {"id": "secret-2"},
        }
        graph = _slack_graph(
            records,
            groups={"thread_C1_1700000000.000100": "rg-a", "thread_C1_1700000200.000100": "rg-b"},
            listings={"rg-a": ["open", "secret"], "rg-b": ["secret-2"]},
            allowed={"burst", "open"},
        )

        out = await _read_thread(graph, "burst", hydrated)

        assert [r["id"] for r in out["records"]] == ["burst", "open"]
        assert "secret" not in out["_fetched"] and "secret-2" not in out["_fetched"]
        # A thread with nothing the user may read is not named either.
        assert [t["thread_record_group_id"] for t in out["threads"]] == ["rg-a"]
        assert "1 of them started a thread" in out["note"]
        assert out["skipped_record_ids"] == []

    async def test_an_inaccessible_record_is_not_read_at_all(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0000")}
        hydrated = {"burst": _hydrated("burst", ("1700000000.000100", True))}
        graph = _slack_graph(records, allowed=set())

        out = await _read_thread(graph, "burst", hydrated)

        assert out["ok"] is False and "records" not in out
        assert out["_fetched"] == []
        graph.get_record_group_by_external_id.assert_not_awaited()

    async def test_a_record_outside_the_turns_scope_is_not_read(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0001")}
        hydrated = {"burst": _hydrated("burst", ("1700000000.000100", True))}
        graph = _slack_graph(records)
        graph.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})
        graph.check_access = AsyncMock(side_effect=lambda user_key, org_id, node_ids=(), scopes=(), **_: AccessCheck(
            node_ids=frozenset(node_ids), node_ids_in_scope=frozenset() if scopes else frozenset(node_ids),
        ))

        out = await _read_thread(
            graph, "burst", hydrated, scope_state={"filters": {"apps": [], "kb": [], "records": ["folder-a"]}},
        )

        assert out["ok"] is False and out["_fetched"] == []

    async def test_a_record_of_another_org_is_not_read(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0002", org_id="other-org")}
        graph = _slack_graph(records)

        out = await _read_thread(graph, "burst", {})

        assert out["ok"] is False and out["_fetched"] == []

    async def test_threads_beyond_the_bound_are_counted_not_read(self):
        stamps = [f"17000{i:05d}.000100" for i in range(fst.MAX_THREADS_PER_RECORD + 2)]
        records = {"burst": _slack_meta(external_record_id="burst_C1_0003")}
        hydrated = {"burst": _hydrated("burst", *[(ts, True) for ts in stamps])}
        hydrated.update({f"r-{ts}": {"id": f"r-{ts}"} for ts in stamps})
        graph = _slack_graph(
            records,
            groups={f"thread_C1_{ts}": f"rg-{ts}" for ts in stamps},
            listings={f"rg-{ts}": [f"r-{ts}"] for ts in stamps},
        )

        out = await _read_thread(graph, "burst", hydrated)

        assert len(out["threads"]) == fst.MAX_THREADS_PER_RECORD
        assert out["threads_not_read"] == 2
        assert graph.get_records_by_record_group.await_count == fst.MAX_THREADS_PER_RECORD
        assert "2 more thread(s)" in out["note"]

    async def test_unread_threads_are_counted_even_when_the_read_ones_show_nothing(self):
        """Threads past the bound are named in the note even when none of the
        threads that were read has a reply the user may see."""
        stamps = [f"17000{i:05d}.000100" for i in range(fst.MAX_THREADS_PER_RECORD + 3)]
        records = {"burst": _slack_meta(external_record_id="burst_C1_0009")}
        hydrated = {"burst": _hydrated("burst", *[(ts, True) for ts in stamps])}
        graph = _slack_graph(
            records,
            groups={f"thread_C1_{ts}": f"rg-{ts}" for ts in stamps},
            listings={f"rg-{ts}": [f"r-{ts}"] for ts in stamps},
            allowed={"burst"},
        )

        out = await _read_thread(graph, "burst", hydrated)

        assert out["threads"] == [] and out["threads_not_read"] == 3
        assert out["note"].startswith("No replies")
        assert out["note"].endswith("3 more thread(s) of this record were not read.")

    async def test_messages_with_replies_are_looked_up_first_and_the_lookups_are_bounded(self):
        stamps = [f"17{i:08d}.000100" for i in range(fst.MAX_MESSAGES_CHECKED + 20)]
        parent = stamps[-1]
        records = {"burst": _slack_meta(external_record_id="burst_C1_0004")}
        hydrated = {"burst": _hydrated("burst", *[(ts, ts == parent) for ts in stamps]), "r": {"id": "r"}}
        graph = _slack_graph(records, groups={f"thread_C1_{parent}": "rg"}, listings={"rg": ["r"]})

        out = await _read_thread(graph, "burst", hydrated)

        assert [r["id"] for r in out["records"]] == ["burst", "r"]
        assert graph.get_record_group_by_external_id.await_count == fst.MAX_MESSAGES_CHECKED

    async def test_a_failed_thread_lookup_is_an_error_not_no_replies(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0005")}
        hydrated = {"burst": _hydrated("burst", ("1700000000.000100", True))}
        graph = _slack_graph(records)
        graph.get_record_group_by_external_id = AsyncMock(side_effect=RuntimeError("graph down"))

        out = await _read_thread(graph, "burst", hydrated)

        assert out["ok"] is False
        assert "Failed to look up the threads" in out["error"]

    async def test_a_record_whose_content_cannot_be_loaded_says_so(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0006")}
        graph = _slack_graph(records)

        out = await _read_thread(graph, "burst", {})

        assert out["ok"] is True and out["records"] == []
        assert out["skipped_record_ids"] == ["burst"]
        assert "could not be loaded" in out["note"]

    async def test_a_failed_reply_listing_is_an_error(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0007")}
        hydrated = {"burst": _hydrated("burst", ("1700000000.000100", True))}
        graph = _slack_graph(records, groups={"thread_C1_1700000000.000100": "rg"})
        graph.get_records_by_record_group = AsyncMock(side_effect=RuntimeError("graph down"))

        out = await _read_thread(graph, "burst", hydrated)

        assert out["ok"] is False and "Failed to list thread records" in out["error"]

    async def test_a_reply_that_cannot_be_loaded_is_named(self):
        records = {"burst": _slack_meta(external_record_id="burst_C1_0008")}
        hydrated = {"burst": _hydrated("burst", ("1700000000.000100", True)), "ok-reply": {"id": "ok-reply"}}
        graph = _slack_graph(
            records, groups={"thread_C1_1700000000.000100": "rg"}, listings={"rg": ["ok-reply", "gone"]},
        )

        out = await _read_thread(graph, "burst", hydrated)

        assert [r["id"] for r in out["records"]] == ["burst", "ok-reply"]
        assert out["skipped_record_ids"] == ["gone"]

    def test_message_timestamps_read_block_models_too(self):
        from types import SimpleNamespace

        record = {"block_containers": SimpleNamespace(blocks=[
            SimpleNamespace(source_id="1700000000.000100", data="**A**: hi"),
            SimpleNamespace(source_id="1700000005.000100", data="**B**: q\n\nReply Count: 3"),
            SimpleNamespace(source_id="#/texts/0", data="not a message"),
        ])}
        assert fst._message_timestamps(record) == ["1700000005.000100", "1700000000.000100"]
        assert fst._message_timestamps(None) == []

    async def test_a_record_not_from_slack_is_not_a_thread(self):
        graph = _slack_graph({"doc": {"connector_name": "CONFLUENCE", "org_id": _ORG, "connector_id": "c"}})

        out = await _read_thread(graph, "doc", {"doc": {"id": "doc"}})

        assert out["ok"] is False and "not part of a Slack thread" in out["error"]
        assert out["_fetched"] == []


@pytest.mark.asyncio
class TestResolveSlackRecordsInThreads:
    async def test_a_file_shared_in_a_thread_reads_that_thread(self):
        graph = _slack_graph({
            "file": _slack_meta(
                record_type="FILE", record_group_id="rg-thread",
                external_record_group_id="thread_C1_1700000000.000100", is_reply=None, has_replies=None,
            ),
        })
        out = await fst._resolve_thread_record_group("file", graph)
        assert out["record_group_id"] == "rg-thread"

    async def test_a_file_of_another_connector_is_not_taken_for_a_thread(self):
        graph = _slack_graph({
            "file": {"connector_name": "GMAIL", "record_group_id": "rg", "external_record_group_id": "thread_x"},
        })
        assert await fst._resolve_thread_record_group("file", graph) is None

    async def test_a_reply_also_sent_to_the_channel_reads_its_parents_thread(self):
        graph = _slack_graph(
            {"bcast": _slack_meta(external_record_id="1700000500.000100", thread_id="1700000000.000100")},
            groups={"thread_C1_1700000000.000100": "rg-parent"},
        )
        out = await fst._resolve_thread_record_group("bcast", graph)
        assert out["record_group_id"] == "rg-parent"
        assert out["external_record_group_id"] == "thread_C1_1700000000.000100"

    async def test_the_channel_record_resolver(self):
        graph = _slack_graph({
            "burst": _slack_meta(external_record_id="burst_C1_ab"),
            "msg": _slack_meta(external_record_id="1700000000.000100"),
            "doc": {"connector_name": "JIRA"},
        })
        burst = await fst._resolve_slack_channel_record("burst", graph)
        assert burst == {
            "connector_id": _CONNECTOR, "org_id": _ORG, "channel_id": "C1",
            "is_message": True, "timestamps": [],
        }
        assert (await fst._resolve_slack_channel_record("msg", graph))["timestamps"] == ["1700000000.000100"]
        assert await fst._resolve_slack_channel_record("doc", graph) is None
        assert await fst._resolve_slack_channel_record("missing", graph) is None

    async def test_the_channel_record_resolver_raises_when_asked(self):
        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(side_effect=RuntimeError("graph down"))
        assert await fst._resolve_slack_channel_record("r", graph) is None
        with pytest.raises(RuntimeError):
            await fst._resolve_slack_channel_record("r", graph, raise_on_error=True)


# ---------------------------------------------------------------------------
# The record shapes behind 189 thread reads that were refused in an
# evaluation run, replayed: every one of them now reads.
# ---------------------------------------------------------------------------

_REFUSED = json.loads(
    (Path(__file__).parent / "fixtures" / "slack_refused_thread_reads.json").read_text()
)["records"]


def _replay_graph(case: dict[str, Any]) -> tuple[AsyncMock, dict[str, dict]]:
    meta = {**case["meta"], "connector_id": _CONNECTOR, "org_id": _ORG}
    if meta["external_record_group_id"].startswith("thread_"):
        meta["record_group_id"] = "rg-own-thread"
    groups = {ext: f"rg-{ext}" for ext in case["thread_groups"]}
    listings = {
        f"rg-{ext}": [f"{ext}-r{i}" for i in range(count)] for ext, count in case["thread_groups"].items()
    }
    listings["rg-own-thread"] = ["own-thread-r0"]
    graph = _slack_graph({case["id"]: meta}, groups=groups, listings=listings)
    hydrated: dict[str, dict] = {
        rid: {"id": rid} for listed in listings.values() for rid in listed
    }
    if case["blocks"] is not None:
        hydrated[case["id"]] = _hydrated(
            case["id"], *[(b["ts"], b["reply_count_line"]) for b in case["blocks"]],
        )
    return graph, hydrated


def test_the_replay_covers_every_refused_read():
    assert len(_REFUSED) == 61
    assert sum(case["refused_reads"] for case in _REFUSED) == 189


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _REFUSED, ids=[f"{c['id']}-{c['kind']}" for c in _REFUSED])
async def test_a_refused_thread_read_now_reads(case: dict[str, Any]):
    graph, hydrated = _replay_graph(case)

    out = await _read_thread(graph, case["id"], hydrated)

    assert out["ok"] is True, out.get("error")
    ids = [r["id"] for r in out["records"]]
    if case["kind"] == "file_in_thread":
        assert ids == ["own-thread-r0"]
        return
    if case["kind"] == "broadcast_reply":
        (ext,) = case["thread_groups"]
        assert ids == [f"{ext}-r{i}" for i in range(case["thread_groups"][ext])]
        return
    # Every other shape reads as the record itself, then the replies of each
    # thread its messages started -- all of them, none invented.
    assert ids[0] == case["id"]
    expected_replies = sorted(
        f"{ext}-r{i}" for ext, count in case["thread_groups"].items() for i in range(count)
    )
    assert sorted(ids[1:]) == expected_replies
    assert len(out["threads"]) == len(case["thread_groups"])
    assert out["note"].startswith("No replies") == (not case["thread_groups"])
