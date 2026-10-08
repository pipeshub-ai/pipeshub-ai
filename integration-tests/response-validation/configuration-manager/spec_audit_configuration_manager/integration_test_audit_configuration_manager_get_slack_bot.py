"""Strict OpenAPI audit of GET /api/v1/configurationManager/slack-bot."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    SECRET_PLACEHOLDER,
    SeedSlackBot,
    request_as,
    slack_bot_body,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/slack-bot"


def test_admin_lists_seeded_config_with_masked_secrets(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    body = slack_bot_body()
    seeded = seed_slack_bot(**body)

    resp = config_client.get("/slack-bot")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    payload = resp.json()
    assert payload["status"] == "success"
    listed = [c for c in payload["configs"] if c["id"] == seeded["id"]]
    assert len(listed) == 1, f"seeded config not listed exactly once: {payload['configs']}"
    config = listed[0]
    assert set(config) == {
        "id",
        "name",
        "agentId",
        "createdAt",
        "updatedAt",
        "botToken",
        "signingSecret",
    }
    assert config["name"] == body["name"]
    # No agent linked: the controller sends an explicit null, not an absent key.
    assert config["agentId"] is None
    assert config["botToken"] == SECRET_PLACEHOLDER
    assert config["signingSecret"] == SECRET_PLACEHOLDER
    assert body["botToken"] not in resp.text
    assert body["signingSecret"] not in resp.text


def test_deleted_config_is_no_longer_listed(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    seeded = seed_slack_bot()
    deleted = config_client.delete(f"/slack-bot/{seeded['id']}")
    assert deleted.status_code == 200, deleted.text[:500]

    resp = config_client.get("/slack-bot")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert seeded["id"] not in [c["id"] for c in resp.json()["configs"]]


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get("/slack-bot", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/slack-bot")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
