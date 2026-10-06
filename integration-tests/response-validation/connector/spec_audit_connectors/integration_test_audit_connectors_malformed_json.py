"""Every /api/v1/connectors route audited here answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request reaches neither the path guard nor the token check.
That includes the org-wide vector-store jobs, which are therefore refused here too.
"""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_CONNECTOR_ID,
    MISSING_CONNECTOR_TYPE,
    ConnectorsAuditClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

_ID = MISSING_CONNECTOR_ID


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "/registry", "/connectors/registry"),
        ("GET", f"/registry/{MISSING_CONNECTOR_TYPE}/schema", "/connectors/registry/{connectorType}/schema"),
        ("GET", "/", "/connectors"),
        ("POST", "/", "/connectors"),
        ("GET", "/active", "/connectors/active"),
        ("GET", "/inactive", "/connectors/inactive"),
        ("GET", "/agents/active", "/connectors/agents/active"),
        ("GET", "/configured", "/connectors/configured"),
        ("GET", "/navigate", "/connectors/navigate"),
        ("GET", "/record/lookup", "/connectors/record/lookup"),
        ("GET", f"/{_ID}", "/connectors/{connectorId}"),
        ("DELETE", f"/{_ID}", "/connectors/{connectorId}"),
        ("GET", f"/{_ID}/stats", "/connectors/{connectorId}/stats"),
        ("GET", f"/record/{_ID}/content", "/connectors/record/{recordId}/content"),
        ("POST", "/vector-store/cleanup", "/connectors/vector-store/cleanup"),
        ("POST", "/vector-store/reindex", "/connectors/vector-store/reindex"),
    ],
    ids=[
        "registry",
        "registry_schema",
        "list",
        "create",
        "active",
        "inactive",
        "agents_active",
        "configured",
        "navigate",
        "record_lookup",
        "get_instance",
        "delete_instance",
        "stats",
        "record_content",
        "vector_store_cleanup",
        "vector_store_reindex",
    ],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    connectors_client: ConnectorsAuditClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = connectors_client.send(
        method, sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
