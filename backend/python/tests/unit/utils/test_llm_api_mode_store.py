"""Unit tests for app.utils.llm_api_mode_store (LLMApiModeStore)."""

from unittest.mock import AsyncMock

import pytest

import app.utils.llm_api_mode_store as store_module
from app.config.constants.service import config_node_constants
from app.utils.llm_api_mode_store import (
    REASONING_MANDATORY_FALLBACK_EFFORT,
    LLMApiMode,
    LLMApiModeStore,
    get_llm_api_mode_store,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """The module-level singleton must not leak state across tests."""
    store_module._default_store = None
    yield
    store_module._default_store = None


def _fake_config_service(stored: dict | None = None) -> AsyncMock:
    service = AsyncMock()
    service.get_config = AsyncMock(return_value=stored)
    service.set_config = AsyncMock(return_value=True)
    return service


class TestLoad:
    async def test_load_populates_snapshot(self):
        stored = {
            "model-key-1": {"gpt-5.6-luna": {"mode": "responses", "learnedAt": store_module.time.time()}},
        }
        config_service = _fake_config_service(stored)
        store = LLMApiModeStore(config_service)

        await store.load()

        config_service.get_config.assert_called_once_with(
            config_node_constants.AI_MODEL_API_MODES.value, use_cache=False,
        )
        assert store.get("model-key-1", "gpt-5.6-luna") == "responses"

    async def test_load_with_nothing_stored_yields_empty_snapshot(self):
        config_service = _fake_config_service(None)
        store = LLMApiModeStore(config_service)
        await store.load()
        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_load_failure_keeps_previous_snapshot(self):
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)
        # First, a successful load establishes a snapshot.
        config_service.get_config.return_value = {
            "k": {"m": {"mode": "responses", "learnedAt": store_module.time.time()}},
        }
        await store.load()
        assert store.get("k", "m") == "responses"

        # A subsequent failing load must not wipe it.
        config_service.get_config.side_effect = RuntimeError("etcd unreachable")
        await store.load()
        assert store.get("k", "m") == "responses"


class TestLoadNormalizesMalformedShapes:
    """`get()` runs synchronously on `get_generator_model()`'s hot path, so a
    malformed KV entry (corruption, manual edit, future/incompatible schema)
    must be dropped here at load time rather than raise out of `get()` and
    fail model construction for every request."""

    async def test_non_dict_top_level_yields_empty_snapshot(self):
        config_service = _fake_config_service()
        config_service.get_config.return_value = ["not", "a", "dict"]
        store = LLMApiModeStore(config_service)

        await store.load()

        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_non_dict_model_name_map_for_a_key_is_dropped(self):
        config_service = _fake_config_service()
        config_service.get_config.return_value = {"model-key-1": "not-a-dict"}
        store = LLMApiModeStore(config_service)

        await store.load()

        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_non_dict_entry_is_dropped(self):
        config_service = _fake_config_service()
        config_service.get_config.return_value = {
            "model-key-1": {"gpt-5.6-luna": "responses"},
        }
        store = LLMApiModeStore(config_service)

        await store.load()

        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_non_numeric_learned_at_is_dropped(self):
        config_service = _fake_config_service()
        config_service.get_config.return_value = {
            "model-key-1": {"gpt-5.6-luna": {"mode": "responses", "learnedAt": "not-a-number"}},
        }
        store = LLMApiModeStore(config_service)

        await store.load()

        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_non_string_mode_is_dropped(self):
        config_service = _fake_config_service()
        config_service.get_config.return_value = {
            "model-key-1": {"gpt-5.6-luna": {"mode": 123, "learnedAt": store_module.time.time()}},
        }
        store = LLMApiModeStore(config_service)

        await store.load()

        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_malformed_entry_does_not_drop_valid_sibling_entries(self):
        config_service = _fake_config_service()
        config_service.get_config.return_value = {
            "model-key-1": {
                "bad-model": "not-a-dict",
                "gpt-5.6-luna": {"mode": "responses", "learnedAt": store_module.time.time()},
            },
            "bad-key": "not-a-dict",
        }
        store = LLMApiModeStore(config_service)

        await store.load()

        assert store.get("model-key-1", "bad-model") is None
        assert store.get("model-key-1", "gpt-5.6-luna") == "responses"
        assert store.get("bad-key", "anything") is None


