"""`PipesHubToolLoader` (`app/agents/agent_loop/tool_loader.py`) — dynamic
tool construction (`_build_dynamic_tools`), specifically the `WebToolAdapter`
wiring used to restore citation IDs for `web_search`/`fetch_url`.

Note: `PipesHubToolLoader.load()`'s connector-toolset registration path
(`ToolInstanceCreator` + `AgentLoopToolsetBuilder` + `get_toolset_registry()`)
has no dedicated unit coverage here — it needs a fair amount of scaffolding
to exercise in isolation (toolset registry state, per-connector client
factories). Covered indirectly today via the chat-mode integration tests.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_loader import PipesHubToolLoader, _build_dynamic_tools
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from collections.abc import Iterator


def _make_context() -> AgentContext:
    return AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="u@example.com",
        logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
    )


class _NoArgs(BaseModel):
    pass


class TestBuildDynamicToolsWebAdapterWiring:
    """`web_search`/`fetch_url` must be wrapped with `WebToolAdapter` (not
    the plain `PipesHubStructuredToolAdapter`) whenever a web search
    provider is configured — this is what restores Citation IDs; see
    `web_tool_adapter.py`."""

    def test_web_search_and_fetch_url_use_web_tool_adapter(self) -> None:
        context = _make_context()
        context.tool_state["web_search_config"] = {"provider": "duckduckgo", "configuration": {}}

        tools = _build_dynamic_tools(context)

        web_tools = [t for t in tools if t.name in ("dynamic__web_search", "dynamic__fetch_url")]
        assert len(web_tools) == 2
        assert all(isinstance(t, WebToolAdapter) for t in web_tools)

    @staticmethod
    def _web_adapters(settings: dict | None) -> dict[str, WebToolAdapter]:
        context = _make_context()
        context.tool_state["web_search_config"] = {
            "provider": "duckduckgo", "configuration": {}, **({"settings": settings} if settings is not None else {}),
        }
        return {t.name: t for t in _build_dynamic_tools(context) if isinstance(t, WebToolAdapter)}

    @pytest.mark.parametrize("settings", [None, {"includeImages": False, "maxImages": 3}])
    def test_pages_are_read_without_images_unless_an_admin_sends_them(self, settings: dict | None) -> None:
        adapters = self._web_adapters(settings)
        assert all(a._image_loader is None for a in adapters.values())

    async def test_sent_images_are_read_for_fetch_url_through_the_runs_fetch_service(self) -> None:
        from app.web.services import WebFetchService

        adapters = self._web_adapters({"includeImages": True, "maxImages": 4})
        fetch_url, web_search = adapters["dynamic__fetch_url"], adapters["dynamic__web_search"]
        assert web_search._image_loader is None
        assert fetch_url._max_images == 4

        with patch.object(WebFetchService, "fetch_images", AsyncMock(return_value=[None])) as fetch_images:
            assert await fetch_url._image_loader(["https://example.com/a.png"]) == [None]
        urls, caller = fetch_images.await_args.args
        assert urls == ["https://example.com/a.png"]
        assert (caller.org_id, caller.user_id) == ("org-1", "user-1")

    @pytest.mark.parametrize(
        ("settings", "expected"),
        [({"includeImages": True, "maxImages": "many"}, 3), ({"includeImages": True, "maxImages": 200}, 50)],
        ids=["not a number", "past the per-page limit"],
    )
    def test_a_bad_or_large_count_never_costs_the_turn_its_web_tools(self, settings: dict, expected: int) -> None:
        """Building the options sits inside the web tools' try: a raise there dropped web search
        and fetch_url for the whole turn."""
        adapters = self._web_adapters(settings)
        assert set(adapters) == {"dynamic__web_search", "dynamic__fetch_url"}
        assert adapters["dynamic__fetch_url"]._max_images == expected

    def test_no_web_search_config_yields_no_dynamic_web_tools(self) -> None:
        context = _make_context()
        context.tool_state["web_search_config"] = None

        tools = _build_dynamic_tools(context)

        assert not any(t.name in ("dynamic__web_search", "dynamic__fetch_url") for t in tools)

    def test_web_tools_share_the_context_citation_ref_mapper(self) -> None:
        """`_build_dynamic_tools` seeds `tool_state["citation_ref_mapper"]`
        when absent so `fetch_url`'s tiny-ref resolution and `WebToolAdapter`'s
        formatting both close over the SAME mapper instance."""
        context = _make_context()
        context.tool_state["web_search_config"] = {"provider": "duckduckgo", "configuration": {}}
        assert context.tool_state.get("citation_ref_mapper") is None

        _build_dynamic_tools(context)
        assert context.tool_state["citation_ref_mapper"] is not None


class TestOwnFileDownloadLinks:
    """The sequence from a chat: the agent asks for an uploaded PDF's download link, then passes it to
    fetch_url. fetch_url must answer from the same request's state, without fetching anything."""

    async def test_fetch_url_refuses_a_link_the_download_tool_just_handed_out(self) -> None:
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.agents.actions.artifacts.artifacts import ArtifactManager
        from app.config.constants.arangodb import OriginTypes

        context = _make_context()
        context.tool_state["web_search_config"] = {"provider": "duckduckgo", "configuration": {}}
        tools = {t.name: t for t in _build_dynamic_tools(context)}

        link = "https://files-bucket.s3.amazonaws.com/org-1/report.pdf?X-Amz-Expires=3600&X-Amz-Signature=0123abcd"
        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(return_value=SimpleNamespace(
            id="rec-1", org_id="org-1", origin=OriginTypes.UPLOAD, external_record_id="doc-1",
            record_name="report.pdf", mime_type="application/pdf", weburl=None,
        ))
        # The caller's read of the upload, through the graph's record-access surface as the providers answer it.
        access = InMemoryRecordAccess().user("user-1", "org-1").record("rec-1", "org-1", origin="UPLOAD")
        access.grant("rec-1", user="user-1").serve(graph)
        context.tool_state["graph_provider"] = graph
        context.tool_state["blob_store"] = MagicMock(get_download_url=AsyncMock(return_value=link))
        # Toolsets get the request's state dict itself (ToolInstanceCreator), as here.
        manager = ArtifactManager(context.tool_state)
        success, _ = await manager.get_record_download_url(record_id="rec-1")
        assert success is True

        with patch("app.web.services.WebFetchService.fetch", new=AsyncMock()) as fetch:
            output = await tools["dynamic__fetch_url"].execute(url=link)

        fetch.assert_not_awaited()
        # A failed call reaches the model as `error`, the same as any fetch_url failure.
        assert output.success is False
        payload = json.loads(output.error)
        assert payload["error_type"] == "own_file"
        assert "rec-1" in payload["hint"]
        assert not output.sources  # nothing to cite as a web page
        assert not context.tool_state.get("web_records")

    async def test_the_next_turn_cannot_fetch_the_link_from_the_saved_step(self) -> None:
        """A new request has no remembered links; what it gets back is the step as saved."""
        import json
        from unittest.mock import AsyncMock

        from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
        from app.agents.agent_loop.protocol.transcript_collector import (
            TranscriptCollector,
        )

        link = "https://files-bucket.s3.amazonaws.com/org-1/report.pdf?X-Amz-Expires=3600&X-Amz-Signature=0123abcd"
        ctx = RunContext(role_name="pipeshub-agent", model="gpt-4")
        collector = TranscriptCollector()
        await collector.emit(AgentEvent(event_type=EventType.RUN_STARTED, run_context=ctx, payload={}))
        await collector.emit(AgentEvent(event_type=EventType.TOOL_CALL_START, run_context=ctx, payload={
            "tool": "dynamic__fetch_url", "args": {"url": link}, "tool_call_id": "call-1",
        }))
        saved_url = json.loads(collector.parts[0]["args"])["url"]
        assert "0123abcd" not in saved_url

        next_turn = _make_context()
        next_turn.tool_state["web_search_config"] = {"provider": "duckduckgo", "configuration": {}}
        tools = {t.name: t for t in _build_dynamic_tools(next_turn)}
        with patch("app.web.services.WebFetchService.fetch", new=AsyncMock()) as fetch:
            output = await tools["dynamic__fetch_url"].execute(url=saved_url)

        fetch.assert_not_awaited()
        assert output.success is False
        assert json.loads(output.error)["error_type"] == "invalid_url"


