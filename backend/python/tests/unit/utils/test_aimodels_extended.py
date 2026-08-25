"""
Extended tests for app.utils.aimodels covering missing lines:
- _create_bedrock_client with explicit keys vs default chain
- get_embedding_model with model_name specified but not in list
- get_embedding_model with HuggingFace provider
- get_generator_model with Bedrock auto-detect (mistral, meta, amazon, cohere, ai21, qwen)
- _get_anthropic_max_tokens for 4.5 model
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CLAUDE_4_5,
    MAX_OUTPUT_TOKENS_CLAUDE_MODERN,
    ModelType,
    _bedrock_is_openai_gpt5,
    _create_bedrock_client,
    _detect_bedrock_provider,
    _get_anthropic_max_tokens,
    get_embedding_model,
    get_generator_model,
    is_multimodal_llm,
)


# ============================================================================
# _create_bedrock_client
# ============================================================================


class TestCreateBedrockClient:
    def test_with_explicit_keys(self):
        config = {
            "awsAccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            "awsAccessSecretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "region": "us-east-1",
        }
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_bedrock_client(config)
            mock_session_cls.assert_called_once_with(
                aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                region_name="us-east-1",
            )
            mock_session.client.assert_called_once_with("bedrock-runtime")

    def test_without_explicit_keys(self):
        config = {"region": "us-west-2"}
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_bedrock_client(config)
            mock_session_cls.assert_called_once_with(region_name="us-west-2")


# ============================================================================
# _get_anthropic_max_tokens
# ============================================================================


class TestGetAnthropicMaxTokens:
    def test_claude_4_5(self):
        assert _get_anthropic_max_tokens("claude-4.5-sonnet") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_regular_model(self):
        assert _get_anthropic_max_tokens("claude-3-opus") == MAX_OUTPUT_TOKENS

    def test_modern_claude_sonnet_5(self):
        assert _get_anthropic_max_tokens("claude-sonnet-5") == MAX_OUTPUT_TOKENS_CLAUDE_MODERN

    def test_modern_claude_opus_4_8(self):
        assert _get_anthropic_max_tokens("claude-opus-4-8") == MAX_OUTPUT_TOKENS_CLAUDE_MODERN


# ============================================================================
# get_embedding_model - model_name not in list
# ============================================================================


class TestGetEmbeddingModelExtended:
    def test_model_name_not_in_list(self):
        config = {
            "configuration": {"model": "model-a, model-b", "apiKey": "key"},
            "isDefault": False,
        }
        with pytest.raises(ValueError, match="not found in"):
            get_embedding_model(EmbeddingProvider.OPENAI.value, config, model_name="model-c")

    def test_hugging_face_with_api_key(self):
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "apiKey": "hf_token",
                "model_kwargs": {"device": "cpu"},
                "encode_kwargs": {},
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels.get_embedding_server_embeddings") as mock_fn:
            mock_fn.return_value = MagicMock()
            get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            mock_fn.assert_called_once_with("sentence-transformers/all-MiniLM-L6-v2", trust_remote_code=False)

    def test_hugging_face_without_api_key(self):
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "model_kwargs": {"device": "cpu"},
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels.get_embedding_server_embeddings") as mock_fn:
            mock_fn.return_value = MagicMock()
            get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            mock_fn.assert_called_once_with("sentence-transformers/all-MiniLM-L6-v2", trust_remote_code=False)

    def test_hugging_face_normalize_defaults(self):
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels.get_embedding_server_embeddings") as mock_fn:
            mock_fn.return_value = MagicMock()
            get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            mock_fn.assert_called_once_with("sentence-transformers/all-MiniLM-L6-v2", trust_remote_code=False)

    def test_hugging_face_trust_remote_code(self):
        config = {
            "configuration": {
                "model": "nomic-ai/nomic-embed-text-v2-moe",
                "trustRemoteCode": True,
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels.get_embedding_server_embeddings") as mock_fn:
            mock_fn.return_value = MagicMock()
            get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            mock_fn.assert_called_once_with(
                "nomic-ai/nomic-embed-text-v2-moe",
                trust_remote_code=True,
            )


# ============================================================================
# get_generator_model - Bedrock auto-detect
# ============================================================================


class TestGetGeneratorModelBedrock:
    def _make_config(self, model_name, provider=None, custom_provider=None):
        config = {
            "configuration": {
                "model": model_name,
                "region": "us-east-1",
            },
            "isDefault": True,
        }
        if provider is not None:
            config["configuration"]["provider"] = provider
        if custom_provider is not None:
            config["configuration"]["customProvider"] = custom_provider
        return config

    def test_auto_detect_mistral(self):
        config = self._make_config("mistral.mistral-7b")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "mistral"

    def test_auto_detect_meta(self):
        config = self._make_config("meta.llama3-70b-instruct")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "meta"

    def test_auto_detect_amazon(self):
        config = self._make_config("amazon.titan-text-express")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "amazon"

    def test_auto_detect_cohere(self):
        config = self._make_config("cohere.command-r-plus")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "cohere"

    def test_auto_detect_ai21(self):
        config = self._make_config("ai21.jamba-instruct")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "ai21"

    def test_auto_detect_qwen(self):
        config = self._make_config("qwen-72b")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "qwen"

    def test_auto_detect_default_anthropic(self):
        config = self._make_config("some-unknown-model")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "anthropic"

    def test_custom_provider(self):
        config = self._make_config("custom-model", provider="other", custom_provider="my_custom")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "my_custom"

    def test_other_provider_no_custom(self):
        """When provider='other' but no customProvider, fall back to auto-detect."""
        config = self._make_config("claude-3-sonnet", provider="other")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "anthropic"

    def test_anthropic_model_in_bedrock(self):
        """Anthropic models in Bedrock should pass max_tokens to Converse."""
        config = self._make_config("anthropic.claude-3-sonnet")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert "max_tokens" in mock_chat.call_args.kwargs
                assert "model_kwargs" not in mock_chat.call_args.kwargs
                assert "beta_use_converse_api" not in mock_chat.call_args.kwargs

    def test_auto_detect_openai_gpt_oss(self):
        config = self._make_config("openai.gpt-oss-120b-1:0")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "openai"

    def test_auto_detect_openai_gpt56_luna(self):
        config = self._make_config("gpt-5.6-luna")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "openai"

    def test_gpt5_pattern_accepts_bedrock_ids(self):
        for model_id in (
            "openai.gpt-5.4",
            "openai.gpt-5.5",
            "us.openai.gpt-5.6-luna",
            "global.openai.gpt-5.6-sol",
            "gpt-5.6-luna",
        ):
            assert _bedrock_is_openai_gpt5(model_id), model_id
            assert _detect_bedrock_provider(model_id) == "openai", model_id

    def test_gpt5_pattern_rejects_false_positives(self):
        assert not _bedrock_is_openai_gpt5("amazon.gpt-50-future")
        assert not _bedrock_is_openai_gpt5("vendor.my-gpt-5-compatible")
        assert not _bedrock_is_openai_gpt5("vendor.gpt5-model")
        assert not _bedrock_is_openai_gpt5("openai.gpt-oss-120b-1:0")
        assert _detect_bedrock_provider("amazon.gpt-50-future") == "amazon"
        assert _detect_bedrock_provider("vendor.gpt5-model") == LLMProvider.ANTHROPIC.value

    def test_auto_detect_nova_2_lite(self):
        config = self._make_config("nova-2-lite")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "amazon"

    def test_auto_detect_deepseek(self):
        config = self._make_config("us.deepseek.r1-v1:0")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "deepseek"


class TestBedrockReasoningAndTemperature:
    def _make_config(
        self,
        model_name: str,
        *,
        provider: str | None = None,
        is_reasoning: bool = False,
    ) -> dict:
        config: dict = {
            "configuration": {
                "model": model_name,
                "region": "us-east-1",
            },
            "isDefault": True,
            "isReasoning": is_reasoning,
        }
        if provider is not None:
            config["configuration"]["provider"] = provider
        return config

    def test_openai_reasoning_effort_and_temperature(self):
        config = self._make_config(
            "openai.gpt-oss-120b-1:0", provider="openai", is_reasoning=True
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value,
                    config,
                    reasoning_effort="medium",
                )
                kwargs = mock_chat.call_args.kwargs
                assert kwargs["additional_model_request_fields"] == {
                    "reasoning_effort": "medium"
                }
                assert kwargs["temperature"] == 0.2

    def test_gpt56_luna_max_effort_not_clamped(self):
        config = self._make_config(
            "us.openai.gpt-5.6-luna", provider="openai", is_reasoning=True
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="max"
                )
                kwargs = mock_chat.call_args.kwargs
                assert kwargs["additional_model_request_fields"] == {
                    "reasoning_effort": "max"
                }
                assert "temperature" not in kwargs

    def test_openai_max_effort_clamps_to_high(self):
        config = self._make_config(
            "openai.gpt-oss-20b-1:0", provider="openai", is_reasoning=True
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="max"
                )
                assert mock_chat.call_args.kwargs["additional_model_request_fields"] == {
                    "reasoning_effort": "high"
                }

    def test_openai_non_reasoning_omits_additional_fields(self):
        config = self._make_config(
            "openai.gpt-oss-120b-1:0", provider="openai", is_reasoning=False
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                assert "additional_model_request_fields" not in mock_chat.call_args.kwargs

    @pytest.mark.parametrize("effort", ("high", "max"))
    def test_anthropic_reasoning_omits_temperature(self, effort):
        config = self._make_config(
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
            provider="anthropic",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort=effort
                )
                kwargs = mock_chat.call_args.kwargs
                thinking = kwargs["additional_model_request_fields"]["thinking"]
                budget = thinking["budget_tokens"]
                max_tokens = kwargs["max_tokens"]
                assert thinking["type"] == "enabled"
                assert budget == 4096
                assert budget >= 1024
                assert max_tokens == MAX_OUTPUT_TOKENS_CLAUDE_4_5
                assert budget < max_tokens
                assert "temperature" not in kwargs

    def test_unrecognised_claude_thinking_keeps_budget_below_max_tokens(self):
        config = self._make_config(
            "anthropic.claude-experimental-v1:0",
            provider="anthropic",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                thinking = kwargs["additional_model_request_fields"]["thinking"]
                budget = thinking["budget_tokens"]
                assert thinking["type"] == "enabled"
                assert budget >= 1024
                assert kwargs["max_tokens"] > budget

    def test_thinking_sets_max_tokens_when_provider_is_not_anthropic(self):
        config = self._make_config(
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
            provider="amazon",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                thinking = kwargs["additional_model_request_fields"]["thinking"]
                budget = thinking["budget_tokens"]
                assert thinking["type"] == "enabled"
                assert budget >= 1024
                assert kwargs["max_tokens"] > budget

    def test_dated_sonnet_4_uses_manual_thinking_not_adaptive(self):
        config = self._make_config(
            "anthropic.claude-sonnet-4-20250514-v1:0",
            provider="anthropic",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                thinking = kwargs["additional_model_request_fields"]["thinking"]
                budget = thinking["budget_tokens"]
                assert thinking["type"] == "enabled"
                assert budget >= 1024
                assert kwargs["max_tokens"] == MAX_OUTPUT_TOKENS_CLAUDE_4_5
                assert budget < kwargs["max_tokens"]

    def test_anthropic_adaptive_reasoning(self):
        config = self._make_config(
            "us.anthropic.claude-sonnet-4-6",
            provider="anthropic",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="medium"
                )
                kwargs = mock_chat.call_args.kwargs
                assert kwargs["additional_model_request_fields"] == {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "medium"},
                }
                assert "temperature" not in kwargs

    def test_nova_2_high_reasoning_omits_temperature(self):
        config = self._make_config(
            "us.amazon.nova-2-lite-v1:0",
            provider="amazon",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                assert kwargs["additional_model_request_fields"] == {
                    "reasoningConfig": {
                        "type": "enabled",
                        "maxReasoningEffort": "high",
                    }
                }
                assert "temperature" not in kwargs
                assert "max_tokens" not in kwargs

    def test_nova_2_unprefixed_id_high_reasoning(self):
        config = self._make_config(
            "amazon.nova-2-lite-v1:0",
            provider="amazon",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                assert kwargs["additional_model_request_fields"] == {
                    "reasoningConfig": {
                        "type": "enabled",
                        "maxReasoningEffort": "high",
                    }
                }
                assert "temperature" not in kwargs
                assert "max_tokens" not in kwargs

    def test_nova_1_reasoning_omits_reasoning_config(self):
        config = self._make_config(
            "amazon.nova-lite-v1:0",
            provider="amazon",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                assert "additional_model_request_fields" not in kwargs
                assert kwargs["temperature"] == 0.2

    def test_deepseek_r1_reasoning_sends_no_additional_fields(self):
        config = self._make_config(
            "us.deepseek.r1-v1:0",
            provider="deepseek",
            is_reasoning=True,
        )
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrockConverse") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(
                    LLMProvider.AWS_BEDROCK.value, config, reasoning_effort="high"
                )
                kwargs = mock_chat.call_args.kwargs
                assert "additional_model_request_fields" not in kwargs
                assert kwargs["temperature"] == 0.2
