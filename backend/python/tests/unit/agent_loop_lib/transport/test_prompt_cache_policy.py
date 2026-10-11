"""Prompt-caching policy shared by every transport: settings, the routing key,
and where each cache parameter may be sent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    PromptCacheSettings,
    anthropic_top_level_allowed,
    openai_cache_kwargs,
    routing_key,
)

BACKEND = Path(__file__).resolve().parents[4]


class TestSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("PIPESHUB_PROMPT_CACHE", "PIPESHUB_PROMPT_CACHE_TTL", "PIPESHUB_PROMPT_CACHE_RETENTION",
                     "PIPESHUB_PROMPT_CACHE_SHARDS"):
            monkeypatch.delenv(name, raising=False)
        settings = PromptCacheSettings.from_env()
        assert settings == PromptCacheSettings()
        assert settings.breakpoint == {"type": "ephemeral"}

    @pytest.mark.parametrize(("env", "expected"), [
        ({"PIPESHUB_PROMPT_CACHE": "false"}, {"enabled": False}),
        ({"PIPESHUB_PROMPT_CACHE_TTL": "1h"}, {"anthropic_ttl": "1h"}),
        ({"PIPESHUB_PROMPT_CACHE_TTL": "10m"}, {"anthropic_ttl": "5m"}),
        ({"PIPESHUB_PROMPT_CACHE_RETENTION": "off"}, {"openai_retention": None}),
        ({"PIPESHUB_PROMPT_CACHE_SHARDS": "0"}, {"routing_shards": 1}),
        ({"PIPESHUB_PROMPT_CACHE_SHARDS": "junk"}, {"routing_shards": 8}),
    ])
    def test_env(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: dict[str, object]) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        settings = PromptCacheSettings.from_env()
        assert {name: getattr(settings, name) for name in expected} == expected

    def test_a_longer_ttl_is_on_every_marker(self) -> None:
        assert PromptCacheSettings(anthropic_ttl="1h").breakpoint == {"type": "ephemeral", "ttl": "1h"}


class TestRoutingKey:
    def test_format_and_stability_within_a_conversation(self) -> None:
        key = routing_key("org-1", "conv-1", 8)
        assert key == routing_key("org-1", "conv-1", 8)
        prefix, org, shard = key.split(":")
        assert prefix == "pipeshub" and len(org) == 12 and "org-1" not in key and 0 <= int(shard) < 8

    def test_conversations_spread_over_the_shards(self) -> None:
        shards = {routing_key("org-1", f"conv-{i}", 8).rsplit(":", 1)[1] for i in range(200)}
        assert len(shards) == 8

    def test_the_same_in_every_process(self) -> None:
        script = (
            "import json; from app.agent_loop_lib.transport.prompt_cache import routing_key;"
            "print(json.dumps([routing_key('org-1', f'conv-{i}', 8) for i in range(20)]))"
        )
        outputs = {
            subprocess.run(
                [sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True, check=True,
                env={**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(BACKEND)},
            ).stdout.strip().splitlines()[-1]
            for seed in range(4)
        }
        assert len(outputs) == 1 and len(json.loads(outputs.pop())) == 20


class TestAnthropicTopLevel:
    @pytest.mark.parametrize(("base_url", "allowed"), [
        (None, True),
        ("https://api.anthropic.com", True),
        ("https://myres.services.ai.azure.com/anthropic", True),
        ("https://proxy.internal.example/anthropic", False),
        ("http://localhost:4000", False),
    ])
    def test_hosts(self, base_url: str | None, allowed: bool) -> None:
        assert anthropic_top_level_allowed(base_url) is allowed


class TestOpenAICacheKwargs:
    SETTINGS = PromptCacheSettings()

    @pytest.mark.parametrize(("model", "base_url", "expected"), [
        ("gpt-5.4", None, {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}),
        ("gpt-5.4-mini", "https://api.openai.com/v1", {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}),
        ("gpt-5.4", "https://res.openai.azure.com/", {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}),
        ("gpt-5", "https://res.cognitiveservices.azure.com", {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}),
        ("gpt-5-codex", None, {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}),
        ("gpt-5.6-luna", None, {"prompt_cache_key": "k"}),
        ("gpt-6-luna", "https://res.openai.azure.com/", {"prompt_cache_key": "k"}),
        ("gpt-4o", None, {"prompt_cache_key": "k"}),
        ("my-custom-deployment", "https://res.openai.azure.com/", {"prompt_cache_key": "k"}),
        ("gpt-5.4", "https://res.services.ai.azure.com/models", {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}),
        ("Llama-4-Maverick", "https://res.services.ai.azure.com/models", {}),
        ("gpt-5.4", "https://openrouter.ai/api/v1", {}),
        ("qwen3", "http://localhost:11434/v1", {}),
    ])
    def test_matrix(self, model: str, base_url: str | None, expected: dict[str, str]) -> None:
        assert openai_cache_kwargs(model=model, base_url=base_url, settings=self.SETTINGS, key="k") == expected

    def test_disabled_or_no_retention(self) -> None:
        off = PromptCacheSettings(enabled=False)
        assert openai_cache_kwargs(model="gpt-5.4", base_url=None, settings=off, key="k") == {}
        no_retention = PromptCacheSettings(openai_retention=None)
        assert openai_cache_kwargs(model="gpt-5.4", base_url=None, settings=no_retention, key="k") == {
            "prompt_cache_key": "k",
        }


class TestRejectedCacheParams:
    def test_recorded_per_model_and_host(self) -> None:
        REJECTED_CACHE_PARAMS.clear()
        try:
            REJECTED_CACHE_PARAMS.record("GPT-5.4", "https://res.openai.azure.com/openai")
            assert REJECTED_CACHE_PARAMS.rejected("gpt-5.4", "https://RES.openai.azure.com")
            assert not REJECTED_CACHE_PARAMS.rejected("gpt-5.4", None)
            assert not REJECTED_CACHE_PARAMS.rejected("gpt-5.6", "https://res.openai.azure.com")
        finally:
            REJECTED_CACHE_PARAMS.clear()
