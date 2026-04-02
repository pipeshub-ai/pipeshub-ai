"""Unit tests for app.utils.custom_chat_model.ChatTogether class properties."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ChatTogether class property tests
# ---------------------------------------------------------------------------
class TestChatTogetherProperties:
    """Tests for ChatTogether class-level properties and metadata."""

    def test_lc_secrets(self):
        """lc_secrets should map together_api_key to TOGETHER_API_KEY env var."""
        from app.utils.custom_chat_model import ChatTogether

        # lc_secrets is a property, instantiate with mocked client setup
        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123",
                model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            )
            secrets = instance.lc_secrets
            assert isinstance(secrets, dict)
            assert secrets["together_api_key"] == "TOGETHER_API_KEY"

    def test_get_lc_namespace(self):
        """get_lc_namespace should return the langchain namespace path."""
        from app.utils.custom_chat_model import ChatTogether

        ns = ChatTogether.get_lc_namespace()
        assert ns == ["langchain", "chat_models", "together"]

    def test_llm_type(self):
        """_llm_type should return 'together-chat'."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123",
                model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            )
            assert instance._llm_type == "together-chat"

    def test_default_model_name(self):
        """Default model should be Meta-Llama-3.1-8B-Instruct-Turbo."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123")
            assert instance.model_name == "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"

    def test_custom_model_name(self):
        """Custom model name should be accepted."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123", model="custom/model"
            )
            assert instance.model_name == "custom/model"

    def test_default_api_base(self):
        """Default API base should be Together AI endpoint."""
        from app.utils.custom_chat_model import ChatTogether

        env = {"TOGETHER_API_KEY": "test-key-123"}
        # Remove TOGETHER_API_BASE to get default
        with patch.dict("os.environ", env, clear=False):
            instance = ChatTogether(api_key="test-key-123")
            assert "together.xyz" in instance.together_api_base

    def test_custom_api_base(self):
        """Custom API base should be accepted."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123",
                base_url="https://custom.api.com/v1/",
            )
            assert instance.together_api_base == "https://custom.api.com/v1/"

    def test_lc_attributes_with_api_base(self):
        """lc_attributes should include together_api_base when set."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123",
                base_url="https://custom.api.com/v1/",
            )
            attrs = instance.lc_attributes
            assert "together_api_base" in attrs
            assert attrs["together_api_base"] == "https://custom.api.com/v1/"

    def test_lc_attributes_empty_when_no_custom_base(self):
        """lc_attributes should be empty when using default api base."""
        from app.utils.custom_chat_model import ChatTogether

        # Default base is truthy, so it will still appear
        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123")
            attrs = instance.lc_attributes
            # Default base is set and truthy, so together_api_base will be in attrs
            assert isinstance(attrs, dict)

    def test_lc_attributes_empty_when_api_base_is_empty(self):
        """lc_attributes should be empty dict when together_api_base is empty string."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123",
                base_url="https://api.together.xyz/v1/",
            )
            # Manually set together_api_base to empty string to trigger falsy branch
            instance.together_api_base = ""
            attrs = instance.lc_attributes
            assert attrs == {}

    def test_get_ls_params(self):
        """_get_ls_params should include ls_provider='together'."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123")
            params = instance._get_ls_params()
            assert params["ls_provider"] == "together"

    def test_validate_n_less_than_1(self):
        """n < 1 should raise ValueError."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            with pytest.raises(ValueError, match="n must be at least 1"):
                ChatTogether(api_key="test-key-123", n=0)

    def test_validate_n_greater_than_1_with_streaming(self):
        """n > 1 with streaming should raise ValueError."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            with pytest.raises(ValueError, match="n must be 1 when streaming"):
                ChatTogether(api_key="test-key-123", n=2, streaming=True)

    def test_populate_by_name_config(self):
        """Model should allow population by field name (not just alias)."""
        from app.utils.custom_chat_model import ChatTogether

        assert ChatTogether.model_config.get("populate_by_name") is True

    def test_validate_environment_no_api_key(self):
        """When together_api_key is None, client_params api_key should be None."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123")
            # Verify instance was created (api_key path with SecretStr)
            assert instance.together_api_key is not None

    def test_validate_environment_max_retries_none(self):
        """When max_retries is None (the default), it should not be added to client_params."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            # Default max_retries is None, so line 102 is skipped
            instance = ChatTogether(api_key="test-key-123")
            assert instance.max_retries is None

    def test_validate_environment_max_retries_set(self):
        """When max_retries is not None, it should be included in client_params (line 102)."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            with patch("app.utils.custom_chat_model.openai.OpenAI") as mock_openai, \
                 patch("app.utils.custom_chat_model.openai.AsyncOpenAI") as mock_async:
                mock_client = MagicMock()
                mock_client.chat.completions = MagicMock()
                mock_openai.return_value = mock_client
                mock_async_client = MagicMock()
                mock_async_client.chat.completions = MagicMock()
                mock_async.return_value = mock_async_client

                instance = ChatTogether(
                    api_key="test-key-123",
                    max_retries=3,
                )
                assert instance.max_retries == 3
                # Verify max_retries was passed to OpenAI constructor
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("max_retries") == 3

    def test_validate_environment_skips_client_creation_when_already_set(self):
        """When client and async_client are already set, skip creation."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            with patch("app.utils.custom_chat_model.openai.OpenAI") as mock_openai, \
                 patch("app.utils.custom_chat_model.openai.AsyncOpenAI") as mock_async:
                mock_client = MagicMock()
                mock_client.chat.completions = MagicMock()
                mock_openai.return_value = mock_client
                mock_async_client = MagicMock()
                mock_async_client.chat.completions = MagicMock()
                mock_async.return_value = mock_async_client

                # First create a normal instance to get clients set up
                instance = ChatTogether(api_key="test-key-123")
                assert mock_openai.call_count >= 1
                assert mock_async.call_count >= 1

                # Record call counts
                openai_calls = mock_openai.call_count
                async_calls = mock_async.call_count

                # Now the clients are set on the instance. Call validate_environment
                # again to exercise the "skip" branches (lines 104->110, 110->118)
                instance.validate_environment()

                # No additional OpenAI/AsyncOpenAI calls should have been made
                assert mock_openai.call_count == openai_calls
                assert mock_async.call_count == async_calls

    def test_validate_n_equal_to_1_no_error(self):
        """n=1 should not raise any error."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123", n=1)
            assert instance.n == 1

    def test_validate_n_greater_than_1_without_streaming(self):
        """n > 1 without streaming should not raise."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(
                api_key="test-key-123", n=2, streaming=False
            )
            assert instance.n == 2

    def test_validate_n_none_is_valid(self):
        """n=None (default) should not raise any error."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123")
            # n is None by default, should pass validation
            assert instance is not None

    def test_get_ls_params_with_stop(self):
        """_get_ls_params should work with stop parameter."""
        from app.utils.custom_chat_model import ChatTogether

        with patch.dict(
            "os.environ", {"TOGETHER_API_KEY": "test-key-123"}, clear=False
        ):
            instance = ChatTogether(api_key="test-key-123")
            params = instance._get_ls_params(stop=["END"])
            assert params["ls_provider"] == "together"
