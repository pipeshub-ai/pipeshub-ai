"""Strict OpenAPI audit of POST /api/v1/configurationManager/slack-bot."""

from __future__ import annotations

import uuid

import pytest
from configuration_manager_audit_support import (
    SECRET_PLACEHOLDER,
    SeedSlackBot,
    request_as,
    slack_bot_body,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/slack-bot"
PATH = "/slack-bot"


def test_create_slack_bot_returns_masked_config(config_client: ConfigClient) -> None:
    body = slack_bot_body()

    resp = config_client.post(PATH, json=body)
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        payload = resp.json()
        assert payload["status"] == "success"
        config = payload["config"]
        assert config["name"] == body["name"]
        assert config["agentId"] is None
        assert config["botToken"] == SECRET_PLACEHOLDER
        assert config["signingSecret"] == SECRET_PLACEHOLDER
    finally:
        if resp.status_code == 200:
            removed = config_client.delete(f"{PATH}/{resp.json()['config']['id']}")
            assert removed.status_code == 200, removed.text[:500]


def test_create_slack_bot_rejects_agent_already_linked(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    # The agent id is never looked up, only compared with the stored configs.
    agent_id = f"spec-audit-agent-{uuid.uuid4().hex}"
    seed_slack_bot(agentId=agent_id)

    resp = config_client.post(PATH, json=slack_bot_body(agentId=agent_id))
    if resp.status_code == 200:
        config_client.delete(f"{PATH}/{resp.json()['config']['id']}")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_slack_bot_missing_bot_token_is_rejected(config_client: ConfigClient) -> None:
    body = slack_bot_body()
    del body["botToken"]

    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_slack_bot_requires_token(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, auth=False, json=slack_bot_body())
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_slack_bot_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before zod, so even this valid body never reaches the store.
    resp = request_as(second_user, "POST", PATH, json=slack_bot_body())
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
