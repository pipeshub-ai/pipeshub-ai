"""Every /api/v1/crawlingManager route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request reaches neither the path guard nor the token check.
That includes the org-wide DELETE /schedule/all, which is therefore refused here too.
"""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_CONNECTOR_ID,
    ROUTE_PAUSE,
    ROUTE_REMOVE,
    ROUTE_RESUME,
    ROUTE_SCHEDULE,
    ROUTE_SCHEDULE_ALL,
    ROUTE_STATS,
    SEED_CONNECTOR_TYPE,
    CrawlingManagerClient,
    error_code,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

_CONNECTOR = f"/{SEED_CONNECTOR_TYPE}/{MISSING_CONNECTOR_ID}"


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("POST", f"{_CONNECTOR}/schedule", ROUTE_SCHEDULE),
        ("GET", f"{_CONNECTOR}/schedule", ROUTE_SCHEDULE),
        ("GET", "/schedule/all", ROUTE_SCHEDULE_ALL),
        ("DELETE", "/schedule/all", ROUTE_SCHEDULE_ALL),
        ("DELETE", f"{_CONNECTOR}/remove", ROUTE_REMOVE),
        ("POST", f"{_CONNECTOR}/pause", ROUTE_PAUSE),
        ("POST", f"{_CONNECTOR}/resume", ROUTE_RESUME),
        ("GET", "/stats", ROUTE_STATS),
    ],
    ids=[
        "schedule",
        "get_schedule",
        "list_all",
        "remove_all",
        "remove",
        "pause",
        "resume",
        "stats",
    ],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    crawling_manager_client: CrawlingManagerClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = crawling_manager_client.send(
        method, sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert error_code(resp) == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
