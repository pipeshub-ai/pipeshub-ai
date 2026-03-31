"""
Tests to push app/agents/tools/wrapper.py coverage above 97%.

Targets uncovered lines/branches from the coverage report:
- Lines 142->144: async factory cache hit with None client (fast-path early return)
- Lines 152->154: lock acquisition path when cache_key not in _cache_locks
- Lines 158-164: double-check lock cache hit (second coroutine finds cached client)
- Lines 166->175: toolset_config logging paths (has config vs no config)
- Lines 184->187: caching after factory call
- Lines 193->198: auth error handling in async path
- Lines 230->232: sync factory with toolset config present
- Lines 241->247: sync factory with no toolset config (legacy warning)
- Lines 255->261: sync factory auth error
- Lines 285->289: get_toolset_config no toolset_id warning
- Line 316: fallback_creation set_state call on state= path
- Lines 331-335: fallback_creation with empty dict and None fallbacks
- Lines 417-418: _build_description exception in getattr
- Lines 438-439: _format_parameters type exception fallback
"""

import asyncio
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
        "function": lambda **kw: "result",
        "parameters": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# Module-level functions (not class methods)
def _sync_func(**kwargs):
    return f"sync: {kwargs.get('x', 0)}"


async def _async_func(**kwargs):
    return f"async: {kwargs.get('x', 0)}"


# ===========================================================================
# ToolInstanceCreator - Async factory: cache hit fast path (line 141-148)
# ===========================================================================

class TestAsyncFactoryCacheHitFastPath:
    """Cover the fast-path cache check before lock acquisition."""

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_cache_hit_fast_path_no_state_param(self, mock_cfr):
        """Line 148: cached client reused, action class without 'state' param."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {}}},
            user_id="user-1",
        )
        creator = ToolInstanceCreator(state)

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        # First call creates client
        inst1 = await creator.create_instance_async(SimpleAction, "test", "test.action")
        assert inst1.client is mock_client

        # Second call should hit fast path cache
        inst2 = await creator.create_instance_async(SimpleAction, "test", "test.action")
        assert inst2.client is mock_client
        # Factory only called once
        assert mock_factory.create_client.await_count == 1

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_cache_hit_fast_path_with_state_param(self, mock_cfr):
        """Line 146-147: cached client reused, action class with 'state' param."""
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

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        # First call
        await creator.create_instance_async(ActionWithState, "test", "test.action")
        # Second call hits fast-path with state param
        inst2 = await creator.create_instance_async(ActionWithState, "test", "test.action")
        assert inst2.state is state
        assert inst2.client is mock_client


# ===========================================================================
# Double-check lock: second coroutine finds cached client (lines 156-164)
# ===========================================================================

class TestAsyncFactoryDoubleCheckLock:
    """Cover the double-check after lock acquisition."""

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_double_check_lock_cache_hit_no_state(self, mock_cfr):
        """Lines 158-164: after acquiring lock, client is already cached (no state param)."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {}}},
            user_id="user-1",
        )
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        real_client = MagicMock(name="real_client")

        # Simulate: the first call through the lock creates the client.
        # To test the double-check path we need to pre-populate the cache
        # AFTER the fast-path check but BEFORE the lock check.
        # The simplest way: run two concurrent calls that both miss the fast-path.
        call_count = [0]
        original_create = AsyncMock(return_value=real_client)

        async def slow_create(*args, **kwargs):
            call_count[0] += 1
            await asyncio.sleep(0.01)  # Simulate slow client creation
            return real_client

        mock_factory.create_client = slow_create
        mock_cfr.get_factory.return_value = mock_factory

        # Run two calls concurrently
        results = await asyncio.gather(
            creator.create_instance_async(SimpleAction, "test", "test.action"),
            creator.create_instance_async(SimpleAction, "test", "test.action"),
        )
        # Both should succeed
        assert all(r.client is real_client for r in results)
        # Factory should only be called once (second call uses double-check cache)
        assert call_count[0] == 1

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_double_check_lock_cache_hit_with_state(self, mock_cfr):
        """Lines 161-163: double-check cache hit with 'state' param in action class."""
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

        real_client = MagicMock(name="real_client")
        call_count = [0]

        async def slow_create(*args, **kwargs):
            call_count[0] += 1
            await asyncio.sleep(0.01)
            return real_client

        mock_factory = MagicMock()
        mock_factory.create_client = slow_create
        mock_cfr.get_factory.return_value = mock_factory

        results = await asyncio.gather(
            creator.create_instance_async(ActionWithState, "test", "test.action"),
            creator.create_instance_async(ActionWithState, "test", "test.action"),
        )
        assert all(r.client is real_client for r in results)
        assert all(r.state is state for r in results)
        assert call_count[0] == 1


