"""The edition hooks leave the open-source behaviour unchanged."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import edition, edition_services
from app.agents.agent_loop import error_classification
from tests.edition_overlay import edition_tree_present


def test_model_decorators_return_their_input() -> None:
    model, embeddings = MagicMock(), MagicMock()

    assert edition_services.decorate_chat_model(model, "openAI", "gpt", {}) is model
    assert edition_services.decorate_embeddings(embeddings, "openAI", "embed", {}) is embeddings


def test_a_model_with_no_saved_credentials_may_use_the_servers_own() -> None:
    assert edition_services.ambient_cloud_credentials_allowed({"provider": "openAI", "configuration": {}}) is True


@pytest.mark.asyncio
async def test_binding_a_model_probe_records_nothing() -> None:
    config_service = MagicMock()

    assert await edition_services.bind_request_org_root(config_service) is None
    assert config_service.mock_calls == []


@pytest.mark.skipif(edition_tree_present(), reason="these hooks are another edition's in this checkout")
def test_configure_app_and_extra_toolsets_do_nothing() -> None:
    app, registry = MagicMock(), MagicMock()

    edition_services.configure_app(app, "query")
    edition_services.register_extra_toolsets(registry)

    assert app.mock_calls == [] and registry.mock_calls == []


@pytest.mark.skipif(edition_tree_present(), reason="these hooks are another edition's in this checkout")
@pytest.mark.parametrize("key", ["/services/aiModels", "a" * 24 + "/services/aiModels", ""])
def test_the_key_value_store_binds_no_org_for_any_key(key: str) -> None:
    assert edition.kv_key_binds_org(key) is False


@pytest.mark.skipif(edition_tree_present(), reason="these hooks are another edition's in this checkout")
def test_the_database_gets_no_org_limits_hook() -> None:
    assert edition.db_org_limits() is None


APP = Path(edition.__file__).parent
# The sandbox's Docker socket proxy reads no PipesHub setting.
READS_NO_SETTINGS = {"docker_proxy_main.py"}


def _is_check(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "check_runtime_settings"
    )


def _builds_the_service(node: ast.stmt) -> bool:
    """The service's container (which holds its stores), its app, or the edition setup of that app."""
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"container", "app"} for t in node.targets):
        return True
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "configure_app"


@pytest.mark.parametrize("main", sorted(p.name for p in APP.glob("*_main.py") if p.name not in READS_NO_SETTINGS))
def test_every_service_runs_the_settings_check_at_load_before_building_itself(main: str) -> None:
    # Called by the main itself: configure_app is replaceable, so a call inside it can be dropped.
    body = ast.parse((APP / main).read_text()).body
    check = next((i for i, node in enumerate(body) if _is_check(node)), None)
    build = next((i for i, node in enumerate(body) if _builds_the_service(node)), len(body))

    assert check is not None, f"{main} never calls check_runtime_settings() at module level"
    assert check < build, f"{main} builds its container or app before check_runtime_settings()"


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


def test_sandbox_org_share_is_the_editions_default() -> None:
    from app import edition
    from tests.support import edition_defaults

    assert edition.sandbox_org_share() == edition_defaults.SANDBOX_ORG_SHARE
