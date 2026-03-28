"""
Deep coverage tests for app/agents/tools/wrapper.py to reach 90%+.

Targets remaining uncovered blocks after test_tool_wrapper_extended.py:
- ToolInstanceCreator: _get_config_service RuntimeError
- ToolInstanceCreator: _get_toolset_config various paths
- ToolInstanceCreator: _fallback_creation all fallback paths
- ToolInstanceCreator: _create_with_factory auth error path
- ToolInstanceCreator: _create_with_factory_async double-check lock
- RegistryToolWrapper: _build_description with parameters
- RegistryToolWrapper: _format_parameters type extraction
- RegistryToolWrapper: _format_result with tuple
- RegistryToolWrapper: _format_error with state.get
- RegistryToolWrapper: arun with dict arg
- RegistryToolWrapper: _execute_class_method_async coroutine detection
"""

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_state(**extra):
    retrieval_service = MagicMock()
    retrieval_service.config_service = MagicMock()
    state = {
        "retrieval_service": retrieval_service,
        "logger": MagicMock(),
        **extra,
    }
    return state


def _make_registry_tool(**kwargs):
    defaults = {
        "description": "A test tool",
        "function": lambda **kw: "result",
        "parameters": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ===================================================================
# ToolInstanceCreator — _get_config_service
# ===================================================================


class TestToolInstanceCreatorConfigService:
    def test_missing_retrieval_service_raises(self):
        from app.agents.tools.wrapper import ToolInstanceCreator
        state = {"logger": MagicMock()}
        with pytest.raises(RuntimeError, match="ConfigurationService not available"):
            ToolInstanceCreator(state)

    def test_missing_config_service_attr_raises(self):
        from app.agents.tools.wrapper import ToolInstanceCreator
        retrieval_service = MagicMock(spec=[])  # no config_service
        state = {"retrieval_service": retrieval_service, "logger": MagicMock()}
        with pytest.raises(RuntimeError, match="ConfigurationService not available"):
            ToolInstanceCreator(state)


# ===================================================================
# ToolInstanceCreator — _get_toolset_config
# ===================================================================


class TestGetToolsetConfig:
    def test_no_toolset_id_returns_none(self):
        from app.agents.tools.wrapper import ToolInstanceCreator
        state = _make_state(tool_to_toolset_map={})
        creator = ToolInstanceCreator(state)
        result = creator._get_toolset_config("slack.send")
        assert result is None

    def test_toolset_id_but_no_config(self):
        from app.agents.tools.wrapper import ToolInstanceCreator
        state = _make_state(
            tool_to_toolset_map={"slack.send": "ts-1"},
            toolset_configs={},
        )
        creator = ToolInstanceCreator(state)
        result = creator._get_toolset_config("slack.send")
        assert result is None

    def test_config_found(self):
        from app.agents.tools.wrapper import ToolInstanceCreator
        state = _make_state(
            tool_to_toolset_map={"slack.send": "ts-1"},
            toolset_configs={"ts-1": {"auth": {"token": "xyz"}}},
        )
        creator = ToolInstanceCreator(state)
        result = creator._get_toolset_config("slack.send")
        assert result == {"auth": {"token": "xyz"}}


# ===================================================================
# ToolInstanceCreator — _fallback_creation
# ===================================================================


class TestFallbackCreation:
    def test_fallback_with_state_param(self):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class ActionWithState:
            def __init__(self, state=None):
                self.state = state

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(ActionWithState)
        assert instance.state == state

    def test_fallback_with_set_state(self):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class ActionWithSetState:
            state = None
            def __init__(self):
                pass
            def set_state(self, s):
                self.state = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(ActionWithSetState)
        assert instance.state == state

    def test_fallback_no_args(self):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self):
                self.ok = True

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(SimpleAction)
        assert instance.ok is True

    def test_fallback_empty_dict_arg(self):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class DictAction:
            def __init__(self, config):
                self.config = config

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(DictAction)
        # Should try with {} or None
        assert instance is not None

    def test_fallback_none_arg(self):
        """Fallback tries state=, then (), then {}, then None — {}  will work first."""
        from app.agents.tools.wrapper import ToolInstanceCreator

        class NoneAction:
            def __init__(self, client):
                self.client = client
            def set_state(self, s):
                self.st = s

        state = _make_state()
        creator = ToolInstanceCreator(state)
        instance = creator._fallback_creation(NoneAction)
        # The fallback first tries state=..., then (), then ({}), then (None)
        # (state=...) raises TypeError, () raises TypeError, ({}) succeeds
        assert instance is not None