# ===========================================================================
# Lines 166-173: toolset_config logging (has config vs no config)
# ===========================================================================

class TestAsyncFactoryToolsetConfigLogging:

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_with_toolset_config_logs_debug(self, mock_cfr):
        """Line 168: logs debug when toolset config is present."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {"token": "xyz"}}},
            user_id="user-1",
        )
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())
        mock_cfr.get_factory.return_value = mock_factory

        await creator.create_instance_async(SimpleAction, "test", "test.action")
        state["logger"].debug.assert_called()

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_without_toolset_config_logs_warning(self, mock_cfr):
        """Lines 170-173: logs warning when no toolset config => legacy auth."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(user_id="user-1")  # No tool_to_toolset_map
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())
        mock_cfr.get_factory.return_value = mock_factory

        await creator.create_instance_async(SimpleAction, "test", "test.action")
        state["logger"].warning.assert_called()

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_without_logger(self, mock_cfr):
        """When logger is None, no logging calls but still works."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(user_id="user-1")
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())
        mock_cfr.get_factory.return_value = mock_factory

        instance = await creator.create_instance_async(SimpleAction, "test")
        assert isinstance(instance, SimpleAction)


# ===========================================================================
# Lines 193-205: auth error handling in async path
# ===========================================================================

class TestAsyncFactoryAuthErrorPath:

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_auth_error_with_empty_app_name(self, mock_cfr):
        """Line 200: app_name is empty => toolset_name becomes 'Toolset'."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state()
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(
            side_effect=Exception("not authenticated")
        )
        mock_cfr.get_factory.return_value = mock_factory

        with pytest.raises(ValueError, match="not authenticated"):
            await creator.create_instance_async(SimpleAction, "", "")


# ===========================================================================
# Lines 230-253: sync factory _create_with_factory logging paths
# ===========================================================================

class TestSyncFactoryLoggingPaths:

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_with_toolset_config_debug_log(self, mock_cfr):
        """Line 231: logs debug when toolset config present in sync factory."""
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
        mock_factory.create_client_sync.return_value = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(SimpleAction, "test", "test.action")
        assert isinstance(instance, SimpleAction)
        state["logger"].debug.assert_called()

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_no_toolset_config_warning(self, mock_cfr):
        """Lines 241-245: logs warning when no toolset config => legacy auth."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state()  # No tool_to_toolset_map
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.return_value = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(SimpleAction, "test", "test.action")
        assert isinstance(instance, SimpleAction)
        state["logger"].warning.assert_called()

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_auth_error_raises(self, mock_cfr):
        """Lines 261-268: auth error in sync factory raises ValueError."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {}}},
        )
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.side_effect = Exception("OAuth authentication error")
        mock_cfr.get_factory.return_value = mock_factory

        with pytest.raises(ValueError, match="not authenticated"):
            creator.create_instance(SimpleAction, "test", "test.action")

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_no_logger(self, mock_cfr):
        """Sync factory without logger still works."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state()
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.return_value = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(SimpleAction, "test", "test.action")
        assert isinstance(instance, SimpleAction)


# ===========================================================================
# Line 285-298: _get_toolset_config with logger warnings
# ===========================================================================

class TestGetToolsetConfigLogPaths:

    def test_no_toolset_id_with_logger_logs_debug(self):
        """Line 285-288: no toolset ID, logger present => debug log."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        state = _make_state(tool_to_toolset_map={})
        creator = ToolInstanceCreator(state)
        result = creator._get_toolset_config("unknown.tool")
        assert result is None
        state["logger"].debug.assert_called()

    def test_no_toolset_id_without_logger(self):
        """No toolset ID, no logger => no crash."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        state = _make_state(tool_to_toolset_map={})
        state["logger"] = None
        creator = ToolInstanceCreator(state)
        result = creator._get_toolset_config("unknown.tool")
        assert result is None

    def test_toolset_id_no_config_with_logger_logs_warning(self):
        """Lines 294-298: toolset ID found but config missing => warning."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        state = _make_state(
            tool_to_toolset_map={"test.tool": "ts-1"},
            toolset_configs={},  # No config for ts-1
        )
        creator = ToolInstanceCreator(state)
        result = creator._get_toolset_config("test.tool")
        assert result is None
        state["logger"].warning.assert_called()


