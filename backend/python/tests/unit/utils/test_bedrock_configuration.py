"""Bedrock client configuration and provider identification.

Two failures motivated these, both of which reached a user as an error that
named neither the setting at fault nor the model it was set on:

* A wrong region or a model not enabled in it produced ~300 seconds of silent
  botocore retrying, which upstream could only report as "timed out".
* A model id naming one vendor with the provider dropdown set to another sent
  Anthropic's `thinking` block to a Nova model, and Bedrock answered
  "extraneous key [thinking] is not permitted".
"""

from __future__ import annotations

import pytest

from app.utils.aimodels import (
    _bedrock_client_config,
    _detect_bedrock_provider,
    _identify_bedrock_provider,
    bedrock_provider_mismatch,
    resolve_bedrock_provider,
)

NOVA_ARN = (
    "arn:aws:bedrock:ap-south-1:108782071197:inference-profile/"
    "global.amazon.nova-2-lite-v1:0"
)


class TestClientConfig:
    def test_a_connection_that_cannot_be_made_fails_fast(self) -> None:
        """botocore's 60s default, times five legacy attempts, is what turned a
        wrong region into a five-minute hang."""
        config = _bedrock_client_config()

        assert config.connect_timeout <= 15
        assert config.retries["max_attempts"] <= 3
        assert config.retries["mode"] == "standard"

    def test_a_request_that_reached_bedrock_is_given_room(self) -> None:
        """Extended thinking legitimately takes a while; only the connection
        should be impatient."""
        assert _bedrock_client_config().read_timeout >= 60

    def test_the_worst_case_stays_under_the_health_check_ceiling(self) -> None:
        config = _bedrock_client_config()
        worst_case = config.retries["max_attempts"] * config.connect_timeout
        assert worst_case < 120, "a connection failure must be reported, not timed out"

    @pytest.mark.parametrize(
        ("env_var", "value", "attribute"),
        [
            ("PIPESHUB_BEDROCK_CONNECT_TIMEOUT", "3", "connect_timeout"),
            ("PIPESHUB_BEDROCK_READ_TIMEOUT", "300", "read_timeout"),
        ],
    )
    def test_an_operator_can_retune_it(
        self, monkeypatch: pytest.MonkeyPatch, env_var: str, value: str, attribute: str,
    ) -> None:
        monkeypatch.setenv(env_var, value)
        assert getattr(_bedrock_client_config(), attribute) == int(value)

    def test_a_malformed_override_does_not_break_model_construction(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PIPESHUB_BEDROCK_CONNECT_TIMEOUT", "soon")
        assert _bedrock_client_config().connect_timeout > 0


class TestProviderIdentification:
    @pytest.mark.parametrize(
        ("model_name", "expected"),
        [
            (NOVA_ARN, "amazon"),
            ("amazon.nova-2-lite-v1:0", "amazon"),
            ("anthropic.claude-sonnet-4-v1:0", "anthropic"),
            ("us.meta.llama3-70b-instruct-v1:0", "meta"),
            ("mistral.mistral-large-2402-v1:0", "mistral"),
            ("deepseek.r1-v1:0", "deepseek"),
            ("openai.gpt-oss-120b-1:0", "openai"),
            ("cohere.command-r-plus-v1:0", "cohere"),
        ],
    )
    def test_a_model_id_names_its_vendor(self, model_name: str, expected: str) -> None:
        assert _identify_bedrock_provider(model_name) == expected

    @pytest.mark.parametrize("model_name", ["", None, "my-fine-tuned-model"])
    def test_an_unrecognisable_id_says_so(self, model_name: str | None) -> None:
        """The difference between "it is Anthropic" and "cannot tell" is what
        decides whether a configured value gets overridden."""
        assert _identify_bedrock_provider(model_name) is None

    def test_the_legacy_default_is_unchanged(self) -> None:
        """`_detect_bedrock_provider` still answers Anthropic for an id it
        cannot place — callers depend on a value, not None."""
        assert _detect_bedrock_provider("my-fine-tuned-model") == "anthropic"


class TestProviderResolution:
    def test_the_reported_case(self) -> None:
        """A Nova model with the provider set to Anthropic: honouring the
        dropdown sends `thinking`, and Bedrock rejects the request."""
        assert resolve_bedrock_provider("anthropic", NOVA_ARN) == "amazon"

    def test_a_matching_setting_is_left_alone(self) -> None:
        assert resolve_bedrock_provider("amazon", NOVA_ARN) == "amazon"
        assert bedrock_provider_mismatch("amazon", NOVA_ARN) is None

    def test_an_unrecognisable_model_keeps_the_configured_provider(self) -> None:
        """A fine-tuned or custom model is exactly when the dropdown is the
        only source of truth."""
        assert resolve_bedrock_provider("anthropic", "my-fine-tuned-model") == "anthropic"
        assert bedrock_provider_mismatch("anthropic", "my-fine-tuned-model") is None

    def test_no_configured_provider_falls_back_to_the_id(self) -> None:
        assert resolve_bedrock_provider(None, NOVA_ARN) == "amazon"

    @pytest.mark.parametrize("configured", ["other", "", "  "])
    def test_other_is_not_a_mismatch(self, configured: str) -> None:
        """"Other" means the admin is supplying a custom provider, not that
        they picked the wrong one."""
        assert bedrock_provider_mismatch(configured, NOVA_ARN) is None

    def test_case_and_spacing_do_not_create_a_false_mismatch(self) -> None:
        assert bedrock_provider_mismatch("  Amazon ", NOVA_ARN) is None


class TestRequestFieldsAfterResolution:
    def test_a_nova_model_never_receives_anthropics_thinking_block(self) -> None:
        """The end-to-end point: this is the payload Bedrock rejected."""
        from app.utils.aimodels import _bedrock_additional_model_request_fields

        provider = resolve_bedrock_provider("anthropic", NOVA_ARN)
        fields = _bedrock_additional_model_request_fields(
            "high", {"isReasoning": True},
            provider_in_bedrock=provider, model_name=NOVA_ARN,
        )

        assert "thinking" not in fields

    def test_a_claude_model_still_gets_one(self) -> None:
        from app.utils.aimodels import _bedrock_additional_model_request_fields

        model = "anthropic.claude-sonnet-4-v1:0"
        provider = resolve_bedrock_provider("anthropic", model)
        fields = _bedrock_additional_model_request_fields(
            "high", {"isReasoning": True},
            provider_in_bedrock=provider, model_name=model,
        )

        assert "thinking" in fields
