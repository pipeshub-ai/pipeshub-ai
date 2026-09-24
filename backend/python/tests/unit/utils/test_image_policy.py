"""`app/utils/image_policy.py` — per-model image capability resolution.

The numbers asserted here are the documented provider limits (Azure OpenAI 10
images per chat request, Bedrock Converse 20, Ollama one per turn); if a
provider changes theirs, these tests are where that shows up.
"""

from __future__ import annotations

import pytest

from app.utils.image_policy import (
    GLOBAL_ENV_VAR,
    MAX_CONFIGURABLE_IMAGES,
    TEXT_ONLY_POLICY,
    UNKNOWN_POLICY,
    permissive_policy,
    provider_env_var,
    resolve_image_policy,
)


class TestProviderDefaults:
    @pytest.mark.parametrize(
        ("provider", "expected"),
        [
            ("openAI", 8),
            ("azureOpenAI", 8),
            ("azureAI", 6),
            ("anthropic", 12),
            ("bedrock", 10),
            ("gemini", 12),
            ("vertexAI", 12),
            ("ollama", 1),
            ("lmStudio", 1),
            ("openRouter", 6),
            ("litellmProxy", 4),
        ],
    )
    def test_each_provider_resolves_to_its_documented_cap(self, provider: str, expected: int) -> None:
        policy = resolve_image_policy(provider=provider, is_multimodal=True)
        assert policy.max_images_per_request == expected
        assert policy.source == "provider"

    def test_every_cap_stays_under_the_tightest_hard_limit_it_could_meet(self) -> None:
        """Azure hard-rejects above 10 and Bedrock Converse above 20, so no
        default may exceed the smallest limit its traffic can reach."""
        for provider, hard_limit in (
            ("openAI", 10),        # an OpenAI model may be served via Azure
            ("azureOpenAI", 10),
            ("openRouter", 10),    # routes to whichever upstream has capacity
            ("bedrock", 20),
            ("anthropic", 20),     # above 20 blocks the dimension rule tightens
        ):
            policy = resolve_image_policy(provider=provider, is_multimodal=True)
            assert policy.max_images_per_request <= hard_limit, provider

    def test_unknown_provider_is_conservative(self) -> None:
        policy = resolve_image_policy(provider="some-self-hosted-thing", is_multimodal=True)
        assert policy == UNKNOWN_POLICY
        assert policy.source == "unknown-default"

    def test_missing_provider_is_conservative(self) -> None:
        assert resolve_image_policy(provider=None, is_multimodal=True) == UNKNOWN_POLICY
        assert resolve_image_policy(provider="", is_multimodal=True) == UNKNOWN_POLICY

    @pytest.mark.parametrize(
        "spelling", ["azureOpenAI", "azure_openai", "AZURE-OPENAI", " azure openai "],
    )
    def test_provider_spellings_fold_onto_one_entry(self, spelling: str) -> None:
        assert resolve_image_policy(provider=spelling, is_multimodal=True).max_images_per_request == 8

    @pytest.mark.parametrize("provider", ["openAI", "anthropic", "ollama", "unknown"])
    def test_text_only_model_takes_no_images_whatever_the_provider(self, provider: str) -> None:
        assert resolve_image_policy(provider=provider, is_multimodal=False) == TEXT_ONLY_POLICY
        assert not TEXT_ONLY_POLICY.allows_images

    def test_local_providers_get_a_smaller_raster(self) -> None:
        """A quantized local model pays for pixels in wall-clock time."""
        local = resolve_image_policy(provider="ollama", is_multimodal=True)
        hosted = resolve_image_policy(provider="anthropic", is_multimodal=True)
        assert local.max_long_edge_px < hosted.max_long_edge_px


class TestEnvOverride:
    def test_provider_specific_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(provider_env_var("ollama"), "4")
        policy = resolve_image_policy(provider="ollama", is_multimodal=True)
        assert policy.max_images_per_request == 4
        assert policy.source == "env"

    def test_global_override_applies_when_no_provider_one_is_set(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(GLOBAL_ENV_VAR, "3")
        assert resolve_image_policy(provider="openAI", is_multimodal=True).max_images_per_request == 3

    def test_provider_override_beats_the_global_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(GLOBAL_ENV_VAR, "3")
        monkeypatch.setenv(provider_env_var("openAI"), "7")
        assert resolve_image_policy(provider="openAI", is_multimodal=True).max_images_per_request == 7

    def test_override_cannot_exceed_the_hard_ceiling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(GLOBAL_ENV_VAR, "99999")
        policy = resolve_image_policy(provider="openAI", is_multimodal=True)
        assert policy.max_images_per_request == MAX_CONFIGURABLE_IMAGES

    def test_zero_is_a_meaningful_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'Send no images from this deployment' is a legitimate instruction,
        distinct from 'no override set'."""
        monkeypatch.setenv(GLOBAL_ENV_VAR, "0")
        policy = resolve_image_policy(provider="openAI", is_multimodal=True)
        assert policy.max_images_per_request == 0
        assert not policy.allows_images

    def test_negative_override_clamps_rather_than_inverting_the_cap(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(GLOBAL_ENV_VAR, "-5")
        assert resolve_image_policy(provider="openAI", is_multimodal=True).max_images_per_request == 0

    @pytest.mark.parametrize("bad", ["eight", "", "   ", "8.5"])
    def test_malformed_override_falls_back_to_the_table(
        self, monkeypatch: pytest.MonkeyPatch, bad: str,
    ) -> None:
        """A typo in an env var must not fail a live request."""
        monkeypatch.setenv(GLOBAL_ENV_VAR, bad)
        policy = resolve_image_policy(provider="openAI", is_multimodal=True)
        assert policy.max_images_per_request == 8
        assert policy.source == "provider"

    def test_override_does_not_resurrect_a_text_only_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`isMultimodal=False` is a statement about the model, not a budget."""
        monkeypatch.setenv(GLOBAL_ENV_VAR, "10")
        assert resolve_image_policy(provider="openAI", is_multimodal=False) == TEXT_ONLY_POLICY


class TestPermissivePolicy:
    def test_permissive_policy_is_labelled_and_bounded_by_its_argument(self) -> None:
        policy = permissive_policy(50)
        assert policy.max_images_per_request == 50
        assert policy.source == "permissive"
