"""Unit tests for :mod:`app.config.collection_spec`.

These helpers are the single source of truth for per-collection embedding
model metadata (provider, model, dimension, is_multimodal). The shape of
the persisted payload and the key layout in the KV store is load-bearing
for health-check identity comparisons — regressions here silently
re-introduce the "same-dim, different model" corruption class the helpers
were written to prevent.
"""
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from app.config.collection_spec import (
    SIGNATURE_VERSION,
    collection_spec_key,
    default_collection_spec,
    delete_collection_spec,
    get_collection_spec,
    set_collection_spec,
)
from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL


class _FakeConfigService:
    """Minimal async stub modelled on :class:`ConfigurationService`.

    Captures the last ``set_config`` call so tests can assert on both
    the key and the payload, and exposes :meth:`get_config`/
    :meth:`delete_config` so round-trip tests look natural.
    """

    def __init__(self, initial: Dict[str, Any] | None = None) -> None:
        self._store: Dict[str, Any] = dict(initial or {})
        self.set_calls: list[tuple[str, Any]] = []
        self.delete_calls: list[str] = []

    async def get_config(self, key, *args, **kwargs):
        return self._store.get(key)

    async def set_config(self, key, value):
        self._store[key] = value
        self.set_calls.append((key, value))
        return True

    async def delete_config(self, key):
        self.delete_calls.append(key)
        return self._store.pop(key, None) is not None


def test_collection_spec_key_has_stable_prefix():
    assert (
        collection_spec_key("records")
        == "/services/vectorCollections/records"
    )


@pytest.mark.asyncio
async def test_round_trip_write_read_persists_normalized_fields():
    svc = _FakeConfigService()

    written = await set_collection_spec(
        svc,
        "records",
        provider="OpenAI",
        model="models/Text-Embedding-3-Small",
        dimension=1536,
        is_multimodal=False,
    )

    assert written is True
    assert svc.set_calls == [
        (
            "/services/vectorCollections/records",
            {
                # Provider/model must be lower-cased and stripped of the
                # "models/" prefix so later identity comparisons in the
                # health check don't trip on trivial casing differences.
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 1536,
                "is_multimodal": False,
                "signature_version": SIGNATURE_VERSION,
            },
        )
    ]

    # Round-trip: the spec we just wrote must be readable via the helper.
    loaded = await get_collection_spec(svc, "records")
    assert loaded is not None
    assert loaded["embedding_provider"] == "openai"
    assert loaded["embedding_model"] == "text-embedding-3-small"
    assert loaded["embedding_dimension"] == 1536
    assert loaded["is_multimodal"] is False
    assert loaded["signature_version"] == SIGNATURE_VERSION


@pytest.mark.asyncio
async def test_set_omits_is_multimodal_when_unknown():
    """``is_multimodal`` must be absent (not ``None``/``False``) when the
    caller doesn't know the flag, so readers can distinguish "text-only"
    from "unknown" and avoid spurious mismatches on legacy specs."""
    svc = _FakeConfigService()

    await set_collection_spec(
        svc,
        "records",
        provider="cohere",
        model="embed-v3",
        dimension=1024,
    )

    _, payload = svc.set_calls[0]
    assert "is_multimodal" not in payload
    assert payload["signature_version"] == SIGNATURE_VERSION


@pytest.mark.asyncio
async def test_get_returns_none_when_node_missing():
    svc = _FakeConfigService()

    assert await get_collection_spec(svc, "records") is None


@pytest.mark.asyncio
async def test_get_returns_none_for_non_dict_payload():
    """Defensive: some KV store backends can return a stale value of the
    wrong type. ``get_collection_spec`` must treat anything that isn't a
    non-empty dict as "not stored" so the default-model fallback kicks in."""
    svc = _FakeConfigService(
        initial={
            "/services/vectorCollections/records": "not a dict",
        }
    )

    assert await get_collection_spec(svc, "records") is None


@pytest.mark.asyncio
async def test_delete_removes_node():
    svc = _FakeConfigService(
        initial={
            "/services/vectorCollections/records": {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 1536,
            }
        }
    )

    assert await delete_collection_spec(svc, "records") is True
    assert svc.delete_calls == ["/services/vectorCollections/records"]
    assert await get_collection_spec(svc, "records") is None


@pytest.mark.asyncio
async def test_delete_swallows_errors():
    """``delete_collection_spec`` is always called on best-effort
    collection-teardown paths; it must never raise out to the caller."""
    svc = AsyncMock()
    svc.delete_config = AsyncMock(side_effect=RuntimeError("boom"))

    assert await delete_collection_spec(svc, "records") is False


def test_default_collection_spec_uses_built_in_model():
    spec = default_collection_spec(1024)

    assert spec["embedding_model"] == DEFAULT_EMBEDDING_MODEL.lower()
    assert spec["embedding_dimension"] == 1024
    assert spec["signature_version"] == SIGNATURE_VERSION


def test_default_collection_spec_omits_dimension_when_unknown():
    spec = default_collection_spec(None)

    assert "embedding_dimension" not in spec
    assert spec["embedding_model"] == DEFAULT_EMBEDDING_MODEL.lower()


def test_default_collection_spec_ignores_invalid_dimension():
    """A zero or negative dimension is meaningless for identity
    comparisons; it should be elided rather than propagated."""
    assert "embedding_dimension" not in default_collection_spec(0)
    assert "embedding_dimension" not in default_collection_spec(-42)


@pytest.mark.asyncio
async def test_set_collection_spec_rejects_empty_name():
    svc = _FakeConfigService()

    result = await set_collection_spec(
        svc,
        "",
        provider="openai",
        model="text-embedding-3-small",
        dimension=1536,
    )

    assert result is False
    assert svc.set_calls == []


@pytest.mark.asyncio
async def test_get_collection_spec_rejects_empty_name():
    svc = _FakeConfigService()

    assert await get_collection_spec(svc, "") is None
