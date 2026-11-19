"""Which AI providers a run is held to, and which it may skip.

Three of the four live providers have no key in the integration workflow, so
holding the nightly to them would only produce permanent red. OpenAI has one
(Azure OpenAI's account is gone), so a run that was meant to cover it must fail
rather than skip when the key is gone — a rotated key otherwise reads as a
passing run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

import pytest

from helper.source_credentials import REQUIRE_ENV

pytestmark = pytest.mark.unit

_SUITE = (
    Path(__file__).resolve().parents[1]
    / "response-validation"
    / "ai-model-providers"
    / "integration_test_ai_model_providers.py"
)


def _suite():
    """Import the suite by path: its directory name is not an identifier."""
    if "ai_model_providers_suite" not in sys.modules:
        spec = importlib.util.spec_from_file_location("ai_model_providers_suite", _SUITE)
        module = importlib.util.module_from_spec(spec)
        sys.modules["ai_model_providers_suite"] = module
        spec.loader.exec_module(module)
    return sys.modules["ai_model_providers_suite"]


def _spec_for(provider_id: str):
    suite = _suite()
    for spec in suite._live_provider_specs():
        if spec.provider_id == provider_id:
            return spec
    raise AssertionError(f"no spec for {provider_id}")


def _clear_keys(monkeypatch) -> None:
    for name in (
        "TEST_AI_MODEL_PROVIDER",
        "TEST_OPENAI_API_KEY", "OPENAI_API_KEY", "TEST_GROQ_API_KEY",
        "TEST_GEMINI_API_KEY", "TEST_AZURE_OPENAI_API_KEY",
        "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_azure(monkeypatch) -> None:
    monkeypatch.setenv("TEST_AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("TEST_AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME", "d")


@pytest.mark.parametrize(
    ("run_provider", "key"),
    [(None, "TEST_OPENAI_API_KEY"), ("openAI", "TEST_OPENAI_API_KEY"),
     ("azureOpenAI", "TEST_AZURE_OPENAI_API_KEY")],
)
def test_a_missing_key_for_the_run_s_provider_fails_the_run(monkeypatch, run_provider, key) -> None:
    """The provider TEST_AI_MODEL_PROVIDER names (OpenAI when unset) must not skip."""
    suite = _suite()
    _clear_keys(monkeypatch)
    monkeypatch.setenv(REQUIRE_ENV, "1")
    if run_provider:
        monkeypatch.setenv("TEST_AI_MODEL_PROVIDER", run_provider)
    provider = run_provider or suite._PROVIDER_OPENAI

    with pytest.raises(BaseException) as caught:
        suite._skip_if_no_live_credentials(_spec_for(provider))

    assert caught.typename == "Failed"
    assert key in str(caught.value)


def test_the_other_provider_skips_even_with_its_key_set(monkeypatch) -> None:
    """Both providers' keys are passed; only the named one may be used."""
    suite = _suite()
    _clear_keys(monkeypatch)
    _set_azure(monkeypatch)
    monkeypatch.setenv(REQUIRE_ENV, "1")
    monkeypatch.setenv("TEST_AI_MODEL_PROVIDER", "openAI")

    with pytest.raises(BaseException) as caught:
        suite._skip_if_no_live_credentials(_spec_for(suite._PROVIDER_AZURE_OPENAI))

    assert caught.typename == "Skipped"


def test_a_provider_with_no_key_in_ci_still_skips(monkeypatch) -> None:
    suite = _suite()
    _clear_keys(monkeypatch)
    monkeypatch.setenv(REQUIRE_ENV, "1")

    with pytest.raises(BaseException) as caught:
        suite._skip_if_no_live_credentials(_spec_for(suite._PROVIDER_GROQ))

    assert caught.typename == "Skipped"


def test_an_ordinary_run_skips_every_provider(monkeypatch) -> None:
    suite = _suite()
    _clear_keys(monkeypatch)
    monkeypatch.delenv(REQUIRE_ENV, raising=False)

    for provider in (suite._PROVIDER_OPENAI, suite._PROVIDER_AZURE_OPENAI, suite._PROVIDER_GROQ):
        with pytest.raises(BaseException) as caught:
            suite._skip_if_no_live_credentials(_spec_for(provider))
        assert caught.typename == "Skipped"


_PROVIDER_FROM_VARIABLE = "${{ vars.TEST_AI_MODEL_PROVIDER || 'openAI' }}"


def _env_blocks(doc: dict):
    """Every env mapping in a workflow: top level, each job, each step."""
    if isinstance(doc.get("env"), dict):
        yield "env", doc["env"]
    for job_name, job in (doc.get("jobs") or {}).items():
        if isinstance(job.get("env"), dict):
            yield job_name, job["env"]
        for step in job.get("steps") or []:
            if isinstance(step.get("env"), dict):
                yield f"{job_name}/{step.get('name', '?')}", step["env"]


def test_switching_provider_is_one_repository_variable() -> None:
    """Every job that names an AI provider takes it from the repository variable
    and gets both providers' keys, so switching needs no workflow change.

    The Ollama workflow runs a local model and is the one exception.
    """
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    problems, checked = [], 0
    for path in sorted(workflows.glob("*.yml")):
        if path.name == "integration-tests-ollama.yml":
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for where, env in _env_blocks(doc):
            if "TEST_AI_MODEL_PROVIDER" not in env:
                continue
            checked += 1
            if env["TEST_AI_MODEL_PROVIDER"] != _PROVIDER_FROM_VARIABLE:
                problems.append(f"{path.name} {where}: TEST_AI_MODEL_PROVIDER is {env['TEST_AI_MODEL_PROVIDER']!r}")
            for key in ("TEST_OPENAI_API_KEY", "TEST_AZURE_OPENAI_API_KEY"):
                if key not in env:
                    problems.append(f"{path.name} {where}: does not pass {key}")
            if "EVAL_PROVIDER" in env:
                problems.append(f"{path.name} {where}: EVAL_PROVIDER overrides the run's provider")
    assert checked, "no workflow sets TEST_AI_MODEL_PROVIDER"
    assert problems == [], "\n".join(problems)