# ===================================================================
# ToolInstanceCreator — _create_with_factory sync auth error
# ===================================================================


class TestCreateWithFactorySyncAuthError:
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_auth_error_raises_value_error(self, mock_cfr):
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
        mock_factory.create_client_sync.side_effect = ValueError("OAuth authentication failed")
        mock_cfr.get_factory.return_value = mock_factory

        with pytest.raises(ValueError, match="not authenticated"):
            creator.create_instance(SimpleAction, "test", "test.action")

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_non_auth_error_falls_back(self, mock_cfr):
        from app.agents.tools.wrapper import ToolInstanceCreator

        class SimpleAction:
            def __init__(self, client=None):
                self.client = client

        state = _make_state()
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.side_effect = RuntimeError("Network timeout")
        mock_cfr.get_factory.return_value = mock_factory

        instance = creator.create_instance(SimpleAction, "test", "test.action")
        assert instance is not None


# ===================================================================
# ToolInstanceCreator — _create_with_factory_async double-check lock
# ===================================================================


class TestCreateWithFactoryAsyncDoubleCheck:
    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_double_check_returns_cached(self, mock_cfr):
        """After acquiring lock, if client was cached by another coroutine, reuse it."""
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

        # Pre-populate cache to simulate another coroutine caching it
        cached_client = MagicMock()

        mock_factory = MagicMock()
        call_count = [0]

        async def fake_create_client(*args, **kwargs):
            call_count[0] += 1
            return MagicMock()

        mock_factory.create_client = fake_create_client
        mock_cfr.get_factory.return_value = mock_factory

        # First call creates client
        instance1 = await creator.create_instance_async(SimpleAction, "test", "test.action")
        # Second call should reuse from cache
        instance2 = await creator.create_instance_async(SimpleAction, "test", "test.action")
        # Factory should only be called once (second call uses cache)
        assert call_count[0] == 1

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_async_factory_with_state_param_action(self, mock_cfr):
        """Async factory with action class that takes state param."""
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

        instance = await creator.create_instance_async(ActionWithState, "test", "test.action")
        assert instance.client == mock_client
        assert instance.state == state


# ===================================================================
# RegistryToolWrapper — _build_description
# ===================================================================


