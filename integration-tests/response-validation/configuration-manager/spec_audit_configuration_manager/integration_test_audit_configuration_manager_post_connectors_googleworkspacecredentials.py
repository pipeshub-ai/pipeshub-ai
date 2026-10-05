"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectors/googleWorkspaceCredentials.

Negative paths only: a successful call stores Google Workspace credentials for the org
(or for the admin, on an individual account) and no route puts the previous value back.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/googleWorkspaceCredentials"
PATH = "/connectors/googleWorkspaceCredentials"
UPLOAD_FIELD = "googleWorkspaceCredentials"

UploadFiles = dict[str, tuple[str, bytes, str]]


def _upload(content: bytes, mimetype: str, filename: str = "credentials.json") -> UploadFiles:
    return {UPLOAD_FIELD: (filename, content, mimetype)}


def test_upload_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, auth=False, files=_upload(b"{}", "application/json"))

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", PATH, files=_upload(b"{}", "application/json"))

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "request_kwargs",
    [
        # Refused by the multer fileFilter: only application/json is allowed.
        pytest.param(
            {"files": _upload(b"not json", "text/plain", "credentials.txt")},
            id="file-type-not-json",
        ),
        # Passes the fileFilter, then JSON.parse fails in the upload processor.
        pytest.param(
            {"files": _upload(b"{not json", "application/json")},
            id="file-content-not-json",
        ),
        # No upload and no fields: an individual org fails the token schema, a business
        # org has neither fileChanged nor adminEmail. Both stop before any write.
        pytest.param({"json": {}}, id="empty-body"),
    ],
)
def test_upload_rejects_invalid_request(config_client: ConfigClient, request_kwargs: dict[str, Any]) -> None:
    resp = config_client.post(PATH, **request_kwargs)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
