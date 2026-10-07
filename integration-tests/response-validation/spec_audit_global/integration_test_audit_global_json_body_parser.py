"""express.json fails on every route, before authentication, as a 500 INTERNAL_ERROR.

The parser is mounted app-wide (limit 10 MB) ahead of every router and runs for any
method whose Content-Type is application/json. Its own errors (400 for a body that does
not parse, 413 for one over the limit) carry no BaseError, so the error middleware
reports them as an internal error.
"""

from __future__ import annotations

import pytest
from global_audit_support import (
    INTERNAL_ERROR,
    JSON_HEADERS,
    JSON_LIMIT_BYTES,
    MALFORMED_JSON_BODY,
    MALFORMED_JSON_DESCRIPTION,
    OVERSIZED_JSON_BODY,
    OVERSIZED_JSON_DESCRIPTION,
    Operation,
    call,
    error_of,
    operations,
    params_for,
)
from strict_openapi import assert_strict_openapi_exchange

from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit


def _assert_internal_error(resp, op: Operation) -> str:
    assert resp.status_code == 500, resp.text[:500]
    assert error_of(resp)["code"] == INTERNAL_ERROR, resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    return (op.response("500") or {}).get("description", "")


@pytest.mark.parametrize("op", params_for(operations()))
def test_a_json_body_that_does_not_parse_is_an_internal_error(
    pipeshub_client: PipeshubClient, op: Operation
) -> None:
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        headers=JSON_HEADERS, data=MALFORMED_JSON_BODY,
    )
    described = _assert_internal_error(resp, op)
    assert MALFORMED_JSON_DESCRIPTION.search(described), f"the 500 of {op.id} does not mention the parse failure"


@pytest.mark.parametrize("op", params_for(operations()))
def test_a_json_body_over_the_limit_is_an_internal_error(pipeshub_client: PipeshubClient, op: Operation) -> None:
    assert len(OVERSIZED_JSON_BODY) == JSON_LIMIT_BYTES + 1
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        headers=JSON_HEADERS, data=OVERSIZED_JSON_BODY,
    )
    described = _assert_internal_error(resp, op)
    assert OVERSIZED_JSON_DESCRIPTION.search(described), f"the 500 of {op.id} does not mention the size limit"
