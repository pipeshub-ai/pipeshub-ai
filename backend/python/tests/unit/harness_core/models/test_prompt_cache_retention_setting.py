"""PIPESHUB_PROMPT_CACHE_RETENTION: unset, the build's default retention; a value the provider does not document is
refused at once instead of failing each model call; a principal narrows a cache scope's routing key."""
from __future__ import annotations

import pytest

from harness_core.edition_constants import PROMPT_CACHE_RETENTION_DEFAULT
from harness_core.models.transports.prompt_cache import (
    CacheScope,
    PromptCacheSettings,
    routing_key,
)

ENV = "PIPESHUB_PROMPT_CACHE_RETENTION"


def test_unset_is_the_builds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert PromptCacheSettings.from_env().openai_retention == PROMPT_CACHE_RETENTION_DEFAULT
    assert PromptCacheSettings().openai_retention == PROMPT_CACHE_RETENTION_DEFAULT


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