class TestGet:
    def test_get_returns_none_when_nothing_learned(self):
        store = LLMApiModeStore(_fake_config_service())
        assert store.get("model-key-1", "gpt-5.6-luna") is None

    def test_get_returns_none_for_missing_model_key(self):
        store = LLMApiModeStore(_fake_config_service())
        assert store.get(None, "gpt-5.6-luna") is None
        assert store.get("", "gpt-5.6-luna") is None

    def test_get_returns_none_for_missing_model_name(self):
        store = LLMApiModeStore(_fake_config_service())
        assert store.get("model-key-1", "") is None

    async def test_get_ignores_expired_fact(self):
        expired_ts = store_module.time.time() - store_module._LEARNED_FACT_TTL_SECONDS - 1
        config_service = _fake_config_service({
            "model-key-1": {"gpt-5.6-luna": {"mode": "responses", "learnedAt": expired_ts}},
        })
        store = LLMApiModeStore(config_service)
        await store.load()
        assert store.get("model-key-1", "gpt-5.6-luna") is None

    async def test_get_honours_unexpired_fact_close_to_ttl_boundary(self):
        recent_ts = store_module.time.time() - store_module._LEARNED_FACT_TTL_SECONDS + 60
        config_service = _fake_config_service({
            "model-key-1": {"gpt-5.6-luna": {"mode": "responses", "learnedAt": recent_ts}},
        })
        store = LLMApiModeStore(config_service)
        await store.load()
        assert store.get("model-key-1", "gpt-5.6-luna") == "responses"


class TestRecord:
    async def test_record_then_get_round_trip(self):
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)

        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.NO_REASONING_WITH_TOOLS.value)

        assert store.get("model-key-1", "gpt-5.6-luna") == LLMApiMode.NO_REASONING_WITH_TOOLS.value
        config_service.set_config.assert_called_once()
        persisted_key, persisted_value = config_service.set_config.call_args.args
        assert persisted_key == config_node_constants.AI_MODEL_API_MODES.value
        assert persisted_value["model-key-1"]["gpt-5.6-luna"]["mode"] == (
            LLMApiMode.NO_REASONING_WITH_TOOLS.value
        )

    async def test_duplicate_record_writes_to_kv_only_once(self):
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)

        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.RESPONSES.value)
        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.RESPONSES.value)
        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.RESPONSES.value)

        assert config_service.set_config.call_count == 1

    async def test_record_different_mode_for_same_model_writes_again(self):
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)

        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.RESPONSES.value)
        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.NO_REASONING_WITH_TOOLS.value)

        assert config_service.set_config.call_count == 2
        assert store.get("model-key-1", "gpt-5.6-luna") == LLMApiMode.NO_REASONING_WITH_TOOLS.value

    async def test_record_no_op_without_model_key(self):
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)

        await store.record(None, "gpt-5.6-luna", LLMApiMode.RESPONSES.value)

        config_service.set_config.assert_not_called()
        assert store.get(None, "gpt-5.6-luna") is None

    async def test_record_persist_failure_keeps_in_process_fact(self):
        """A KV write failure must not lose the in-process learning for the
        rest of this pod's lifetime — only cross-pod propagation degrades."""
        config_service = _fake_config_service()
        config_service.set_config.side_effect = RuntimeError("etcd unreachable")
        store = LLMApiModeStore(config_service)

        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.RESPONSES.value)

        assert store.get("model-key-1", "gpt-5.6-luna") == LLMApiMode.RESPONSES.value

    async def test_record_two_different_models_under_same_key(self):
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)

        await store.record("model-key-1", "gpt-5.6-luna", LLMApiMode.RESPONSES.value)
        await store.record("model-key-1", "gpt-5.7-nova", LLMApiMode.NO_REASONING_WITH_TOOLS.value)

        assert store.get("model-key-1", "gpt-5.6-luna") == LLMApiMode.RESPONSES.value
        assert store.get("model-key-1", "gpt-5.7-nova") == LLMApiMode.NO_REASONING_WITH_TOOLS.value

    async def test_record_reasoning_mandatory_then_get_round_trip(self):
        """`REASONING_MANDATORY` is stored/read like any other mode — see
        `app/utils/aimodels.py::_reasoning_effort_kwargs` for how a `"none"`
        request gets bumped to `REASONING_MANDATORY_FALLBACK_EFFORT` once
        this mode is learned for a model."""
        config_service = _fake_config_service()
        store = LLMApiModeStore(config_service)

        await store.record("model-key-1", "gemini-flash", LLMApiMode.REASONING_MANDATORY.value)

        assert store.get("model-key-1", "gemini-flash") == LLMApiMode.REASONING_MANDATORY.value


class TestSingletonFactory:
    def test_returns_none_when_never_initialized(self):
        assert get_llm_api_mode_store() is None

    def test_first_call_with_config_service_creates_singleton(self):
        config_service = _fake_config_service()
        store = get_llm_api_mode_store(config_service)
        assert isinstance(store, LLMApiModeStore)
        # Every later call, with or without arguments, returns the SAME instance.
        assert get_llm_api_mode_store() is store
        assert get_llm_api_mode_store(_fake_config_service()) is store


def test_reasoning_mandatory_fallback_effort_is_a_valid_reasoning_effort_value():
    """Must be a value `_reasoning_effort_kwargs` can actually send onwards
    (i.e. not `"none"`/`"max"`, which not every provider's effort map
    accepts) — "low" is universally supported."""
    assert REASONING_MANDATORY_FALLBACK_EFFORT == "low"