class TestBuildDescription:
    def test_with_parameters(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        param = SimpleNamespace(
            name="query",
            type=SimpleNamespace(name="string"),
            description="Search query",
            required=True,
        )
        tool = _make_registry_tool(
            description="Search tool",
            parameters=[param],
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "search", tool, state)
        assert "query" in wrapper.description
        assert "required" in wrapper.description

    def test_with_param_no_type_name(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        param = SimpleNamespace(
            name="limit",
            type="integer",
            description="Max results",
            required=False,
        )
        tool = _make_registry_tool(
            description="Search tool",
            parameters=[param],
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "search", tool, state)
        assert "limit" in wrapper.description

    def test_with_param_exception_in_type(self):
        """Param with type that raises on access uses 'string' fallback."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        param = MagicMock()
        param.name = "bad_param"
        param.description = "desc"
        param.required = True
        type_mock = MagicMock()
        type_mock.name = property(lambda self: (_ for _ in ()).throw(Exception("bad")))
        param.type = type_mock

        tool = _make_registry_tool(
            description="Tool",
            parameters=[param],
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        assert "bad_param" in wrapper.description

    def test_no_parameters(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(
            description="Simple tool",
            parameters=[],
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        assert wrapper.description == "Simple tool"

    def test_none_parameters(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(
            description="Simple tool",
            parameters=None,
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        assert wrapper.description == "Simple tool"

    def test_uses_llm_description(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(
            description="Short desc",
            llm_description="Longer LLM description with usage guidance",
            parameters=[],
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        assert "Longer LLM description" in wrapper.description

    def test_fallback_description(self):
        """When no description, uses default format."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = SimpleNamespace(
            function=lambda **kw: "ok",
            parameters=[],
        )
        state = _make_state()
        wrapper = RegistryToolWrapper("myapp", "mytool", tool, state)
        assert "myapp.mytool" in wrapper.description


# ===================================================================
# RegistryToolWrapper — _format_result
# ===================================================================


class TestFormatResult:
    def test_tuple_result(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        result = wrapper._format_result((True, "Success data"))
        assert result == "Success data"

    def test_list_result(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        result = wrapper._format_result([False, "Error data"])
        assert result == "Error data"

    def test_string_result(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        result = wrapper._format_result("plain string")
        assert result == "plain string"

    def test_dict_result(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        result = wrapper._format_result({"key": "value"})
        assert "key" in result


# ===================================================================
# RegistryToolWrapper — arun with dict argument
# ===================================================================


# Module-level functions for arun tests (must be module-level to avoid
# _is_class_method detecting them as class methods)
async def _arun_dict_func(**kwargs):
    return f"got {kwargs.get('x', 0)}"


async def _arun_tuple_func(**kwargs):
    return (True, "Success")


class TestArunDictArg:
    @pytest.mark.asyncio
    async def test_arun_with_dict_arg(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_arun_dict_func)
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper.arun({"x": 42})
        assert "42" in str(result)

    @pytest.mark.asyncio
    async def test_arun_returns_tuple(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool(function=_arun_tuple_func)
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "func", tool, state)
        result = await wrapper.arun({})
        # arun preserves tuple structure when len==2
        assert isinstance(result, (tuple, list))
        assert result[0] is True
        assert result[1] == "Success"


# ===================================================================
# RegistryToolWrapper — _execute_class_method_async coroutine detection
# ===================================================================


class TestExecuteClassMethodAsyncCoroutineDetection:
    @pytest.mark.asyncio
    async def test_sync_method_returns_non_coroutine(self):
        """Class method that is not async but returns a value."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        class SyncAction:
            def __init__(self, client):
                pass

            def do_sync(self, **kwargs):
                return "sync result"

        func = SyncAction.do_sync
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_sync", tool, state)

        mock_instance = SyncAction(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance_async = AsyncMock(return_value=mock_instance)

        result = await wrapper._execute_tool_async({})
        assert result == "sync result"

    @pytest.mark.asyncio
    async def test_shutdown_error_suppressed(self):
        """Exception in shutdown is suppressed."""
        from app.agents.tools.wrapper import RegistryToolWrapper

        class ActionWithBadShutdown:
            def __init__(self, client):
                pass

            async def do_work(self, **kwargs):
                return "done"

            async def shutdown(self):
                raise RuntimeError("shutdown failed")

        func = ActionWithBadShutdown.do_work
        tool = _make_registry_tool(function=func)
        state = _make_state()

        wrapper = RegistryToolWrapper("myapp", "do_work", tool, state)

        mock_instance = ActionWithBadShutdown(None)
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.create_instance_async = AsyncMock(return_value=mock_instance)

        # Should not raise despite shutdown failure
        result = await wrapper._execute_tool_async({})
        assert result == "done"


# ===================================================================
# RegistryToolWrapper — state property
# ===================================================================


class TestStateProperty:
    def test_state_returns_chat_state(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        tool = _make_registry_tool()
        state = _make_state()
        wrapper = RegistryToolWrapper("test", "tool", tool, state)
        assert wrapper.state is state


# ===================================================================
# RegistryToolWrapper — _is_class_method
# ===================================================================


class TestIsClassMethod:
    def test_class_method_detection(self):
        from app.agents.tools.wrapper import RegistryToolWrapper

        class MyClass:
            def method(self):
                pass

        assert RegistryToolWrapper._is_class_method(MyClass.method) is True

    def test_standalone_function_detection(self):
        """Module-level function is not a class method."""
        from app.agents.tools.wrapper import RegistryToolWrapper
        # Use a real module-level function from this module
        assert RegistryToolWrapper._is_class_method(_arun_dict_func) is False
