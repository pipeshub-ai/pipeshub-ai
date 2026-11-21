"""Every read route (GET) the API documents, and the Express routes that should match.

The unauthorized-read test calls each documented GET without a valid token. The spec
is its list; ``unit/test_read_route_inventory.py`` checks that list against the
routers, so a route that is served but undocumented (or documented but not served)
fails a test instead of escaping the check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PLACEHOLDER_ID = "000000000000000000000000"
DEFAULT_PREFIX = "/api/v1"
_PARAM = re.compile(r"\{[^/}]+\}")
_EXPRESS_PARAM = re.compile(r":[A-Za-z_]\w*")


@dataclass(frozen=True)
class ReadRoute:
    operation_id: str
    path: str
    url_path: str
    public: bool


def _prefix(path_item: dict[str, Any], op: dict[str, Any]) -> str:
    # The spec-level servers list is the instance URL placeholder, not a route prefix.
    for node in (op, path_item):
        servers = node.get("servers")
        if servers:
            return servers[0]["url"].replace("{instance_url}", "").rstrip("/")
    return DEFAULT_PREFIX


def read_routes(spec: dict[str, Any]) -> list[ReadRoute]:
    """Every ``get`` in the spec, sorted by operationId."""
    routes = []
    for path, item in spec.get("paths", {}).items():
        op = item.get("get")
        if not op:
            continue
        prefix = _prefix(item, op)
        routes.append(ReadRoute(
            operation_id=op["operationId"],
            path=path,
            url_path=prefix + _PARAM.sub(PLACEHOLDER_ID, path),
            public=op.get("security") == [],
        ))
    return sorted(routes, key=lambda r: r.operation_id)


def spec_template(path: str) -> str:
    return _PARAM.sub("{}", path).rstrip("/") or "/"


def express_template(path: str) -> str:
    return _EXPRESS_PARAM.sub("{}", path).rstrip("/") or "/"


def classify(status: int, allowed: tuple[int, ...] = (401,)) -> str | None:
    """``None`` when the answer is an expected refusal, else why it is not."""
    if status in allowed:
        return None
    if 200 <= status < 400:
        return f"answered {status} without a valid token"
    if status == 404:
        return "not served (404): the spec documents a route the server lacks"
    if status >= 500:
        return f"server error {status}: not a refusal"
    return f"answered {status}, expected one of {', '.join(map(str, allowed))}"
