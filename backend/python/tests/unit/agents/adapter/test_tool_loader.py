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

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_loader import PipesHubToolLoader, _build_dynamic_tools
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter

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


class TestSandboxToolsetGate:
    """`coding_sandbox` is `.as_internal()`, so nothing else stops it from
    loading into every chat. With no usable `SANDBOX_MODE` its tools would
    only ever answer with "sandbox unavailable" (or, before the fix, run the
    code in-process), so the loader drops the toolset with one warning."""

    @pytest.fixture
    def _only_coding_sandbox(self) -> Iterator[None]:
        registry = MagicMock()
        registry.get_all_toolsets.return_value = {
            "codingsandbox": {"class": CodingSandbox, "isInternal": True, "description": "sandbox"},
        }
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry", return_value=registry,
        ):
            yield

    @pytest.mark.usefixtures("_only_coding_sandbox")
    async def test_unset_mode_skips_sandbox_toolset_with_warning(self, monkeypatch) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        context = _make_context()

        registry = await PipesHubToolLoader().load(context)

        assert not any(name.startswith("coding_sandbox") for name in registry.names())
        warning_calls = [
            c for c in context.tool_state["logger"].warning.call_args_list
            if "Skipping sandbox toolsets" in str(c.args[0])
        ]
        assert len(warning_calls) == 1

    @pytest.mark.usefixtures("_only_coding_sandbox")
    async def test_local_mode_without_dev_flag_skips_sandbox_toolset(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.delenv("SANDBOX_ALLOW_LOCAL", raising=False)
        context = _make_context()

        registry = await PipesHubToolLoader().load(context)

        assert not any(name.startswith("coding_sandbox") for name in registry.names())
        warnings = [str(c.args) for c in context.tool_state["logger"].warning.call_args_list]
        assert any("SANDBOX_ALLOW_LOCAL" in w for w in warnings)

    @pytest.mark.usefixtures("_only_coding_sandbox")
    async def test_explicit_local_mode_loads_sandbox_toolset(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        context = _make_context()

        registry = await PipesHubToolLoader().load(context)

        assert any(name.startswith("coding_sandbox") for name in registry.names())
