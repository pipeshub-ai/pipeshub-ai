"""`setup-pipeshub`: one idempotent step from a fresh stack to a benchmark-ready one."""

from __future__ import annotations

from typing import Any

import pytest
from frames_testkit import FakeSession

from benchmarks.harness.config import EmbeddingSelector
from benchmarks.harness.credentials import Credentials
from benchmarks.harness.errors import ConfigError
from benchmarks.harness.systems.pipeshub import setup


class _Response:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:  # noqa: ANN401
        self.status_code = status_code
        self._body = body
        self.ok = status_code < 400

    def json(self) -> Any:  # noqa: ANN401
        return self._body


_SELECTOR = EmbeddingSelector(model="text-embedding-3-small", provider="azureOpenAI")


class TestWaitUntilHealthy:
    def test_waits_for_every_service_not_just_the_api(self) -> None:
        answers = iter([
            _Response(200, {"status": "healthy"}),
            _Response(200, {"status": "unhealthy", "services": {"indexing": "unhealthy"}}),
            _Response(503, None),
            _Response(200, {"status": "healthy"}),
        ])
        seen: list[str] = []

        def get(url: str, **_: Any) -> _Response:
            seen.append(url)
            return next(answers)

        setup.wait_until_healthy("http://pipeshub.test", get=get, sleep=lambda _s: None)

        assert seen[0].endswith("/api/v1/health")
        assert all(url.endswith("/api/v1/health/services") for url in seen[1:])
        assert len(seen) == 4

    def test_gives_up_with_the_path_that_never_became_healthy(self) -> None:
        with pytest.raises(ConfigError, match="health/services"):
            setup.wait_until_healthy(
                "http://pipeshub.test", timeout_s=0,
                get=lambda url, **_: _Response(200, {"status": "healthy" if url.endswith("/health") else "unhealthy"}),
                sleep=lambda _s: None,
            )


class TestEnsureOrg:
    @pytest.mark.parametrize(("status", "created"), [(200, True), (400, False)])
    def test_an_existing_org_is_not_an_error(self, status: int, created: bool) -> None:
        sent: list[dict[str, Any]] = []

        def post(url: str, *, json: dict[str, Any], **_: Any) -> _Response:
            sent.append(json)
            return _Response(status)

        assert setup.ensure_org("http://pipeshub.test", "a@b.test", "pw", post=post) is created
        assert sent[0]["contactEmail"] == "a@b.test" and sent[0]["password"] == sent[0]["confirmPassword"]

    def test_a_server_error_stops_setup(self) -> None:
        with pytest.raises(ConfigError):
            setup.ensure_org("http://pipeshub.test", "a@b.test", "pw", post=lambda *_a, **_k: _Response(502))


class TestFeatureFlags:
    def test_merges_into_existing_flags_and_keeps_the_upload_limit(self) -> None:
        def handler(method: str, _path: str, kwargs: dict[str, Any]) -> _Response:
            if method == "GET":
                return _Response(200, {"featureFlags": {"ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": True},
                                       "fileUploadMaxSizeBytes": 1000})
            return _Response(200, {})

        session = FakeSession(handler)
        setup.apply_feature_flags(session, setup.BENCHMARK_FEATURE_FLAGS)

        (_method, _path, kwargs) = session.calls[-1]
        assert kwargs["json"] == {
            "featureFlags": {"ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": False},
            "fileUploadMaxSizeBytes": 1000,
        }

    def test_benchmark_runs_without_user_context(self) -> None:
        assert setup.BENCHMARK_FEATURE_FLAGS == {"ENABLE_USER_CONTEXT": False}


class TestEnsureEmbedding:
    def test_registers_the_configured_model_when_none_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registered: list[dict[str, Any]] = []
        monkeypatch.setattr(setup, "seed_explicit_embedding", lambda _s, **kw: registered.append(kw) or type(
            "Seeded", (), {"provider": kw["provider"], "model_name": kw["model_name"], "model_key": "k1"})())
        session = FakeSession(lambda *_: _Response(200, {"models": []}))
        credentials = Credentials.from_env({"AZURE_OPENAI_API_KEY": "secret"})

        setup.ensure_embedding(session, _SELECTOR, credentials)

        assert registered == [{"provider": "azureOpenAI", "model_name": "text-embedding-3-small",
                               "api_key": "secret", "deployment_name": None}]

    def test_verifies_an_existing_model_instead_of_adding_another(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(setup, "seed_explicit_embedding", lambda *_a, **_k: pytest.fail("must not register"))
        existing = {"models": [{"provider": "azureOpenAI", "isDefault": True,
                                "configuration": {"model": "text-embedding-3-small"}}]}
        setup.ensure_embedding(FakeSession(lambda *_: _Response(200, existing)), _SELECTOR, Credentials.from_env({}))

    def test_a_different_existing_model_is_refused(self) -> None:
        existing = {"models": [{"provider": "openAI", "isDefault": True, "configuration": {"model": "bge-large"}}]}
        with pytest.raises(ConfigError, match="different vector space"):
            setup.ensure_embedding(FakeSession(lambda *_: _Response(200, existing)), _SELECTOR, Credentials.from_env({}))


def test_setup_refuses_to_run_without_the_benchmark_user() -> None:
    with pytest.raises(ConfigError, match="PIPESHUB_TEST_USER_EMAIL"):
        setup.setup_pipeshub("http://pipeshub.test", _SELECTOR, Credentials.from_env({}))
