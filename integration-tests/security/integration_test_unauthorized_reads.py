"""Every documented read route refuses a request with no token or a malformed one.

needs: service:stack

One parameter per non-public GET in the merged OpenAPI spec and per case: no
``Authorization`` header, and ``Bearer not-a-token``. Each must answer 401. A 2xx or
3xx means the route is open; a 404 means the spec documents a route the server does
not serve; a 5xx is not a refusal. A route that legitimately answers something else
is listed in ``helper/read_route_table.py`` ``STATUS_EXCEPTIONS`` with a reason.

Path parameters are filled with a zero id: authentication runs before lookup, so the
value does not matter. A route that validates before it authenticates answers 400 and
shows up here. ``unit/test_read_route_inventory.py`` keeps the spec in step with the routers.
"""

from __future__ import annotations

import os

import pytest
import requests

from helper.openapi_spec import load_spec
from helper.read_route_table import STATUS_EXCEPTIONS
from helper.read_routes import classify, read_routes

pytestmark = [pytest.mark.integration, pytest.mark.security]

_CASES = {"no-token": {}, "bad-token": {"Authorization": "Bearer not-a-token"}}
_ROUTES = [r for r in read_routes(load_spec()) if not r.public]
# An empty list would make the parametrized test vanish and the run look green.
assert len(_ROUTES) >= 150, f"only {len(_ROUTES)} non-public read routes were found in the spec; the loader is broken"


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.operation_id)
def test_unauthorized_read_is_refused(route, case: str) -> None:
    base_url = os.environ["PIPESHUB_BASE_URL"].rstrip("/")
    response = requests.get(
        f"{base_url}{route.url_path}", headers=_CASES[case], allow_redirects=False, timeout=30,
    )
    allowed = STATUS_EXCEPTIONS.get(route.operation_id, ((401,), ""))[0]
    problem = classify(response.status_code, allowed)
    assert problem is None, (
        f"{route.operation_id} GET {route.url_path} ({case}): {problem}; body: {response.text[:200]!r}"
    )
