"""Unit tests for AgentContext.cleanup() and
app.agents.agent_loop.instance_creator.close_cached_clients.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.instance_creator import close_cached_clients


def _make_context(**overrides) -> AgentContext:
    defaults = dict(org_id="org-1", user_id="user-1", user_email="u@example.com")
    defaults.update(overrides)
    return AgentContext(**defaults)


class TestAgentContextCleanup:
    async def test_clears_tool_state(self):
        context = _make_context()
        context.tool_state["final_results"] = [1, 2, 3]
        context.tool_state["_client_cache"] = {}

        await context.cleanup()

        assert context.tool_state == {}

    async def test_clears_previous_conversations_and_attachment_blocks(self):
        context = _make_context(
            previous_conversations=[{"role": "user", "content": "hi"}],
            attachment_image_blocks=[{"type": "image_url", "image_url": {"url": "x"}}],
        )

        await context.cleanup()

        assert context.previous_conversations == []
        assert context.attachment_image_blocks == []

    async def test_closes_cached_clients_before_clearing_tool_state(self):
        """`close_cached_clients` needs `tool_state["_client_cache"]`/
        `["_toolset_instances"]` to still be populated when it runs, so it
        must be called BEFORE `tool_state.clear()`, not after."""
        context = _make_context()
        client = AsyncMock()
        client.aclose = AsyncMock()
        context.tool_state["_client_cache"] = {("jira", "default", "user-1"): client}

        await context.cleanup()

        client.aclose.assert_awaited_once()
        assert context.tool_state == {}

    async def test_swallows_client_teardown_errors(self):
        """A broken client's close() must never propagate out of cleanup()
        and fail the request."""
        context = _make_context()
        client = AsyncMock()
        client.aclose = AsyncMock(side_effect=RuntimeError("boom"))
        context.tool_state["_client_cache"] = {("slack", "default", "user-1"): client}

        await context.cleanup()  # must not raise

        assert context.tool_state == {}

    async def test_uses_default_logger_when_context_logger_is_none(self):
        context = _make_context(logger=None)
        await context.cleanup()  # must not raise despite no logger configured


class TestCloseCachedClients:
    async def test_prefers_aclose_over_close_and_shutdown(self):
        client = MagicMock()
        client.aclose = AsyncMock()
        client.close = MagicMock()
        client.shutdown = MagicMock()
        tool_state = {"_client_cache": {"k": client}}

        await close_cached_clients(tool_state)

        client.aclose.assert_awaited_once()
        client.close.assert_not_called()
        client.shutdown.assert_not_called()

    async def test_falls_back_to_sync_close_when_no_aclose(self):
        client = MagicMock(spec=["close"])
        client.close = MagicMock()
        tool_state = {"_client_cache": {"k": client}}

        await close_cached_clients(tool_state)

        client.close.assert_called_once()

    async def test_falls_back_to_sync_shutdown_when_no_aclose_or_close(self):
        client = MagicMock(spec=["shutdown"])
        client.shutdown = MagicMock()
        tool_state = {"_client_cache": {"k": client}}

        await close_cached_clients(tool_state)

        client.shutdown.assert_called_once()

    async def test_client_with_no_close_method_is_left_alone(self):
        client = object()
        tool_state = {"_client_cache": {"k": client}}

        await close_cached_clients(tool_state)  # must not raise

    async def test_closes_every_cached_client_and_toolset_instance(self):
        client_a = AsyncMock()
        client_a.aclose = AsyncMock()
        client_b = AsyncMock()
        client_b.aclose = AsyncMock()
        toolset_instance = MagicMock(spec=["shutdown"])
        toolset_instance.shutdown = MagicMock()

        tool_state = {
            "_client_cache": {"a": client_a, "b": client_b},
            "_toolset_instances": [toolset_instance],
        }

        await close_cached_clients(tool_state)

        client_a.aclose.assert_awaited_once()
        client_b.aclose.assert_awaited_once()
        toolset_instance.shutdown.assert_called_once()

    async def test_clears_both_caches_after_closing(self):
        client = AsyncMock()
        client.aclose = AsyncMock()
        tool_state = {
            "_client_cache": {"a": client},
            "_toolset_instances": [MagicMock(spec=[])],
        }

        await close_cached_clients(tool_state)

        assert tool_state["_client_cache"] == {}
        assert tool_state["_toolset_instances"] == []

    async def test_one_broken_client_does_not_stop_others_from_closing(self):
        broken = AsyncMock()
        broken.aclose = AsyncMock(side_effect=RuntimeError("boom"))
        healthy = AsyncMock()
        healthy.aclose = AsyncMock()
        tool_state = {"_client_cache": {"broken": broken, "healthy": healthy}}

        await close_cached_clients(tool_state)

        broken.aclose.assert_awaited_once()
        healthy.aclose.assert_awaited_once()

    async def test_missing_cache_keys_is_a_no_op(self):
        await close_cached_clients({})  # must not raise

    async def test_sync_shutdown_runs_off_the_event_loop(self, monkeypatch):
        """Sync teardown methods (e.g. `Thread.join()`-based `shutdown()`)
        must run via `asyncio.to_thread`, not block the caller's task
        directly."""
        import app.agents.agent_loop.instance_creator as instance_creator_module

        calls = []
        real_to_thread = instance_creator_module.asyncio.to_thread

        async def _spy_to_thread(fn, *args, **kwargs):
            calls.append(fn)
            return await real_to_thread(fn, *args, **kwargs)

        monkeypatch.setattr(instance_creator_module.asyncio, "to_thread", _spy_to_thread)

        client = MagicMock(spec=["shutdown"])
        client.shutdown = MagicMock()
        tool_state = {"_client_cache": {"k": client}}

        await close_cached_clients(tool_state)

        assert client.shutdown in calls
