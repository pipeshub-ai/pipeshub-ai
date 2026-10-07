"""Strict OpenAPI audit of POST /api/v1/configurationManager/ai-models/prepare-model.

No validator: the handler checks ``model`` itself, then proxies to the embedding server.
The success case uses a model the embedding server has already loaded, so nothing is
downloaded; the first download-progress frame proves it before the call is made.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MALFORMED_EMBEDDING_MODEL,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/prepare-model"
PATH = "/ai-models/prepare-model"
# The embedding server's default local model; loaded at start-up.
LOADED_MODEL = "BAAI/bge-large-en-v1.5"

# Refused by the controller's own check, so no caller can start a download with it.
REFUSED_BODY: dict[str, Any] = {"model": MALFORMED_EMBEDDING_MODEL}


def _first_progress_status(config_client: ConfigClient, model: str) -> str:
    resp = config_client.get("/ai-models/download-progress", params={"model": model}, stream=True, timeout=(10, 20))
    try:
        assert resp.status_code == 200, resp.text[:300]
        for line in resp.iter_lines():
            if line.startswith(b"data: "):
                return str(json.loads(line[len(b"data: "):])["status"])
    finally:
        resp.close()
    raise AssertionError("the download-progress stream ended without a frame")


def test_preparing_a_model_that_is_already_loaded_answers_ready(config_client: ConfigClient) -> None:
    status = _first_progress_status(config_client, LOADED_MODEL)
    if status != "ready":
        pytest.fail(f"environment: {LOADED_MODEL} is not loaded on the embedding server ({status}); preparing it would download it")

    resp = config_client.post(PATH, json={"model": LOADED_MODEL})

    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["model"], body["status"], body["progress"], body["error"]) == (LOADED_MODEL, "ready", 100.0, None)


def test_remote_code_is_refused_by_the_embedding_server(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, json={"model": LOADED_MODEL, "trustRemoteCode": True})

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"].startswith("trust_remote_code is disabled on this server.")


def test_prepare_model_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, auth=False, json=REFUSED_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [INVALID_BEARER_HEADERS], ids=["invalid-token"])
def test_prepare_model_with_invalid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str]) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=REFUSED_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_prepare_model_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before the controller's model check, so this is 403 and not 400.
    resp = request_as(second_user, "POST", PATH, json=REFUSED_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.post(PATH, auth=False, headers=narrow_scope_headers, json=REFUSED_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {},
        REFUSED_BODY,
        {"model": ""},
        {"model": "owner/name/extra"},
        {"model": 123, "trustRemoteCode": False},
    ],
    ids=["missing_model", "model_fails_pattern", "model_empty", "model_with_two_slashes", "model_not_a_string"],
)
def test_prepare_model_invalid_model_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert resp.json() == {"status": "error", "message": "A valid model name is required"}


def test_trust_remote_code_of_another_type_is_read_as_a_boolean(config_client: ConfigClient) -> None:
    # No validator: Boolean("no") is true, so a non-empty string asks for remote code.
    with outside_request_contract("trustRemoteCode sent as a string, which the handler coerces"):
        resp = config_client.post(PATH, json={"model": LOADED_MODEL, "trustRemoteCode": "no"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["detail"].startswith("trust_remote_code is disabled on this server.")
