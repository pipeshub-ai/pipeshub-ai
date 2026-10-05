"""Strict OpenAPI audit of PUT /api/v1/configurationManager/slack-bot/:configId."""

from __future__ import annotations

import uuid

import pytest
from configuration_manager_audit_support import (
    MISSING_SLACK_BOT_CONFIG_ID,
    SECRET_PLACEHOLDER,
    SeedSlackBot,
    slack_bot_body,
)
from helper.clients.config_client import ConfigClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/slack-bot/:configId"


def test_update_renames_config_and_keeps_secrets_masked(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    seeded = seed_slack_bot()
    new_name = f"spec-audit renamed {uuid.uuid4().hex[:8]}"

    # The edit form never saw the real secrets, so it re-submits the placeholder.
    resp = config_client.put(
        f"/slack-bot/{seeded['id']}",
        json={
            "name": new_name,
            "botToken": SECRET_PLACEHOLDER,
            "signingSecret": SECRET_PLACEHOLDER,
        },
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success"
    config = body["config"]
    assert config["id"] == seeded["id"]
    assert config["name"] == new_name
    assert config["agentId"] is None
    assert config["createdAt"] == seeded["createdAt"]
    assert config["botToken"] == SECRET_PLACEHOLDER
    assert config["signingSecret"] == SECRET_PLACEHOLDER


def test_update_unknown_config_is_not_found(config_client: ConfigClient) -> None:
    resp = config_client.put(
        f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}", json=slack_bot_body()
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_without_name_is_rejected(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    seeded = seed_slack_bot()
    body = slack_bot_body()
    del body["name"]

    resp = config_client.put(f"/slack-bot/{seeded['id']}", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_to_agent_linked_elsewhere_is_rejected(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    # agentId is stored as given, never looked up, so a made-up one is enough.
    agent_id = f"spec-audit-agent-{uuid.uuid4().hex}"
    seed_slack_bot(agentId=agent_id)
    other = seed_slack_bot()

    resp = config_client.put(
        f"/slack-bot/{other['id']}", json=slack_bot_body(agentId=agent_id)
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.put(
        f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}", auth=False, json=slack_bot_body()
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