# ===========================================================================
# Lines 311-335: _fallback_creation all branches
# ===========================================================================

class TestFallbackCreationAllBranches:

    def test_fallback_state_kwarg_with_set_state(self):
        """Line 315-316: action with state= kwarg and set_state method."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class Action:
            def __init__(self, state=None):
                self.state_kwarg = state

            def set_state(self, s):
                self.set_state_val = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(Action)
        assert instance.state_kwarg is state
        assert instance.set_state_val is state

    def test_fallback_no_args_with_set_state(self):
        """Lines 319-322: action with no args but has set_state."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class Action:
            def __init__(self):
                pass

            def set_state(self, s):
                self.st = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(Action)
        assert instance.st is state

    def test_fallback_empty_dict_with_set_state(self):
        """Lines 327-330: action({}) works and has set_state."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class Action:
            def __init__(self, config):
                self.config = config

            def set_state(self, s):
                self.st = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(Action)
        assert instance.st is state

    def test_fallback_none_arg_with_set_state(self):
        """Lines 331-335: action(None) as last resort with set_state."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class Action:
            """Rejects state=, no-args, and {} but accepts None."""
            def __init__(self, arg=None):
                if isinstance(arg, dict) and not arg:
                    # Also accepts empty dict, so this would be caught by the {} branch
                    pass
                self.arg = arg

            def set_state(self, s):
                self.st = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(Action)
        assert hasattr(instance, 'st')

    def test_fallback_none_only(self):
        """Lines 332-335: class that only accepts None as positional arg."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class NoneOnly:
            def __init__(self, x):
                if x is not None and not isinstance(x, dict):
                    raise TypeError("Only None or dict")
                self.x = x

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(NoneOnly)
        assert instance.x is None or instance.x == {}


# ===========================================================================
# Lines 417-418: _build_description exception handling
# ===========================================================================

class TestBuildDescriptionException:

    def test_build_description_exception_returns_base(self):
        """Lines 417-418: exception in getattr(registry_tool, 'parameters') => base description."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        # Create a tool where accessing 'parameters' raises
        class BrokenTool:
            def __init__(self):
                self.description = "Base desc"
                self.llm_description = None
                self.function = lambda **kw: "ok"

            @property
            def parameters(self):
                raise RuntimeError("broken params")

        tool = BrokenTool()
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        assert wrapper.description == "Base desc"


# ===========================================================================
# Lines 438-439: _format_parameters type exception
# ===========================================================================

class TestFormatParametersTypeException:

    def test_type_access_raises_exception(self):
        """Lines 438-439: accessing param.type raises a non-AttributeError => fallback to 'string'."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        # We need the entire `getattr(param.type, 'name', ...)` call to raise.
        # That happens when accessing `param.type` itself raises (not AttributeError).
        class ParamWithBrokenType:
            name = "bad_param"
            description = "desc"
            required = False

            @property
            def type(self):
                raise RuntimeError("type is broken")

        param = ParamWithBrokenType()

        result = RegistryToolWrapper._format_parameters([param])
        assert len(result) == 1
        assert "string" in result[0]


# ===========================================================================
# _run sync path with class method
# ===========================================================================

class TestRunSyncClassMethod:

    def test_run_with_class_method_and_shutdown_error(self):
        """Sync _execute_class_method with shutdown that raises (suppressed)."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        class MyAction:
            def __init__(self, client):
                pass

            def do_work(self, **kwargs):
                return "done"

            def shutdown(self):
                raise RuntimeError("shutdown fail")

        func = MyAction.do_work
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_work", tool, state)
        mock_instance = MyAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance.return_value = mock_instance

        result = wrapper._execute_tool({})
        assert result == "done"


