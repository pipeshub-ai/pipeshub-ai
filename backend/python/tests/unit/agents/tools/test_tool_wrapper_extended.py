"""
Extended tests for app/agents/tools/wrapper.py targeting uncovered lines.
Covers: _execute_class_method_async with async shutdown, sync _run, _execute_tool,
async/sync class method execution, error handling, factory caching, etc.
"""

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_state(**extra):
    """Create a minimal ChatState-like dict."""
    retrieval_service = MagicMock()
    retrieval_service.config_service = MagicMock()
    state = {
        "retrieval_service": retrieval_service,
        "logger": MagicMock(),
        **extra,
    }
    return state


def _make_registry_tool(**kwargs):
    """Create a mock registry tool."""
    defaults = {
        "description": "A test tool",
        "function": _standalone_func,
        "parameters": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# Module-level standalone functions (not class methods)
def _standalone_func(**kwargs):
    return f"Hello {kwargs.get('name', 'world')}"


def _standalone_tuple_func(**kwargs):
    return (True, "Success")


def _standalone_bad_func(**kwargs):
    raise ValueError("Something went wrong")


async def _standalone_async_func(**kwargs):
    return f"got {kwargs.get('x', 0)}"


async def _standalone_bad_async_func(**kwargs):
    raise RuntimeError("Boom")


# ===================================================================
# RegistryToolWrapper._run (sync path)
# ===================================================================


class TestRegistryToolWrapperSyncRun:
    """Cover _run method for sync execution."""

    def test_run_standalone_function(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "greet", tool, state)
        result = wrapper._run(name="Alice")
        assert "Hello Alice" in result

    def test_run_returns_tuple(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_tuple_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "op", tool, state)
        result = wrapper._run()
        assert isinstance(result, tuple)
        assert result[0] is True

    def test_run_error_returns_json(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_bad_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "fail", tool, state)
        result = wrapper._run()
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Something went wrong" in parsed["message"]


# ===================================================================
# RegistryToolWrapper._execute_class_method (sync)
# ===================================================================


class TestExecuteClassMethodSync:
    """Cover _execute_class_method including shutdown."""

    def test_execute_class_method_with_shutdown(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        class MyAction:
            def __init__(self, client):
                pass

            def do_something(self, **kwargs):
                return "done"

            def shutdown(self):
                pass

        func = MyAction.do_something
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_something", tool, state)

        mock_instance = MyAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance.return_value = mock_instance

        result = wrapper._execute_tool({})
        assert result == "done"

    def test_execute_class_method_failure_raises_runtime_error(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        class FailAction:
            def __init__(self, client):
                pass

            def fail_method(self, **kwargs):
                raise ValueError("Fail")

        func = FailAction.fail_method
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "fail_method", tool, state)

        mock_instance = FailAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance.return_value = mock_instance

        with pytest.raises(RuntimeError, match="Failed to execute"):
            wrapper._execute_tool({})


# ===================================================================
# RegistryToolWrapper._execute_class_method_async
# ===================================================================


class TestExecuteClassMethodAsync:
    """Cover _execute_class_method_async including async shutdown."""

    @pytest.mark.asyncio
    async def test_async_class_method_with_async_shutdown(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        class MyAsyncAction:
            def __init__(self, client):
                pass

            async def do_work(self, **kwargs):
                return "async done"

            async def shutdown(self):
                pass

        func = MyAsyncAction.do_work
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_work", tool, state)

        mock_instance = MyAsyncAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance_async = AsyncMock(
            return_value=mock_instance
        )

        result = await wrapper._execute_tool_async({})
        assert result == "async done"

    @pytest.mark.asyncio
    async def test_async_class_method_with_sync_shutdown(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        class SyncShutdownAction:
            def __init__(self, client):
                pass

            async def do_work(self, **kwargs):
                return "result"

            def shutdown(self):
                pass

        func = SyncShutdownAction.do_work
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_work", tool, state)

        mock_instance = SyncShutdownAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance_async = AsyncMock(
            return_value=mock_instance
        )

        result = await wrapper._execute_tool_async({})
        assert result == "result"

    @pytest.mark.asyncio
    async def test_async_class_method_failure_raises_runtime_error(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        class BrokenAction:
            def __init__(self, client):
                pass

            async def broken(self, **kwargs):
                raise ConnectionError("Network error")

        func = BrokenAction.broken
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "broken", tool, state)

        mock_instance = BrokenAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance_async = AsyncMock(
            return_value=mock_instance
        )

        with pytest.raises(RuntimeError, match="Failed to execute"):
            await wrapper._execute_tool_async({})

    @pytest.mark.asyncio
    async def test_execute_standalone_sync_function(self):
        """Non-class method, non-async function."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper._execute_tool_async({})
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_execute_standalone_async_function(self):
        """Non-class method, async function."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_async_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper._execute_tool_async({})
        assert result == "got 0"


# ===================================================================
# ToolInstanceCreator — async factory caching
# ===================================================================


class TestToolInstanceCreatorAsyncCaching:
    """Cover client caching and lock behavior in _create_with_factory_async."""

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_cached_client_reused_with_state_param(self, mock_cfr):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class ActionWithState:
            def __init__(self, client, state=None):
                self.client = client
                self.state = state

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {}}},
            user_id="user-1",
        )
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        # First call creates client
        instance1 = await creator.create_instance_async(
            ActionWithState, "test", "test.action"
        )
        assert instance1.client == mock_client

        # Second call reuses cached client
        instance2 = await creator.create_instance_async(
            ActionWithState, "test", "test.action"
        )
        assert instance2.client == mock_client
        # Factory should only be called once
        assert mock_factory.create_client.await_count == 1

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_async_factory_no_toolset_config(self, mock_cfr):
        """create_instance_async with no toolset config => legacy fallback."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state()
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        instance = await creator.create_instance_async(
            SimpleAction, "test", "test.unknown_tool"
        )
        assert instance.client == mock_client

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_async_factory_auth_error_raises_valueerror(self, mock_cfr):
        """Authentication error in async factory raises ValueError."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state()
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(
            side_effect=ValueError("OAuth authentication failed")
        )
        mock_cfr.get_factory.return_value = mock_factory

        with pytest.raises(ValueError, match="not authenticated"):
            await creator.create_instance_async(
                SimpleAction, "test", "test.tool"
            )


# ===================================================================
# ToolInstanceCreator — sync factory
# ===================================================================


class TestToolInstanceCreatorSyncFactory:
    """Cover _create_with_factory with toolset config."""

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_with_toolset_config(self, mock_cfr):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {"token": "abc"}}},
        )
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_factory.create_client_sync.return_value = mock_client
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(
            SimpleAction, "test", "test.action"
        )
        assert instance.client == mock_client

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_no_toolset_config_falls_back(self, mock_cfr):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state()
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_factory.create_client_sync.return_value = mock_client
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(
            SimpleAction, "test", "test.unknown"
        )
        assert instance.client == mock_client


# ===================================================================
# RegistryToolWrapper.arun edge cases
# ===================================================================


class TestArunEdgeCases:
    """Cover arun with kwargs, error formatting."""

    @pytest.mark.asyncio
    async def test_arun_with_kwargs_only(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_async_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper.arun(x=42)
        assert "42" in str(result)

    @pytest.mark.asyncio
    async def test_arun_error_with_kwargs(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_bad_async_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper.arun(x=1)
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_arun_error_with_no_args(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_standalone_bad_async_func)
        state = _make_state()

        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper.arun()
        parsed = json.loads(result)
        assert parsed["status"] == "error"


# ===================================================================
# _format_error with no logger
# ===================================================================


class TestFormatErrorNoLogger:
    """Cover _format_error when state has no logger."""

    def test_format_error_no_logger_in_state(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        state["logger"] = None

        wrapper = RegistryToolWrapper("test", "func", tool, state)
        error_result = wrapper._format_error(
            ValueError("test error"), {"arg": "value"}
        )
        parsed = json.loads(error_result)
        assert parsed["status"] == "error"
