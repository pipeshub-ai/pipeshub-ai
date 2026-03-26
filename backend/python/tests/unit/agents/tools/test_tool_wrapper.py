"""Unit tests for app.agents.tools.wrapper — ToolInstanceCreator and RegistryToolWrapper."""

import asyncio
import importlib
import inspect
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level mocking to prevent import failures from missing optional deps.
# The wrapper module's import chain pulls in torch, langchain_qdrant,
# googleapiclient, dependency_injector, fastapi, and many more.
# We install a custom import hook that auto-mocks any module that fails.
# ---------------------------------------------------------------------------

def _mock_module(name: str) -> None:
    """Create a mock module in sys.modules that acts like a package."""
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__file__ = f"<mock {name}>"
    mod.__spec__ = None
    mod.__package__ = name.rsplit(".", 1)[0] if "." in name else name
    mod.__getattr__ = lambda attr: MagicMock()
    sys.modules[name] = mod


def _import_wrapper_with_mocking():
    """Iteratively mock missing modules until the wrapper can be imported."""
    max_attempts = 50
    for _ in range(max_attempts):
        try:
            from app.agents.tools.wrapper import ToolInstanceCreator, RegistryToolWrapper
            return ToolInstanceCreator, RegistryToolWrapper
        except ModuleNotFoundError as e:
            msg = str(e)
            # Extract module name from "No module named 'xxx'"
            if "'" in msg:
                mod_name = msg.split("'")[1]
            else:
                raise
            # Mock the missing module and all parent packages
            parts = mod_name.split(".")
            for i in range(1, len(parts) + 1):
                _mock_module(".".join(parts[:i]))
        except Exception:
            raise
    raise ImportError(f"Could not import wrapper after {max_attempts} attempts")

# Now import the wrapper - missing deps will be auto-mocked
_WRAPPER_AVAILABLE = False
_import_err_msg = ""
try:
    ToolInstanceCreator, RegistryToolWrapper = _import_wrapper_with_mocking()
    _WRAPPER_AVAILABLE = True
except Exception as _exc:
    _import_err_msg = str(_exc)
    ToolInstanceCreator = None
    RegistryToolWrapper = None

pytestmark = pytest.mark.skipif(
    not _WRAPPER_AVAILABLE,
    reason=f"Cannot import wrapper module: {_import_err_msg}",
)


# ---------------------------------------------------------------------------
# ToolInstanceCreator
# ---------------------------------------------------------------------------