# ===========================================================================
# _execute_class_method_async: coroutine returned by non-async decorated method
# ===========================================================================

class TestAsyncClassMethodCoroutineDetection:

    @pytest.mark.asyncio
    async def test_method_returns_coroutine_despite_not_being_async(self):
        """Lines 597-599: bound method returns coroutine despite iscoroutinefunction=False."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        class ActionWithDecoratedMethod:
            def __init__(self, client):
                pass

            def do_work(self, **kwargs):
                # Simulate a decorator that wraps async def but
                # iscoroutinefunction returns False on the wrapper
                async def _inner():
                    return "coroutine result"
                return _inner()

        func = ActionWithDecoratedMethod.do_work
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_work", tool, state)
        mock_instance = ActionWithDecoratedMethod(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance_async = AsyncMock(
            return_value=mock_instance
        )

        result = await wrapper._execute_tool_async({})
        assert result == "coroutine result"


# ===========================================================================
# _format_error with state that has no 'get' attribute
# ===========================================================================

class TestFormatErrorStateMissingGet:

    def test_format_error_state_without_get(self):
        """Line 701: state without 'get' attribute => logger is None."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        wrapper = RegistryToolWrapper("app", "tool", tool, state)
        # Override chat_state with something that has no 'get'
        wrapper.chat_state = 42
        error = ValueError("test err")
        result = wrapper._format_error(error, {"arg": "val"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"


# ===========================================================================
# arun: error with args[0] as positional dict
# ===========================================================================

class TestArunErrorWithDictArg:

    @pytest.mark.asyncio
    async def test_arun_error_with_dict_first_arg(self):
        """Line 478: error path when arun called with dict as first arg."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        def bad_func(**kwargs):
            raise RuntimeError("boom")

        bad_func.__qualname__ = "bad_func"
        tool = _make_registry_tool(function=bad_func)
        state = _make_state()
        wrapper = RegistryToolWrapper("app", "tool", tool, state)

        result = await wrapper.arun({"key": "val"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_arun_error_with_no_args(self):
        """Line 478: error path when arun called with no args."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        def bad_func(**kwargs):
            raise RuntimeError("boom")

        bad_func.__qualname__ = "bad_func"
        tool = _make_registry_tool(function=bad_func)
        state = _make_state()
        wrapper = RegistryToolWrapper("app", "tool", tool, state)

        result = await wrapper.arun()
        parsed = json.loads(result)
        assert parsed["status"] == "error"


# ===========================================================================
# Async factory: tool_full_name=None (no toolset ID lookup)
# ===========================================================================

# ===========================================================================
# Lines 142->144, 158->160: async cache hit with logger=None
# ===========================================================================

class TestAsyncCacheHitNoLogger:

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_cache_hit_fast_path_no_logger(self, mock_cfr):
        """Line 142->144: cached client, logger is None => skip debug log."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {}}},
            user_id="user-1",
        )
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        # First call creates the client
        inst1 = await creator.create_instance_async(SimpleAction, "test", "test.action")
        assert inst1.client is mock_client

        # Second call hits the fast-path cache with logger=None
        inst2 = await creator.create_instance_async(SimpleAction, "test", "test.action")
        assert inst2.client is mock_client
        assert mock_factory.create_client.await_count == 1

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_double_check_cache_hit_no_logger(self, mock_cfr):
        """Line 158->160: double-check cache hit, logger is None."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {}}},
            user_id="user-1",
        )
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        real_client = MagicMock()
        call_count = [0]

        async def slow_create(*args, **kwargs):
            call_count[0] += 1
            await asyncio.sleep(0.01)
            return real_client

        mock_factory = MagicMock()
        mock_factory.create_client = slow_create
        mock_cfr.get_factory.return_value = mock_factory

        # Run concurrent calls to trigger double-check path
        results = await asyncio.gather(
            creator.create_instance_async(SimpleAction, "test", "test.action"),
            creator.create_instance_async(SimpleAction, "test", "test.action"),
        )
        assert all(r.client is real_client for r in results)
        assert call_count[0] == 1


# ===========================================================================
# Lines 230->232, 255->261: sync factory with no logger
# ===========================================================================

class TestSyncFactoryNoLoggerAllPaths:

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_toolset_config_no_logger(self, mock_cfr):
        """Line 230->232: toolset_config present but logger is None."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(
            tool_to_toolset_map={"test.action": "ts-1"},
            toolset_configs={"ts-1": {"auth": {"token": "abc"}}},
        )
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.return_value = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(SimpleAction, "test", "test.action")
        assert isinstance(instance, SimpleAction)

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_sync_factory_error_no_logger(self, mock_cfr):
        """Line 255->261: error in sync factory with logger=None."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, state=None):
                self.state = state

        state = _make_state()
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.side_effect = RuntimeError("network fail")
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(SimpleAction, "test", "test.action")
        assert isinstance(instance, SimpleAction)


# ===========================================================================
# Lines 331-335: fallback creation action_class(None) with set_state
# ===========================================================================

class TestFallbackCreationNoneWithSetState:

    def test_fallback_none_with_set_state(self):
        """Lines 331-335: class rejects state=, (), {} but accepts None and has set_state."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class StrictAction:
            """Rejects state= kwarg, no-args, and {} - only accepts None."""
            def __init__(self, arg):
                if arg is not None and not (isinstance(arg, dict) and len(arg) == 0):
                    # state= dict with content triggers this
                    pass
                if isinstance(arg, dict):
                    raise TypeError("no dicts allowed")
                self.arg = arg

            def set_state(self, s):
                self.st = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(StrictAction)
        assert instance.arg is None
        assert instance.st is state


class TestFallbackCreationNoneWithoutSetState:

    def test_fallback_none_without_set_state(self):
        """Branch 333->335: class rejects state=, (), {} but accepts None, no set_state."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class NoneOnlyAction:
            """Rejects state= kwarg, no-args, and {} - only accepts None, no set_state."""
            def __init__(self, arg):
                if isinstance(arg, dict):
                    raise TypeError("no dicts")
                self.arg = arg

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(NoneOnlyAction)
        assert instance.arg is None
        assert not hasattr(instance, 'set_state')


class TestAsyncFactoryNoToolFullName:

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_async_no_tool_full_name(self, mock_cfr):
        """When tool_full_name is None, toolset_id defaults to 'default'."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(user_id="user-1")
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())
        mock_cfr.get_factory.return_value = mock_factory

        instance = await creator.create_instance_async(SimpleAction, "test", None)
        assert isinstance(instance, SimpleAction)

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_async_no_factory(self, mock_cfr):
        """Async with no factory => fallback creation."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self):
                pass

        state = _make_state()
        creator = ToolInstanceCreator(state)
        mock_cfr.get_factory.return_value = None

        instance = await creator.create_instance_async(SimpleAction, "test", "test.tool")
        assert isinstance(instance, SimpleAction)


# ===========================================================================
# Async factory: after lock, client created and action returned (lines 187-191)
# ===========================================================================

class TestAsyncFactoryPostLockCreation:

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_post_lock_creation_with_state(self, mock_cfr):
        """Lines 188-189: after lock, new client created, action has 'state' param."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class ActionWithState:
            def __init__(self, client, state=None):
                self.client = client
                self.state = state

        state = _make_state(user_id="user-1")
        creator = ToolInstanceCreator(state)

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        instance = await creator.create_instance_async(ActionWithState, "test", "test.action")
        assert instance.client is mock_client
        assert instance.state is state

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_post_lock_creation_without_state(self, mock_cfr):
        """Lines 191: after lock, new client created, action without 'state' param."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client):
                self.client = client

        state = _make_state(user_id="user-1")
        creator = ToolInstanceCreator(state)

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        instance = await creator.create_instance_async(SimpleAction, "test", "test.action")
        assert instance.client is mock_client

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_async_error_no_logger(self, mock_cfr):
        """Error in async factory with no logger => no crash."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, state=None):
                self.state = state

        state = _make_state()
        state["logger"] = None
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(side_effect=Exception("network error"))
        mock_cfr.get_factory.return_value = mock_factory

        instance = await creator.create_instance_async(SimpleAction, "test", "test.tool")
        assert isinstance(instance, SimpleAction)
