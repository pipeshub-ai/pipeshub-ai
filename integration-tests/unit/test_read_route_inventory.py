"""The read-route inventory matches the spec and the Express routers.

``security/integration_test_unauthorized_reads.py`` calls every documented GET
without a valid token. These tests keep its list honest: the public reads are
exactly the table's, every GET the routers serve is documented (or listed with
a reason), and no table row outlives its route. Source and spec only, no stack.
"""

from __future__ import annotations

import pytest

from helper.admin_routes import discover_admin_routes, discover_routes
from helper.openapi_spec import load_spec
from helper.read_route_table import (
    PUBLIC_READS,
    STATUS_EXCEPTIONS,
    UNDOCUMENTED_READS,
    UNREAD_ROUTERS,
)
from helper.read_routes import PLACEHOLDER_ID, ReadRoute, classify, express_template, read_routes, spec_template

pytestmark = pytest.mark.unit

_MIN_GET_ROUTES = 150


def test_read_routes_come_from_every_get_in_the_spec() -> None:
    spec = {
        "security": [{"bearerAuth": []}],
        "paths": {
            "/b": {"get": {"operationId": "b"}, "post": {"operationId": "ignored"}},
            "/open": {"get": {"operationId": "open", "security": []}},
            "/kb/{kbId}": {"get": {"operationId": "kb"}},
            "/x": {"get": {"operationId": "wellKnown", "servers": [{"url": "/.well-known"}]}},
            "/root": {"get": {"operationId": "root", "servers": [{"url": "/"}]}},
        },
    }
    assert read_routes(spec) == [
        ReadRoute("b", "/b", "/api/v1/b", False),
        ReadRoute("kb", "/kb/{kbId}", f"/api/v1/kb/{PLACEHOLDER_ID}", False),
        ReadRoute("open", "/open", "/api/v1/open", True),
        ReadRoute("root", "/root", "/root", False),
        ReadRoute("wellKnown", "/x", "/.well-known/x", False),
    ]
    assert read_routes(spec)[2].url_path == "/api/v1/open"


def test_the_public_reads_are_exactly_the_table() -> None:
    public = {r.operation_id for r in read_routes(load_spec()) if r.public}
    assert public == PUBLIC_READS, (
        f"public GETs missing from read_route_table.PUBLIC_READS: {sorted(public - PUBLIC_READS)}; "
        f"table rows that are not public in the spec: {sorted(PUBLIC_READS - public)}"
    )


def test_every_express_get_route_is_in_the_spec() -> None:
    documented = {spec_template(r.url_path.replace(PLACEHOLDER_ID, "{}")) for r in read_routes(load_spec())}
    served, unknown = discover_routes("GET")
    assert len(served) >= _MIN_GET_ROUTES, f"only {len(served)} GET routes found; the reader has stopped matching the routers"
    served_keys = {f"GET {express_template(r.path)}" for r in served}
    missing = sorted(k for k in served_keys if k.split(" ", 1)[1] not in documented and k not in UNDOCUMENTED_READS)
    assert not missing, "GET routes the server has and the spec lacks (document them or list them with a reason):\n  " + "\n  ".join(missing)
    stale = sorted(k for k in UNDOCUMENTED_READS if k not in served_keys or k.split(" ", 1)[1] in documented)
    assert not stale, f"UNDOCUMENTED_READS rows that no longer apply: {stale}"
    unlisted = sorted(set(unknown) - set(UNREAD_ROUTERS))
    assert not unlisted, f"router files with an unreadable mount, not in UNREAD_ROUTERS: {unlisted}"
    gone = sorted(f for f in UNREAD_ROUTERS if f not in unknown)
    assert not gone, f"UNREAD_ROUTERS rows that are readable or gone: {gone}"


def test_status_exceptions_name_documented_reads() -> None:
    ids = {r.operation_id for r in read_routes(load_spec())}
    assert not sorted(set(STATUS_EXCEPTIONS) - ids), "STATUS_EXCEPTIONS rows for unknown operations"
    assert all(reason for _, reason in STATUS_EXCEPTIONS.values())


def test_only_401_counts_as_refused() -> None:
    assert classify(401) is None
    assert classify(200) == "answered 200 without a valid token"
    assert classify(302) == "answered 302 without a valid token"
    assert classify(404) == "not served (404): the spec documents a route the server lacks"
    assert classify(502) == "server error 502: not a refusal"
    assert classify(403) == "answered 403, expected one of 401"
    assert classify(403, (401, 403)) is None


def test_admin_route_discovery_is_unchanged() -> None:
    """The shared reader still finds what it found before ``routes_in_file`` was split out."""
    try:
        found = {(r.method, r.path) for r in discover_admin_routes()}
    except AssertionError:
        pytest.skip("discover_admin_routes fails on its own unmounted-router check; test_admin_route_inventory reports it")
    assert len(found) >= 101
    assert {
        ("DELETE", "/api/v1/configurationManager/ai-models/providers/:modelType/:modelKey"),
        ("PATCH", "/api/v1/service-accounts/:id"),
        ("PUT", "/api/v1/users/:id/unblock"),
    } <= found
