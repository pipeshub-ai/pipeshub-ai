"""The query and connectors apps mount a build's own routes only when it has some.

The default build has none: ``query_extension_router`` and ``entity_extension_router`` are
``None``, and both apps start and serve their shared routes all the same."""

from app import edition_config
from tests.support import edition_defaults


def _paths(routes: object) -> set[str]:
    return {getattr(route, "path", "") for route in (routes or [])}


def test_the_build_has_extension_routers_only_when_it_says_so() -> None:
    expected = edition_defaults.extension_routers()
    assert (edition_config.query_extension_router is not None) is expected
    assert (edition_config.entity_extension_router is not None) is expected


def test_the_query_app_serves_its_routes_and_any_extension_routes_under_api_v1() -> None:
    from app.query_main import app

    served = _paths(app.routes)
    extension = edition_config.query_extension_router
    assert {f"/api/v1{path}" for path in _paths(getattr(extension, "routes", None))} <= served
    assert "/api/v1/agent/{agent_id}/readiness" in served
    assert "/api/v1/chat/stream" in served


def test_the_connectors_app_serves_its_routes_and_any_entity_extension_routes() -> None:
    from app.connectors_main import app

    served = _paths(app.routes)
    extension = edition_config.entity_extension_router
    assert _paths(getattr(extension, "routes", None)) <= served
    assert any(path.startswith("/api/v1/entity") for path in served)
