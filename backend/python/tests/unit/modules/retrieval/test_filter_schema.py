from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import app.edition_config  # noqa: F401  (loads the route modules in an order that avoids their import cycle)
from app.api.routes.chatbot import ChatQuery
from app.api.routes.search import SearchQuery
from app.modules.retrieval.filter_schema import (
    ALLOWED_REQUEST_FILTER_KEYS,
    PUBLIC_REQUEST_FILTER_KEYS,
    SERVER_SET_REQUEST_FILTER_KEYS,
    validate_request_filters,
)

SPEC = (
    Path(__file__).resolve().parents[4].parent
    / "nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
)


def test_known_keys_pass_unchanged() -> None:
    filters = {"apps": ["a"], "kb": [], "departments": ["Finance"], "entityFilters": {"name": "x"}, "topics": None}
    assert validate_request_filters(filters) is filters
    assert validate_request_filters(None) is None


def test_unknown_key_lists_allowed_keys() -> None:
    with pytest.raises(ValueError) as exc:
        validate_request_filters({"status": ["open"], "connectors": [], "apps": []})
    message = str(exc.value)
    assert "Unknown filter key(s): connectors, status" in message
    assert all(key in message for key in ALLOWED_REQUEST_FILTER_KEYS)


@pytest.mark.parametrize("filters", [{"apps": "a"}, {"departments": [1]}, {"entityFilters": ["x"]}])
def test_wrong_type_is_refused(filters) -> None:
    with pytest.raises(ValueError, match="must be"):
        validate_request_filters(filters)


@pytest.mark.parametrize("model", [ChatQuery, SearchQuery])
def test_request_models_refuse_unknown_filter(model) -> None:
    with pytest.raises(ValidationError, match="Allowed keys"):
        model(query="q", filters={"connectors": ["x"]})
    assert model(query="q", filters={"apps": ["a"]}).filters == {"apps": ["a"]}


def test_keys_match_openapi_filters_schema() -> None:
    if not SPEC.exists():
        pytest.skip("OpenAPI spec not in this checkout")
    spec = yaml.safe_load(SPEC.read_text())
    documented = set(spec["components"]["schemas"]["Filters"]["properties"]) - {"where"}
    assert documented == set(PUBLIC_REQUEST_FILTER_KEYS)
    assert set(PUBLIC_REQUEST_FILTER_KEYS) <= set(ALLOWED_REQUEST_FILTER_KEYS)


def test_server_injected_keys_ride_outside_client_filters() -> None:
    # Node sends project bounds as top-level fields; strictScope also rides at the top level.
    query = ChatQuery(query="q", filters={"apps": []}, strictScope=True, allowedFilters={"projectApps": ["a"]})
    assert query.allowedFilters == {"projectApps": ["a"]}


def test_strict_scope_is_an_internal_boolean_key() -> None:
    # The service puts it into the filters of its own follow-up searches (an "outside dates" search), so its
    # own validator must accept it; it is not a public key, and only a boolean is accepted.
    filters = {"apps": ["a"], "strictScope": True}
    assert validate_request_filters(filters) is filters
    assert "strictScope" not in PUBLIC_REQUEST_FILTER_KEYS
    with pytest.raises(ValueError, match="strictScope"):
        validate_request_filters({"strictScope": "yes"})
    assert SearchQuery(query="q", filters=filters).filters == filters


def test_server_set_bounds_are_accepted_so_the_route_can_drop_them() -> None:
    # A client that sends a bound the server sets itself is not refused; the retrieval entry points drop it.
    filters = {"apps": ["a"], "connectionApps": ["x"], "allowedApps": ["y"], "selectionApps": ["z"]}
    assert validate_request_filters(filters) is filters
    assert not set(SERVER_SET_REQUEST_FILTER_KEYS) & set(ALLOWED_REQUEST_FILTER_KEYS)
    with pytest.raises(ValueError, match="must be"):
        validate_request_filters({"connectionApps": "x"})