class TestToolInstanceCreator:
    def _make_state(self, retrieval_service=None):
        state = {}
        if retrieval_service is not None:
            state["retrieval_service"] = retrieval_service
        state["logger"] = MagicMock()
        return state

    def _make_retrieval_service(self):
        rs = MagicMock()
        rs.config_service = MagicMock()
        return rs

    def test_init_creates_client_cache(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)
        assert "_client_cache" in state
        assert "_client_cache_locks" in state

    def test_init_reuses_existing_cache(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        cache = {"existing": True}
        state["_client_cache"] = cache
        state["_client_cache_locks"] = {}
        creator = ToolInstanceCreator(state)
        assert creator._client_cache is cache

    def test_init_raises_without_retrieval_service(self):
        state = self._make_state()
        with pytest.raises(RuntimeError, match="ConfigurationService not available"):
            ToolInstanceCreator(state)

    def test_init_raises_without_config_service(self):
        rs = MagicMock(spec=[])  # No config_service attribute
        state = self._make_state(rs)
        with pytest.raises(RuntimeError, match="ConfigurationService not available"):
            ToolInstanceCreator(state)

    def test_get_toolset_config_returns_config(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        state["tool_to_toolset_map"] = {"slack.send": "ts1"}
        state["toolset_configs"] = {"ts1": {"auth": "oauth"}}
        creator = ToolInstanceCreator(state)
        config = creator._get_toolset_config("slack.send")
        assert config == {"auth": "oauth"}

    def test_get_toolset_config_no_mapping(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)
        assert creator._get_toolset_config("unknown.tool") is None

    def test_get_toolset_config_no_config_for_toolset_id(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        state["tool_to_toolset_map"] = {"slack.send": "ts1"}
        state["toolset_configs"] = {}
        creator = ToolInstanceCreator(state)
        assert creator._get_toolset_config("slack.send") is None

    def test_fallback_creation_with_state(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        class Action:
            def __init__(self, state=None):
                self.state = state

        instance = creator._fallback_creation(Action)
        assert instance.state is state

    def test_fallback_creation_no_args(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        class Action:
            def __init__(self):
                pass

        instance = creator._fallback_creation(Action)
        assert isinstance(instance, Action)

    def test_fallback_creation_empty_dict(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        class Action:
            def __init__(self, arg):
                self.arg = arg

        instance = creator._fallback_creation(Action)
        assert instance.arg == {} or instance.arg is None

    def test_fallback_creation_set_state(self):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        class Action:
            def __init__(self):
                self.state_val = None

            def set_state(self, s):
                self.state_val = s

        instance = creator._fallback_creation(Action)
        assert instance.state_val is state

    def test_fallback_creation_none_arg(self):
        """Covers the last fallback: action_class(None)."""
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        class Action:
            """Only accepts None or dict."""
            def __init__(self, arg):
                if arg is not None and not isinstance(arg, dict):
                    raise TypeError("needs specific arg")
                self.arg = arg

        instance = creator._fallback_creation(Action)
        assert instance.arg is None or instance.arg == {}

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_create_instance_with_factory(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_factory.create_client_sync.return_value = mock_client
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client):
                self.client = client

        instance = creator.create_instance(Action, "slack")
        assert instance.client is mock_client

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_create_instance_no_factory_falls_back(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_cfr.get_factory.return_value = None

        class Action:
            def __init__(self):
                pass

        instance = creator.create_instance(Action, "unknown")
        assert isinstance(instance, Action)

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_create_with_factory_auth_error_raises_valueerror(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.side_effect = Exception("not authenticated via oauth")
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client):
                self.client = client

        with pytest.raises(ValueError, match="not authenticated"):
            creator.create_instance(Action, "slack")

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_create_with_factory_non_auth_error_fallback(self, mock_cfr):
        """Non-auth errors fall back to _fallback_creation."""
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client_sync.side_effect = Exception("connection timeout")
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, state=None):
                self.state = state

        instance = creator.create_instance(Action, "slack")
        assert isinstance(instance, Action)

    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    def test_create_with_factory_uses_toolset_config(self, mock_cfr):
        """When tool_full_name is provided, toolset config is used."""
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        state["tool_to_toolset_map"] = {"slack.send_message": "ts1"}
        state["toolset_configs"] = {"ts1": {"auth": {"type": "oauth"}}}
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_factory.create_client_sync.return_value = mock_client
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client):
                self.client = client

        instance = creator.create_instance(Action, "slack", "slack.send_message")
        assert instance.client is mock_client
        call_args = mock_factory.create_client_sync.call_args
        assert call_args[0][2] == {"auth": {"type": "oauth"}}

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_create_instance_async_with_factory(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client):
                self.client = client

        instance = await creator.create_instance_async(Action, "slack")
        assert isinstance(instance, Action)

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_create_instance_async_caches_client(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client):
                self.client = client

        inst1 = await creator.create_instance_async(Action, "slack")
        inst2 = await creator.create_instance_async(Action, "slack")
        assert mock_factory.create_client.await_count == 1

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_create_instance_async_auth_error(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(side_effect=Exception("OAuth authentication failed"))
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client):
                self.client = client

        with pytest.raises(ValueError, match="not authenticated"):
            await creator.create_instance_async(Action, "slack")

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_create_instance_async_no_factory(self, mock_cfr):
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_cfr.get_factory.return_value = None

        class Action:
            def __init__(self):
                pass

        instance = await creator.create_instance_async(Action, "unknown")
        assert isinstance(instance, Action)

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_create_instance_async_non_auth_error_fallback(self, mock_cfr):
        """Non-auth errors in async path fall back to _fallback_creation."""
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(side_effect=Exception("network timeout"))
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, state=None):
                self.state = state

        instance = await creator.create_instance_async(Action, "slack")
        assert isinstance(instance, Action)

    @pytest.mark.asyncio
    @patch("app.agents.tools.wrapper.ClientFactoryRegistry")
    async def test_create_instance_async_with_state_param(self, mock_cfr):
        """When action class __init__ has 'state' param, state is passed."""
        rs = self._make_retrieval_service()
        state = self._make_state(rs)
        creator = ToolInstanceCreator(state)

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr.get_factory.return_value = mock_factory

        class Action:
            def __init__(self, client, state=None):
                self.client = client
                self.state = state

        instance = await creator.create_instance_async(Action, "slack")
        assert instance.client is mock_client
        assert instance.state is state


# ---------------------------------------------------------------------------
# RegistryToolWrapper
# ---------------------------------------------------------------------------

class TestRegistryToolWrapper:
    def _make_state(self):
        rs = MagicMock()
        rs.config_service = MagicMock()
        state = {
            "retrieval_service": rs,
            "logger": MagicMock(),
        }
        return state

    def _make_registry_tool(self, description="A test tool", params=None, fn=None, llm_desc=None):
        tool = MagicMock()
        tool.description = description
        tool.llm_description = llm_desc
        tool.parameters = params or []
        tool.function = fn or (lambda **kwargs: "result")
        return tool

    def test_init_sets_name(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("slack", "send_message", reg_tool, state)
        assert wrapper.name == "slack.send_message"

    def test_init_uses_llm_description_when_available(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool(
            description="Short",
            llm_desc="Detailed LLM description with guidance",
        )
        wrapper = RegistryToolWrapper("slack", "send", reg_tool, state)
        assert "Detailed LLM description" in wrapper.description

    def test_init_falls_back_to_description(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool(description="Tool desc", llm_desc=None)
        wrapper = RegistryToolWrapper("slack", "send", reg_tool, state)
        assert "Tool desc" in wrapper.description

    def test_init_fallback_name_description(self):
        state = self._make_state()
        reg_tool = MagicMock(spec=[])
        reg_tool.function = lambda: None
        reg_tool.parameters = []
        del reg_tool.description
        del reg_tool.llm_description
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        assert "app.tool" in wrapper.description

    def test_build_description_with_params(self):
        param = MagicMock()
        param.name = "query"
        param.type = MagicMock()
        param.type.name = "string"
        param.required = True
        param.description = "Search query"

        state = self._make_state()
        reg_tool = self._make_registry_tool(params=[param])
        wrapper = RegistryToolWrapper("search", "find", reg_tool, state)
        assert "Parameters" in wrapper.description
        assert "query" in wrapper.description

    def test_format_parameters_static(self):
        param = MagicMock()
        param.name = "limit"
        param.type = MagicMock()
        param.type.name = "integer"
        param.required = False
        param.description = "Max results"

        result = RegistryToolWrapper._format_parameters([param])
        assert len(result) == 1
        assert "limit" in result[0]
        assert "integer" in result[0]

    def test_format_parameters_no_type_name(self):
        param = MagicMock()
        param.name = "x"
        param.type = "string"
        param.required = True
        param.description = "desc"

        result = RegistryToolWrapper._format_parameters([param])
        assert "string" in result[0]

    def test_state_property(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        assert wrapper.state is state

    def test_is_class_method_true(self):
        class Foo:
            def bar(self):
                pass

        assert RegistryToolWrapper._is_class_method(Foo.bar) is True

    def test_format_result_tuple(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        assert wrapper._format_result((True, "data")) == "data"

    def test_format_result_list_tuple(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        assert wrapper._format_result([False, "error msg"]) == "error msg"

    def test_format_result_plain(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        assert wrapper._format_result("hello") == "hello"

    def test_format_result_long_tuple(self):
        """Tuples with length != 2 are stringified normally."""
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = wrapper._format_result((1, 2, 3))
        assert result == "(1, 2, 3)"

    def test_format_error(self):
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        error = ValueError("something broke")
        result = wrapper._format_error(error, {"arg": "val"})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "something broke" in parsed["message"]
        assert parsed["tool"] == "app.tool"
        assert parsed["args"] == {"arg": "val"}

    def test_format_error_no_logger(self):
        """When state lacks 'get', error is still formatted."""
        state = self._make_state()
        reg_tool = self._make_registry_tool()
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        wrapper.chat_state = "not a dict"
        error = ValueError("boom")
        result = wrapper._format_error(error, {})
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_arun_standalone_async(self):
        state = self._make_state()

        async def my_fn(**kwargs):
            return f"async: {kwargs.get('q')}"

        my_fn.__qualname__ = "my_fn"
        reg_tool = self._make_registry_tool(fn=my_fn)
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = await wrapper.arun(q="test")
        assert result == "async: test"

    @pytest.mark.asyncio
    async def test_arun_standalone_sync(self):
        """Standalone sync function via arun."""
        state = self._make_state()

        def my_fn(**kwargs):
            return f"sync: {kwargs.get('q')}"

        my_fn.__qualname__ = "my_fn"
        reg_tool = self._make_registry_tool(fn=my_fn)
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = await wrapper.arun(q="test")
        assert result == "sync: test"

    @pytest.mark.asyncio
    async def test_arun_with_dict_arg(self):
        state = self._make_state()

        def my_fn(**kwargs):
            return f"got: {kwargs.get('key')}"

        my_fn.__qualname__ = "my_fn"
        reg_tool = self._make_registry_tool(fn=my_fn)
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = await wrapper.arun({"key": "value"})
        assert result == "got: value"

    @pytest.mark.asyncio
    async def test_arun_with_no_args(self):
        """arun with no args uses empty dict."""
        state = self._make_state()

        def my_fn(**kwargs):
            return "no args"

        my_fn.__qualname__ = "my_fn"
        reg_tool = self._make_registry_tool(fn=my_fn)
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = await wrapper.arun()
        assert result == "no args"

    @pytest.mark.asyncio
    async def test_arun_error_returns_formatted(self):
        state = self._make_state()

        def my_fn(**kwargs):
            raise ValueError("arun error")

        my_fn.__qualname__ = "my_fn"
        reg_tool = self._make_registry_tool(fn=my_fn)
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = await wrapper.arun(a="b")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_arun_returns_tuple(self):
        state = self._make_state()

        def my_fn(**kwargs):
            return (True, "success")

        my_fn.__qualname__ = "my_fn"
        reg_tool = self._make_registry_tool(fn=my_fn)
        wrapper = RegistryToolWrapper("app", "tool", reg_tool, state)
        result = await wrapper.arun()
        assert result == (True, "success")
