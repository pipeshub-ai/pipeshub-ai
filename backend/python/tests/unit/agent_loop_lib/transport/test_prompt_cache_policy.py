"""Prompt-caching policy shared by every transport: settings, the routing key,
and where each cache parameter may be sent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    CacheScope,
    OpenAICachePlan,
    PromptCacheSettings,
    anthropic_top_level_allowed,
    explicit_breakpoints,
    openai_cache_plan,
    routing_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

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

    def test_a_prompt_family_gets_keys_of_its_own(self) -> None:
        key = routing_key("org-1", "record-1", 8, family="named-entities")
        prefix, org, family, shard = key.split(":")
        assert (prefix, family) == ("pipeshub", "named-entities") and 0 <= int(shard) < 8
        assert org == routing_key("org-1", "record-1", 8).split(":")[1]
        assert key != routing_key("org-1", "record-1", 8, family="document-extraction")

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


AZURE = "https://res.openai.azure.com/"
KEYED = {"prompt_cache_key": "k"}
RETAINED = {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}


def _plan(model: str | None, base_url: str | None = None, settings: PromptCacheSettings = PromptCacheSettings(),
          refused: frozenset[str] = frozenset(), conversation: str = "conv-1") -> OpenAICachePlan | None:
    return openai_cache_plan(model=model, base_url=base_url, settings=settings,
                             scope=CacheScope("org-1", conversation), refused=refused)


def _keyed(plan: OpenAICachePlan | None) -> tuple[dict[str, str], bool] | None:
    """The plan with its key replaced by "k", so the matrix reads by shape."""
    if plan is None:
        return None
    return {name: ("k" if name == "prompt_cache_key" else value) for name, value in plan.kwargs.items()}, plan.breakpoint


class TestExplicitBreakpoints:
    @pytest.mark.parametrize(("model", "expected"), [
        ("gpt-5.6-sol", True), ("gpt-5.6-luna", True), ("gpt-5.6-sol-2026-07-01", True), ("gpt-6-luna", True),
        ("gpt-6.1-sol", True), ("GPT-6-Astra", True), ("gpt-7", True), ("gpt-5.10", True),
        ("gpt-5.5", False), ("gpt-5.5-pro", False), ("gpt-5.4-mini", False), ("gpt-5", False), ("gpt-4.1", False),
        ("gpt-4o", False), ("o3", False), ("gpt-oss-120b", False), ("prod-sol", False), ("", False), (None, False),
    ])
    def test_gpt_5_6_and_later_only(self, model: str | None, expected: bool) -> None:
        assert explicit_breakpoints(model) is expected


class TestOpenAICachePlan:
    @pytest.mark.parametrize(("model", "base_url", "expected"), [
        ("gpt-5.4", None, (RETAINED, False)),
        ("gpt-5.4", "https://api.openai.com/v1", (RETAINED, False)),
        ("gpt-5.4", AZURE, (RETAINED, False)),
        ("gpt-5", "https://res.cognitiveservices.azure.com", (RETAINED, False)),
        ("gpt-5-codex", None, (RETAINED, False)),
        ("gpt-4.1-2025-04-14", None, (RETAINED, False)),
        ("gpt-5.6-luna", None, (KEYED, True)),
        ("gpt-6.1-sol", AZURE, (KEYED, True)),
        ("gpt-6-luna", AZURE, (KEYED, True)),
        ("gpt-4o", None, (KEYED, False)),
        ("my-custom-deployment", AZURE, (KEYED, False)),
        ("gpt-5.4", "https://res.services.ai.azure.com/models", (RETAINED, False)),
        ("gpt-6.1-sol", "https://res.services.ai.azure.com/models", (KEYED, True)),
    ])
    def test_matrix(self, model: str, base_url: str | None, expected: tuple[dict[str, str], bool]) -> None:
        assert _keyed(_plan(model, base_url)) == expected

    @pytest.mark.parametrize(("model", "base_url"), [
        ("Llama-4-Maverick", "https://res.services.ai.azure.com/models"),
        ("gpt-6.1-sol", "https://api.together.xyz/v1"),
        ("gpt-6.1-sol", "http://litellm.internal:4000"),
        ("qwen3", "http://localhost:11434/v1"),
    ])
    def test_nothing_for_other_hosts(self, model: str, base_url: str) -> None:
        assert _plan(model, base_url) is None

    def test_disabled_or_no_retention(self) -> None:
        assert _plan("gpt-5.4", settings=PromptCacheSettings(enabled=False)) is None
        assert _keyed(_plan("gpt-5.4", settings=PromptCacheSettings(openai_retention=None))) == (KEYED, False)

    @pytest.mark.parametrize(("model", "on_openai", "on_azure"), [
        ("gpt-5.5", True, True), ("gpt-5.5-pro", True, False), ("gpt-5.4", True, True),
        ("gpt-5.3-codex", False, True), ("gpt-5.2", True, True), ("gpt-5.1-codex-max", True, True),
        ("gpt-5.1-chat-latest", True, False), ("gpt-5.1-chat", False, True), ("gpt-5", True, True),
        ("gpt-4.1", True, True), ("gpt-4.1-2025-04-14", True, True),
        ("gpt-5.4-mini", False, False), ("gpt-5.4-nano", False, False), ("gpt-5-mini", False, False),
        ("gpt-4.1-mini", False, False), ("gpt-5.3", False, False), ("gpt-5.2-codex", False, False),
        ("gpt-5.6-sol", False, False), ("gpt-6-luna", False, False), ("gpt-4o", False, False),
    ])
    def test_retention_only_for_the_models_each_provider_lists(
        self, model: str, on_openai: bool, on_azure: bool,
    ) -> None:
        assert ("prompt_cache_retention" in _plan(model).kwargs) is on_openai
        assert ("prompt_cache_retention" in _plan(model, AZURE).kwargs) is on_azure

    def test_gpt_5_6_on_openai_shares_one_key_per_org(self) -> None:
        keys = {_plan("gpt-6.1-sol", conversation=f"conv-{i}").kwargs["prompt_cache_key"] for i in range(50)}
        assert len(keys) == 1
        other_org = openai_cache_plan(model="gpt-6.1-sol", base_url=None, settings=PromptCacheSettings(),
                                      scope=CacheScope("org-2", "conv-1"))
        assert other_org is not None and other_org.kwargs["prompt_cache_key"] not in keys

    @pytest.mark.parametrize(("model", "base_url"), [("gpt-6.1-sol", AZURE), ("gpt-5.4", None), ("gpt-5.4", AZURE)])
    def test_azure_and_earlier_models_spread_over_the_shards(self, model: str, base_url: str | None) -> None:
        keys = {_plan(model, base_url, conversation=f"conv-{i}").kwargs["prompt_cache_key"] for i in range(200)}
        assert len(keys) == 8

    def test_refused_parameters_are_left_out(self) -> None:
        assert _keyed(_plan("gpt-5.4", refused=frozenset({"prompt_cache_retention"}))) == (KEYED, False)
        assert _keyed(_plan("gpt-6.1-sol", AZURE, refused=frozenset({"prompt_cache_breakpoint"}))) == (KEYED, False)
        assert _keyed(_plan("gpt-6.1-sol", AZURE, refused=frozenset({"prompt_cache_key"}))) == ({}, True)
        assert _plan("gpt-4o", refused=frozenset({"prompt_cache_key"})) is None

    def test_sent_params(self) -> None:
        assert _plan("gpt-6.1-sol").sent_params == {"prompt_cache_key", "prompt_cache_breakpoint"}
        assert _plan("gpt-5.4").sent_params == {"prompt_cache_key", "prompt_cache_retention"}


OPENROUTER = "https://openrouter.ai/api/v1"


class TestOpenRouter:
    """OpenRouter turns a breakpoint into what the upstream takes (GPT-5.6+:
    the same field; Claude: a cache_control) and drops it elsewhere; the key
    routes a shard of the org to one upstream."""

    @pytest.mark.parametrize(("model", "breakpoint_", "last_message"), [
        ("openai/gpt-6.1-sol", True, False),
        ("openai/gpt-5.6-luna", True, False),
        ("anthropic/claude-sonnet-5.5", True, True),
        ("openai/gpt-5.4", False, False),
        ("google/gemini-3.8-flash", False, False),
        ("deepseek/deepseek-v4.1-flash", False, False),
        ("gpt-6.1-sol", False, False),
    ])
    def test_markers_by_upstream(self, model: str, breakpoint_: bool, last_message: bool) -> None:
        plan = _plan(model, OPENROUTER)
        assert (plan.breakpoint, plan.last_message) == (breakpoint_, last_message)
        assert set(plan.kwargs) == {"prompt_cache_key"}

    def test_keys_spread_over_the_shards(self) -> None:
        keys = {_plan("anthropic/claude-sonnet-5.5", OPENROUTER, conversation=f"c-{i}").kwargs["prompt_cache_key"]
                for i in range(200)}
        assert len(keys) == 8

    def test_a_refused_breakpoint_takes_both_markers(self) -> None:
        plan = _plan("anthropic/claude-sonnet-5.5", OPENROUTER, refused=frozenset({"prompt_cache_breakpoint"}))
        assert (plan.breakpoint, plan.last_message, set(plan.kwargs)) == (False, False, {"prompt_cache_key"})
        assert _plan("openai/gpt-5.4", OPENROUTER, refused=frozenset({"prompt_cache_key"})) is None


class TestXAI:
    @pytest.mark.parametrize("model", ["grok-4.7", "gpt-6.1-sol"])
    def test_the_key_only(self, model: str) -> None:
        plan = _plan(model, "https://api.x.ai/v1/")
        assert (set(plan.kwargs), plan.breakpoint, plan.last_message) == ({"prompt_cache_key"}, False, False)
        assert _plan(model, "https://api.x.ai/v1/", refused=frozenset({"prompt_cache_key"})) is None

    @pytest.mark.parametrize("base_url", ["https://api.x.ai.example.com/v1", "https://proxy.example/api.x.ai/v1"])
    def test_only_xais_own_host(self, base_url: str) -> None:
        assert _plan("grok-4.7", base_url) is None


class TestRejectedCacheParams:
    @pytest.fixture(autouse=True)
    def _clean(self) -> Iterator[None]:
        REJECTED_CACHE_PARAMS.clear()
        yield
        REJECTED_CACHE_PARAMS.clear()

    def test_recorded_per_model_and_host(self) -> None:
        REJECTED_CACHE_PARAMS.record("GPT-5.4", "https://res.openai.azure.com/openai")
        assert REJECTED_CACHE_PARAMS.rejected("gpt-5.4", "https://RES.openai.azure.com")
        assert not REJECTED_CACHE_PARAMS.rejected("gpt-5.4", None)
        assert not REJECTED_CACHE_PARAMS.rejected("gpt-5.6", "https://res.openai.azure.com")

    def test_named_parameters_accumulate(self) -> None:
        REJECTED_CACHE_PARAMS.record("gpt-5.4", AZURE, {"prompt_cache_retention"})
        REJECTED_CACHE_PARAMS.record("gpt-5.4", AZURE, {"Prompt_Cache_Breakpoint"})
        assert REJECTED_CACHE_PARAMS.refused("gpt-5.4", AZURE) == {"prompt_cache_retention", "prompt_cache_breakpoint"}
        assert not REJECTED_CACHE_PARAMS.rejected("gpt-5.4", AZURE)

    def test_an_empty_set_refuses_nothing(self) -> None:
        REJECTED_CACHE_PARAMS.record("gpt-5.4", AZURE, frozenset())
        assert REJECTED_CACHE_PARAMS.refused("gpt-5.4", AZURE) == frozenset()
        assert REJECTED_CACHE_PARAMS.refused("gpt-5.4", None) == frozenset()
