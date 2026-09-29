"""Which model judges demo answers, chosen from the environment, without a model.

``build_chat_model`` is replaced by a recorder, so these check what the judge
asks for and never reach a provider.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from app.connectors.sources.demo.harness.answer_judge import AnswerJudge
from tests.evals import chat_models
from tests.evals.chat_models import (
    JudgeConfigError,
    MissingModelError,
    judge_model_from_env,
)

ALL_VARS = (
    "JUDGE_FOUNDRY_RESOURCE", "JUDGE_PROVIDER", "JUDGE_MODEL", "JUDGE_API_KEY", "JUDGE_AZURE_ENDPOINT", "JUDGE_AZURE_DEPLOYMENT",
    "JUDGE_AZURE_API_VERSION", "EVAL_PROVIDER", "EVAL_MODEL", "TEST_AZURE_OPENAI_API_KEY",
    "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME", "TEST_AZURE_OPENAI_MODEL",
    "TEST_OPENAI_API_KEY", "TEST_ANTHROPIC_API_KEY",
)

ANSWERING_MODEL = {
    "TEST_AZURE_OPENAI_API_KEY": "answer-key",
    "TEST_AZURE_OPENAI_ENDPOINT": "https://answers.example.invalid",
    "TEST_AZURE_OPENAI_DEPLOYMENT_NAME": "answer-deployment",
    "TEST_AZURE_OPENAI_MODEL": "gpt-4o-mini",
}
AZURE_JUDGE = {
    "JUDGE_PROVIDER": "azure_openai",
    "JUDGE_MODEL": "gpt-4.1",
    "JUDGE_API_KEY": "judge-key",
    "JUDGE_AZURE_ENDPOINT": "https://judge.example.invalid",
    "JUDGE_AZURE_DEPLOYMENT": "judge-deployment",
}


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    calls: list[dict] = []

    def record(provider: str, model: str, api_key: str | None, **kwargs: object) -> object:
        if not api_key:
            raise MissingModelError(f"No API key for '{provider}'.")
        calls.append({"provider": provider, "model": model, "api_key": api_key, **kwargs})
        return object()

    monkeypatch.setattr(chat_models, "build_chat_model", record)

    def record_foundry(resource: str, api_key: str, model: str) -> object:
        calls.append({"provider": "anthropic_foundry", "resource": resource, "api_key": api_key, "model": model})
        return object()

    monkeypatch.setattr(chat_models, "build_foundry_judge_client", record_foundry)
    return calls


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_judge_settings_take_precedence_over_the_answering_model(
    monkeypatch: pytest.MonkeyPatch, built: list[dict]
) -> None:
    _set(monkeypatch, ANSWERING_MODEL | AZURE_JUDGE | {"EVAL_PROVIDER": "azure_openai"})
    judge = judge_model_from_env()
    assert judge.dedicated and (judge.provider, judge.model) == ("azure_openai", "gpt-4.1")
    assert built == [{
        "provider": "azure_openai", "model": "gpt-4.1", "api_key": "judge-key",
        "azure_endpoint": "https://judge.example.invalid", "azure_deployment": "judge-deployment",
        "azure_api_version": None,
    }]
    described = judge.describe()
    assert "JUDGE_*" in described and "judge-key" not in described


def test_an_openai_judge_needs_no_azure_settings(monkeypatch: pytest.MonkeyPatch, built: list[dict]) -> None:
    _set(monkeypatch, ANSWERING_MODEL | {"JUDGE_PROVIDER": "openai", "JUDGE_MODEL": "gpt-4.1", "JUDGE_API_KEY": "k"})
    judge = judge_model_from_env()
    assert (judge.provider, judge.model, built[0]["api_key"]) == ("openai", "gpt-4.1", "k")


@pytest.mark.parametrize(("drop", "named"), [
    ("JUDGE_API_KEY", "JUDGE_API_KEY"),
    ("JUDGE_AZURE_ENDPOINT", "JUDGE_AZURE_ENDPOINT"),
    ("JUDGE_AZURE_DEPLOYMENT", "JUDGE_AZURE_DEPLOYMENT"),
])
def test_a_missing_judge_setting_is_a_config_error_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch, built: list[dict], drop: str, named: str
) -> None:
    _set(monkeypatch, ANSWERING_MODEL | {k: v for k, v in AZURE_JUDGE.items() if k != drop})
    with pytest.raises(JudgeConfigError, match=named):
        judge_model_from_env()
    assert built == [], "the answering model must not stand in for a misconfigured judge"


def test_an_openai_judge_without_a_model_is_a_config_error(monkeypatch: pytest.MonkeyPatch, built: list[dict]) -> None:
    _set(monkeypatch, {"JUDGE_PROVIDER": "openai", "JUDGE_API_KEY": "k"})
    with pytest.raises(JudgeConfigError, match="JUDGE_MODEL"):
        judge_model_from_env()


def test_an_unknown_judge_provider_is_a_config_error(monkeypatch: pytest.MonkeyPatch, built: list[dict]) -> None:
    _set(monkeypatch, AZURE_JUDGE | {"JUDGE_PROVIDER": "gemini"})
    with pytest.raises(JudgeConfigError, match="gemini"):
        judge_model_from_env()


def test_without_judge_provider_the_eval_settings_are_used(monkeypatch: pytest.MonkeyPatch, built: list[dict]) -> None:
    _set(monkeypatch, ANSWERING_MODEL | {k: v for k, v in AZURE_JUDGE.items() if k != "JUDGE_PROVIDER"})
    judge = judge_model_from_env()
    assert not judge.dedicated
    assert (judge.provider, judge.model) == ("azure_openai", "gpt-4o-mini")
    assert built[0]["api_key"] == "answer-key"


def test_a_misconfigured_judge_fails_every_judgement_without_a_call() -> None:
    result = AnswerJudge.misconfigured("JUDGE_PROVIDER=azure_openai but JUDGE_API_KEY is not set.").judge(
        "Purchases up to $250 need no approval.", ["A purchase of up to and including $250 needs no approval."]
    )
    assert result.status == "judge error" and not result.passed
    assert "JUDGE_API_KEY" in result.detail


FOUNDRY_JUDGE = {
    "JUDGE_PROVIDER": "anthropic_foundry",
    "JUDGE_MODEL": "claude-sonnet-5.5",
    "JUDGE_API_KEY": "judge-key",
    "JUDGE_AZURE_ENDPOINT": "https://acme-ai.cognitiveservices.azure.com/",
}


@pytest.mark.parametrize(("endpoint", "resource"), [
    ("https://acme-ai.cognitiveservices.azure.com/", "acme-ai"),
    ("https://acme-ai.openai.azure.com", "acme-ai"),
    ("https://Acme-AI.services.ai.azure.com/anthropic/", "acme-ai"),
    ("acme-ai.openai.azure.com", "acme-ai"),
    ("https://example.com/", None),
    ("https://acme-ai.cognitiveservices.azure.com.evil.example/", None),
])
def test_the_foundry_resource_comes_from_the_endpoint_host(endpoint: str, resource: str | None) -> None:
    assert chat_models.foundry_resource(endpoint) == resource


def test_a_foundry_judge_uses_the_resource_deployment_and_its_own_key(
    monkeypatch: pytest.MonkeyPatch, built: list[dict]
) -> None:
    _set(monkeypatch, ANSWERING_MODEL | FOUNDRY_JUDGE)
    judge = judge_model_from_env()
    assert judge.dedicated and (judge.provider, judge.model) == ("anthropic_foundry", "claude-sonnet-5.5")
    assert built == [{
        "provider": "anthropic_foundry", "resource": "acme-ai", "api_key": "judge-key", "model": "claude-sonnet-5.5",
    }]


def test_an_explicit_foundry_resource_wins(monkeypatch: pytest.MonkeyPatch, built: list[dict]) -> None:
    _set(monkeypatch, FOUNDRY_JUDGE | {"JUDGE_AZURE_ENDPOINT": "https://example.com", "JUDGE_FOUNDRY_RESOURCE": "other"})
    judge_model_from_env()
    assert built[0]["resource"] == "other"


@pytest.mark.parametrize(("change", "named"), [
    ({"JUDGE_API_KEY": None}, "JUDGE_API_KEY"),
    ({"JUDGE_MODEL": None}, "JUDGE_MODEL or JUDGE_AZURE_DEPLOYMENT"),
    ({"JUDGE_AZURE_ENDPOINT": None}, "JUDGE_FOUNDRY_RESOURCE"),
    ({"JUDGE_AZURE_ENDPOINT": "https://example.com"}, "the one set is not"),
])
def test_a_foundry_judge_missing_a_setting_names_it(
    monkeypatch: pytest.MonkeyPatch, built: list[dict], change: dict, named: str
) -> None:
    _set(monkeypatch, ANSWERING_MODEL | {k: v for k, v in (FOUNDRY_JUDGE | change).items() if v is not None})
    with pytest.raises(JudgeConfigError) as err:
        judge_model_from_env()
    assert named in str(err.value) and "judge-key" not in str(err.value)
    assert built == []


def test_the_foundry_deployment_can_come_from_judge_azure_deployment(
    monkeypatch: pytest.MonkeyPatch, built: list[dict]
) -> None:
    _set(monkeypatch, {k: v for k, v in FOUNDRY_JUDGE.items() if k != "JUDGE_MODEL"} | {"JUDGE_AZURE_DEPLOYMENT": "claude-sonnet-5.5"})
    assert judge_model_from_env().model == "claude-sonnet-5.5"


def test_the_real_foundry_builder_points_the_sdk_at_the_resource() -> None:
    client = chat_models.build_foundry_judge_client("acme-ai", "not-a-real-key", "claude-sonnet-5.5")
    assert urlparse(str(client._sdk.base_url)).hostname == "acme-ai.services.ai.azure.com"
