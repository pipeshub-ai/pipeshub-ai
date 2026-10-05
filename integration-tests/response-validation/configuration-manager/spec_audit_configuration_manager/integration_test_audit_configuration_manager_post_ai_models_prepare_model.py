"""Strict OpenAPI audit of POST /api/v1/configurationManager/ai-models/prepare-model.

Negative paths only: a successful call makes the embedding server download and load a model.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import MALFORMED_EMBEDDING_MODEL, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/prepare-model"
PATH = "/ai-models/prepare-model"

# Refused by the controller's own check, so no caller can start a download with it.
REFUSED_BODY: dict[str, Any] = {"model": MALFORMED_EMBEDDING_MODEL}


def test_prepare_model_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, auth=False, json=REFUSED_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_prepare_model_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before the controller's model check, so this is 403 and not 400.
    resp = request_as(second_user, "POST", PATH, json=REFUSED_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {},
        REFUSED_BODY,
        {"model": 123, "trustRemoteCode": False},
    ],
    ids=["missing_model", "model_fails_pattern", "model_not_a_string"],
)
def test_prepare_model_invalid_model_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "error", "message": "A valid model name is required"}
