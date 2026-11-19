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
        "TEST_OPENAI_API_KEY", "OPENAI_API_KEY", "TEST_GROQ_API_KEY",
        "TEST_GEMINI_API_KEY", "TEST_AZURE_OPENAI_API_KEY",
        "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_a_missing_openai_key_fails_a_run_meant_to_cover_it(monkeypatch) -> None:
    suite = _suite()
    _clear_keys(monkeypatch)
    monkeypatch.setenv(REQUIRE_ENV, "1")

    with pytest.raises(BaseException) as caught:
        suite._skip_if_no_live_credentials(_spec_for(suite._PROVIDER_OPENAI))

    assert caught.typename == "Failed"
    assert "TEST_OPENAI_API_KEY" in str(caught.value)


def test_a_provider_the_run_no_longer_funds_only_skips(monkeypatch) -> None:
    """Azure OpenAI's account is gone, so the nightly must not still demand it."""
    suite = _suite()
    _clear_keys(monkeypatch)
    monkeypatch.setenv(REQUIRE_ENV, "1")

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


def test_the_test_jobs_no_longer_pass_the_gone_azure_key() -> None:
    """The skip-or-fail rule only decides what happens when a key is absent.

    While a job passes TEST_AZURE_OPENAI_API_KEY, every live Azure spec, and
    the Playwright helper (which tries Azure first), goes on running against
    the account that no longer exists. Unsetting the variable in a test cannot
    catch that, because the test controls its own environment and CI does not;
    the workflows are the thing to assert on. The perf and answer-quality
    workflows still name Azure and are out of this check until they move too.
    """
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    passing_it = sorted(
        name
        for name in ("integration-tests.yml", "sdk-tests.yml")
        if "TEST_AZURE_OPENAI_API_KEY: ${{ secrets." in (workflows / name).read_text()
    )
    assert passing_it == [], (
        "these jobs still hand the tests the gone Azure OpenAI key: " + ", ".join(passing_it)
    )
