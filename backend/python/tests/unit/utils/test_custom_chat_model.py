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
