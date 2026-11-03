"""The edition hooks leave the open-source behaviour unchanged."""

from __future__ import annotations

from unittest.mock import MagicMock

from app import edition_services
from app.agents.agent_loop import error_classification


def test_model_decorators_return_their_input() -> None:
    model, embeddings = MagicMock(), MagicMock()

    assert edition_services.decorate_chat_model(model, "openAI", "gpt", {}) is model
    assert edition_services.decorate_embeddings(embeddings, "openAI", "embed", {}) is embeddings


def test_configure_app_and_extra_toolsets_do_nothing() -> None:
    app, registry = MagicMock(), MagicMock()

    edition_services.configure_app(app, "query")
    edition_services.register_extra_toolsets(registry)

    assert app.mock_calls == [] and registry.mock_calls == []


def test_a_registered_classifier_is_asked_first(monkeypatch) -> None:
    monkeypatch.setattr(error_classification, "_extra_classifiers", [])
    error_classification.register_error_classifier(
        lambda message: ("custom_code", "Custom message") if "special" in message else None
    )

    assert error_classification.classify_error("a special failure") == ("custom_code", "Custom message")
    assert error_classification.classify_error("rate limit exceeded") != ("custom_code", "Custom message")


def test_a_failing_classifier_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(error_classification, "_extra_classifiers", [])

    def broken(_message: str) -> tuple[str, str] | None:
        raise RuntimeError("classifier bug")

    error_classification.register_error_classifier(broken)
    error_classification.register_error_classifier(
        lambda message: ("custom_code", "Custom message") if "special" in message else None
    )

    assert error_classification.classify_error("a special failure") == ("custom_code", "Custom message")
    assert error_classification.classify_error("rate limit exceeded")[0] == "rate_limit"


async def test_bootstrap_leased_ids_keeps_nothing() -> None:
    """A restart has no live peer, so every leftover flag may be cleared."""
    assert await edition_services.bootstrap_leased_ids(["a", "b"]) == set()


async def test_web_access_gate_allows_searches_and_public_urls_only() -> None:
    from app.web.models import CallerContext
    from app.web.ports import AccessRequest

    gate = edition_services.get_web_access_gate()
    caller = CallerContext(org_id="org-1", user_id="user-1")

    assert (await gate.check(AccessRequest("search", "anything at all", caller))).allowed
    assert (await gate.check(AccessRequest("fetch", "https://example.com/page", caller))).allowed
    for blocked in ("http://localhost/", "http://169.254.169.254/", "file:///etc/passwd", "https:///no-host"):
        decision = await gate.check(AccessRequest("fetch", blocked, caller))
        assert not decision.allowed and decision.reason
