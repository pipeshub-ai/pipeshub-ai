"""Every /api/v1/toolsets route answers 400 to markup anywhere in the query string or body.

A global sanitizer runs right after the body parser, before the router: it refuses
the request when any query or JSON body string holds an HTML tag, whether or not the
route reads that field, and it does so before the token check.
"""

from __future__ import annotations

import pytest
import requests
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    HTML_REFUSAL,
    HTML_VALUE,
    SHARED_BEHAVIOUR_OPERATIONS,
    TOOLSETS_BASE,
    ToolsetsClient,
    error_of,
)

pytestmark = pytest.mark.spec_audit

BODY_METHODS = {"POST", "PUT"}


def _assert_html_refusal(resp: requests.Response, route: str) -> None:
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert error["message"].startswith(HTML_REFUSAL), resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{TOOLSETS_BASE}{route}")


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [operation[1:] for operation in SHARED_BEHAVIOUR_OPERATIONS],
    ids=[operation[0] for operation in SHARED_BEHAVIOUR_OPERATIONS],
)
def test_markup_in_an_unread_query_parameter_is_refused_before_the_token_check(
    toolsets_client: ToolsetsClient, method: str, sub_path: str, route: str
) -> None:
    resp = getattr(toolsets_client, method.lower())(
        sub_path, auth=False, params={"specAuditUnread": HTML_VALUE}
    )
    _assert_html_refusal(resp, route)


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [operation[1:] for operation in SHARED_BEHAVIOUR_OPERATIONS if operation[1] in BODY_METHODS],
    ids=[operation[0] for operation in SHARED_BEHAVIOUR_OPERATIONS if operation[1] in BODY_METHODS],
)
def test_markup_in_a_nested_body_string_is_refused_before_the_token_check(
    toolsets_client: ToolsetsClient, method: str, sub_path: str, route: str
) -> None:
    resp = getattr(toolsets_client, method.lower())(
        sub_path, auth=False, json={"specAuditUnread": {"nested": [HTML_VALUE]}}
    )
    _assert_html_refusal(resp, route)
