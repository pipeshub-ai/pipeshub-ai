"""PIPESHUB_PROMPT_CACHE_RETENTION: unset, the build's default retention; a value the provider does not document stops
every service that makes model calls at start, instead of failing each chat; a principal narrows a cache scope's
routing key."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from harness_core.edition_constants import PROMPT_CACHE_RETENTION_DEFAULT
from harness_core.models.transports.prompt_cache import (
    CacheScope,
    PromptCacheSettings,
    check_prompt_cache_settings,
    routing_key,
)

ENV = "PIPESHUB_PROMPT_CACHE_RETENTION"


def test_unset_is_the_builds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert PromptCacheSettings.from_env().openai_retention == PROMPT_CACHE_RETENTION_DEFAULT
    assert PromptCacheSettings().openai_retention == PROMPT_CACHE_RETENTION_DEFAULT


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_is_unset_and_off_is_how_to_send_none(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv(ENV, blank)
    assert PromptCacheSettings.from_env().openai_retention == PROMPT_CACHE_RETENTION_DEFAULT
    check_prompt_cache_settings()
    monkeypatch.setenv(ENV, "off")
    assert PromptCacheSettings.from_env().openai_retention is None


@pytest.mark.parametrize(("value", "sent"), [("24h", "24h"), ("24H", "24h"), ("in_memory", "in_memory"),
                                             ("off", None), ("none", None)])
def test_a_documented_value_is_sent_as_the_provider_spells_it(
    monkeypatch: pytest.MonkeyPatch, value: str, sent: str | None,
) -> None:
    monkeypatch.setenv(ENV, value)
    assert PromptCacheSettings.from_env().openai_retention == sent


def test_an_undocumented_value_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "1h")
    with pytest.raises(ValueError, match="PIPESHUB_PROMPT_CACHE_RETENTION='1h' is not supported"):
        PromptCacheSettings.from_env()


def test_a_scope_with_a_principal_keys_the_cache_by_it() -> None:
    assert CacheScope("org-1", "conv-1").key(8) == routing_key("org-1", "conv-1", 8)
    assert CacheScope("org-1", "conv-1", principal_id="user:u1").key(8) == routing_key(
        "org-1", "conv-1", 8, principal_id="user:u1",
    )
    assert CacheScope("org-1", "conv-1", principal_id="user:u1").key(8) != CacheScope("org-1", "conv-1").key(8)


def test_the_start_up_check_refuses_an_undocumented_value_and_passes_a_documented_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "forever")
    with pytest.raises(ValueError, match="Leave it unset, set it to off, or to one of: 24h, in_memory"):
        check_prompt_cache_settings()
    monkeypatch.setenv(ENV, "24h")
    check_prompt_cache_settings()


APP = Path(__file__).resolve().parents[4] / "app"
# The service mains whose processes make model calls (the chat loop, the harness, the model gateway, indexing and
# extraction prompts).
MODEL_CALLING_MAINS = ("query_main.py", "harness_main.py", "model_gateway_main.py", "indexing_main.py",
                       "extraction_main.py")


@pytest.mark.parametrize("main", MODEL_CALLING_MAINS)
def test_each_model_calling_service_checks_the_setting_at_load(main: str) -> None:
    tree = ast.parse((APP / main).read_text())
    at_load = [
        n.value.func.id for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
    ]
    assert "check_prompt_cache_settings" in at_load, f"{main} does not check the prompt-cache setting at load"
