import pytest

from app.services.ai_models import traits as traits_module
from app.services.ai_models.traits import (
    normalize_efforts,
    parse_parameter_count,
    resolve_traits,
    size_from_name,
    size_from_parameters,
    size_from_price,
)


@pytest.fixture
def no_catalog(monkeypatch) -> None:
    monkeypatch.setattr(traits_module, "lookup_catalog", lambda provider, model: None)


@pytest.fixture
def no_overlay(monkeypatch) -> None:
    monkeypatch.setattr(traits_module, "load_overlay", lambda: [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("qwen3:8b", 8.0),
        ("llama-3.3-70b-instruct", 70.0),
        ("mixtral:8x7b", 56.0),
        ("32.8B", 32.8),
        ("qwen3-30b-a3b", 30.0),
        ("gpt-5.6", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_parameter_count(text, expected) -> None:
    assert parse_parameter_count(text) == expected


@pytest.mark.parametrize(
    ("billions", "size"), [(0.6, "small"), (14.9, "small"), (15, "medium"), (80, "medium"), (120, "large"), (None, None), (0, None)],
)
def test_size_from_parameters(billions, size) -> None:
    assert size_from_parameters(billions) == size


@pytest.mark.parametrize(("price", "size"), [(0.4, "small"), (1.5, "medium"), (7.99, "medium"), (8, "large"), (0, None), (-1, None), (None, None)])
def test_size_from_price(price, size) -> None:
    assert size_from_price(price) == size


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("gpt-5.6-mini", "small"),
        ("gemini-3-flash", "small"),
        ("gemini-3-pro", "large"),
        ("claude-opus-5", "large"),
        ("minimax-m2", None),
        ("gemini-3", None),
        ("my-company-model", None),
    ],
)
def test_size_from_name_keyword_boundaries(name, size) -> None:
    assert size_from_name(name) == size


def test_normalize_efforts_maps_provider_names_and_orders() -> None:
    assert normalize_efforts(["xhigh", "minimal", "medium", "bogus"]) == ["low", "medium", "max"]
    assert normalize_efforts("high") == []


def test_unknown_model_defaults_to_medium_and_no_thinking(no_catalog, no_overlay) -> None:
    result = resolve_traits("openAICompatible", "acme-internal-model")
    assert result.size == "medium"
    assert result.source["size"] == "default"
    assert result.thinking == "none"
    assert result.reasoning_efforts == []


def test_stored_values_win_over_everything(no_catalog) -> None:
    result = resolve_traits(
        "openAI", "gpt-5.6-mini",
        {"model": "gpt-5.6-mini", "size": "large", "thinking": "always", "reasoningEfforts": ["low", "high"]},
    )
    assert (result.size, result.thinking, result.reasoning_efforts) == ("large", "always", ["low", "high"])
    assert result.source["size"] == "stored"


def test_stored_auto_value_means_detect(no_catalog) -> None:
    result = resolve_traits("openAI", "gpt-5.6-mini", {"size": "auto", "thinking": "auto"})
    assert result.size == "small"
    assert result.source["size"] == "overlay"


def test_stored_traits_ignored_on_multi_model_entries(no_catalog, no_overlay) -> None:
    result = resolve_traits("ollama", "qwen3:8b", {"size": "large"}, single_model_entry=False)
    assert result.size == "small"
    assert result.source["size"] == "parameters"


def test_provider_parameter_count_beats_name(no_catalog, no_overlay) -> None:
    result = resolve_traits("ollama", "mystery-model", provider_facts={"parameter_count": 70})
    assert result.size == "medium"


def test_catalog_price_band_and_reasoning(monkeypatch, no_overlay) -> None:
    monkeypatch.setattr(
        traits_module, "lookup_catalog",
        lambda provider, model: {"output_cost_per_token": 0.00001, "supports_reasoning": True,
                                 "supports_xhigh_reasoning_effort": True, "supports_function_calling": True},
    )
    result = resolve_traits("openAI", "some-model")
    assert result.size == "large"
    assert result.thinking == "optional"
    assert result.reasoning_efforts == ["low", "medium", "high", "max"]
    assert result.supports_tools is True


def test_catalog_beats_legacy_reasoning_flag(monkeypatch, no_overlay) -> None:
    monkeypatch.setattr(traits_module, "lookup_catalog", lambda provider, model: {"supports_reasoning": False})
    result = resolve_traits("openAI", "gpt-4o", entry={"isReasoning": True})
    assert result.thinking == "none"


def test_legacy_reasoning_flag_used_when_nothing_else_knows(no_catalog, no_overlay) -> None:
    result = resolve_traits("openAICompatible", "acme", entry={"isReasoning": True})
    assert result.thinking == "optional"
    assert result.reasoning_efforts == ["low", "medium", "high"]


def test_overlay_marks_always_thinking_family(no_catalog) -> None:
    result = resolve_traits("gemini", "gemini-3-pro-preview")
    assert (result.size, result.thinking) == ("large", "always")


def test_shipped_overlay_compiles() -> None:
    traits_module.load_overlay.cache_clear()
    rules = traits_module.load_overlay()
    assert rules and all("_re" in rule for rule in rules)


@pytest.mark.parametrize(
    ("model", "size"),
    [("gpt-5.6-luna", "small"), ("gpt-5.6-terra", "medium"), ("gpt-6-sol", "large"), ("gpt-5.4-mini", "small"),
     ("gpt-5.6", "large"), ("gpt-5-chat-latest", "medium")],
)
def test_openai_family_names_map_to_sizes(no_catalog, model, size) -> None:
    assert resolve_traits("openAI", model).size == size