class TestSandboxToolsetGate:
    """`coding_sandbox` is `.as_internal()`, so nothing else stops it from
    loading into every chat. With no usable `SANDBOX_MODE` its tools would
    only ever answer with "sandbox unavailable" (or, before the fix, run the
    code in-process), so the loader drops the toolset with one warning."""

    @pytest.fixture
    def _only_coding_sandbox(self) -> Iterator[None]:
        registry = MagicMock()
        registry.get_all_toolsets.return_value = {
            "codingsandbox": {"class": CodingSandbox, "isInternal": True, "provider": "platform", "description": "sandbox"},
        }
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry", return_value=registry,
        ):
            yield

    @pytest.mark.usefixtures("_only_coding_sandbox")
    async def test_unset_mode_skips_sandbox_toolset_with_warning(self, monkeypatch, caplog) -> None:
        from app.utils import logging_fields

        monkeypatch.setattr(logging_fields, "_log_once_last", {})
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        context = _make_context()

        with caplog.at_level(logging.DEBUG):
            registry = await PipesHubToolLoader().load(context)
            await PipesHubToolLoader().load(_make_context())

        assert not any(name.startswith("coding_sandbox") for name in registry.names())
        warnings = [r for r in caplog.records if r.getMessage() == "code_execution.unavailable"]
        assert [r.levelno for r in warnings] == [logging.WARNING]

    @pytest.mark.usefixtures("_only_coding_sandbox")
    async def test_local_mode_without_dev_flag_skips_sandbox_toolset(self, monkeypatch, caplog) -> None:
        from app.utils import logging_fields

        monkeypatch.setattr(logging_fields, "_log_once_last", {})
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.delenv("SANDBOX_ALLOW_LOCAL", raising=False)
        context = _make_context()

        with caplog.at_level(logging.DEBUG):
            registry = await PipesHubToolLoader().load(context)

        assert not any(name.startswith("coding_sandbox") for name in registry.names())
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == ["code_execution.unavailable"]

    @pytest.mark.usefixtures("_only_coding_sandbox")
    async def test_explicit_local_mode_loads_sandbox_toolset(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        context = _make_context()

        registry = await PipesHubToolLoader().load(context)

        assert any(name.startswith("coding_sandbox") for name in registry.names())
