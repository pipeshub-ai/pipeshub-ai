"""Strict OpenAPI audit of DELETE /api/v1/configurationManager/slack-bot/:configId."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MISSING_SLACK_BOT_CONFIG_ID,
    SeedSlackBot,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/slack-bot/:configId"


def _stored_config_ids(config_client: ConfigClient) -> set[str]:
    resp = config_client.get("/slack-bot")
    assert resp.status_code == 200, resp.text[:500]
    return {config["id"] for config in resp.json()["configs"]}


def test_admin_deletes_config_then_it_is_gone(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    config_id = seed_slack_bot()["id"]
    assert config_id in _stored_config_ids(config_client)

    resp = config_client.delete(f"/slack-bot/{config_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Slack Bot configuration deleted",
    }
    assert config_id not in _stored_config_ids(config_client)

    again = config_client.delete(f"/slack-bot/{config_id}")
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_unknown_config_id_is_not_found(config_client: ConfigClient) -> None:
    resp = config_client.delete(f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.delete(
        f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}", auth=False, headers=headers
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden_and_config_survives(
    config_client: ConfigClient,
    second_user: SecondUser,
    seed_slack_bot: SeedSlackBot,
) -> None:
    config_id = seed_slack_bot()["id"]

    resp = request_as(second_user, "DELETE", f"/slack-bot/{config_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert config_id in _stored_config_ids(config_client)
