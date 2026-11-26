"""Which AI providers a run is held to, and which it may skip.

The workflows pass OpenAI's, Azure OpenAI's and an OpenAI-compatible gateway's
settings and name one of them in the repository variable TEST_AI_MODEL_PROVIDER
(openAI when unset). The named
provider must fail rather than skip when its key is gone — a rotated key
otherwise reads as a passing run — and every other provider skips, even with a
key set, because that key may belong to an account nobody is paying for.
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
        "TEST_OPENAI_COMPATIBLE_API_KEY", "TEST_OPENAI_COMPATIBLE_ENDPOINT",
        "TEST_OPENAI_COMPATIBLE_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_azure(monkeypatch) -> None:
    monkeypatch.setenv("TEST_AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("TEST_AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME", "d")


def _set_compatible(monkeypatch) -> None:
    monkeypatch.setenv("TEST_OPENAI_COMPATIBLE_API_KEY", "k")
    monkeypatch.setenv("TEST_OPENAI_COMPATIBLE_ENDPOINT", "https://gateway.example.com/v1")
    monkeypatch.setenv("TEST_OPENAI_COMPATIBLE_LLM_MODEL", "openai/gpt-5.6-luna")


@pytest.mark.parametrize(
    ("run_provider", "key"),
    [(None, "TEST_OPENAI_API_KEY"), ("openAI", "TEST_OPENAI_API_KEY"),
     ("azureOpenAI", "TEST_AZURE_OPENAI_API_KEY"),
     ("openAICompatible", "TEST_OPENAI_COMPATIBLE_API_KEY")],
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


@pytest.mark.parametrize(
    ("run_provider", "other"),
    [("openAI", "azureOpenAI"), ("azureOpenAI", "openAI"), ("openAICompatible", "openAI"),
     ("openAI", "openAICompatible")],
)
def test_the_other_provider_skips_even_with_its_key_set(monkeypatch, run_provider, other) -> None:
    """Every provider's keys are passed; only the named one may be used."""
    suite = _suite()
    _clear_keys(monkeypatch)
    _set_azure(monkeypatch)
    _set_compatible(monkeypatch)
    monkeypatch.setenv("TEST_OPENAI_API_KEY", "k")
    monkeypatch.setenv(REQUIRE_ENV, "1")
    monkeypatch.setenv("TEST_AI_MODEL_PROVIDER", run_provider)

    with pytest.raises(BaseException) as caught:
        suite._skip_if_no_live_credentials(_spec_for(other))

    assert caught.typename == "Skipped"


def test_a_named_provider_without_its_key_is_not_swapped_for_another(monkeypatch) -> None:
    """The spec for "any live provider" is the named one even when only the other
    provider's key is set, so the missing key fails instead of being skipped."""
    suite = _suite()
    _clear_keys(monkeypatch)
    _set_azure(monkeypatch)
    monkeypatch.setenv("TEST_AI_MODEL_PROVIDER", "openAI")

    assert suite._spec_for_a_live_provider().provider_id == suite._PROVIDER_OPENAI


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
# gpt-5.4-nano (the helper's local default) skipped tools in the agent tests.
_OPENAI_MODEL_FROM_VARIABLE = "${{ vars.TEST_OPENAI_LLM_MODEL || 'gpt-5.6-luna' }}"
# The gateway's ids for the same models (Bifrost names them provider/model).
_COMPATIBLE_MODELS_FROM_VARIABLES = {
    "TEST_OPENAI_COMPATIBLE_LLM_MODEL": "${{ vars.TEST_OPENAI_COMPATIBLE_LLM_MODEL || 'openai/gpt-5.6-luna' }}",
    "TEST_OPENAI_COMPATIBLE_EMBEDDING_MODEL": (
        "${{ vars.TEST_OPENAI_COMPATIBLE_EMBEDDING_MODEL || 'openai/text-embedding-3-small' }}"
    ),
}


def _env_blocks(doc: dict):
    """Every env mapping in a workflow (top level, each job, each step), with the
    mappings it inherits from."""
    top = doc.get("env") if isinstance(doc.get("env"), dict) else {}
    if top:
        yield "env", top, {}
    for job_name, job in (doc.get("jobs") or {}).items():
        job_env = job.get("env") if isinstance(job.get("env"), dict) else {}
        if job_env:
            yield job_name, job_env, top
        for step in job.get("steps") or []:
            if isinstance(step.get("env"), dict):
                yield f"{job_name}/{step.get('name', '?')}", step["env"], {**top, **job_env}


def test_switching_provider_is_one_repository_variable() -> None:
    """Every job that names an AI provider takes it from the repository variable
    and gets every provider's settings, so switching needs no workflow change.

    The Ollama workflow runs a local model and is the one exception.
    """
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    problems, checked = [], 0
    for path in sorted(workflows.glob("*.yml")):
        if path.name == "integration-tests-ollama.yml":
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for where, env, inherited in _env_blocks(doc):
            # Checked on every mapping: a step can override a job's provider on its own.
            if "EVAL_PROVIDER" in env:
                problems.append(f"{path.name} {where}: EVAL_PROVIDER overrides the run's provider")
            if "TEST_AI_MODEL_PROVIDER" not in env:
                # A key with no provider named sends the helpers back to Azure-first.
                if {"TEST_OPENAI_API_KEY", "TEST_AZURE_OPENAI_API_KEY", "TEST_OPENAI_COMPATIBLE_API_KEY"} & env.keys() and (
                    "TEST_AI_MODEL_PROVIDER" not in inherited
                ):
                    problems.append(f"{path.name} {where}: passes an AI key without TEST_AI_MODEL_PROVIDER")
                continue
            checked += 1
            if env["TEST_AI_MODEL_PROVIDER"] != _PROVIDER_FROM_VARIABLE:
                problems.append(f"{path.name} {where}: TEST_AI_MODEL_PROVIDER is {env['TEST_AI_MODEL_PROVIDER']!r}")
            for key in (
                "TEST_OPENAI_API_KEY", "TEST_AZURE_OPENAI_API_KEY",
                "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
                "TEST_OPENAI_COMPATIBLE_API_KEY", "TEST_OPENAI_COMPATIBLE_ENDPOINT",
                "TEST_OPENAI_COMPATIBLE_LLM_MODEL", "TEST_OPENAI_COMPATIBLE_EMBEDDING_MODEL",
            ):
                if key not in env:
                    problems.append(f"{path.name} {where}: does not pass {key}")
            if env.get("TEST_OPENAI_LLM_MODEL") != _OPENAI_MODEL_FROM_VARIABLE:
                problems.append(f"{path.name} {where}: TEST_OPENAI_LLM_MODEL is {env.get('TEST_OPENAI_LLM_MODEL')!r}")
            for key, expected in _COMPATIBLE_MODELS_FROM_VARIABLES.items():
                if env.get(key) != expected:
                    problems.append(f"{path.name} {where}: {key} is {env.get(key)!r}")
    assert checked, "no workflow sets TEST_AI_MODEL_PROVIDER"
    assert problems == [], "\n".join(problems)
