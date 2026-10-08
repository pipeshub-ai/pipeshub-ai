"""Strict OpenAPI audit of PUT /api/v1/configurationManager/slack-bot/:configId.

Chain: authenticate -> userAdminCheck -> zod params and body -> updateSlackBotConfig. The body
is the same as for POST: a full replace of name, credentials and agent link.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MISSING_SLACK_BOT_CONFIG_ID,
    SECRET_PLACEHOLDER,
    SeedSlackBot,
    assert_validation_error,
    request_as,
    slack_bot_body,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/slack-bot/:configId"
DUPLICATE_AGENT_MESSAGE = "Selected agent is already linked to another Slack Bot configuration"


def _stored(config_client: ConfigClient, config_id: str) -> dict[str, Any]:
    resp = config_client.get("/slack-bot")
    assert resp.status_code == 200, resp.text[:500]
    [config] = [c for c in resp.json()["configs"] if c["id"] == config_id]
    return config


def test_update_renames_config_and_keeps_secrets_masked(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    seeded = seed_slack_bot()
    new_name = f"spec-audit renamed {uuid.uuid4().hex[:8]}"

    # The edit form never saw the real secrets, so it re-submits the placeholder.
    resp = config_client.put(
        f"/slack-bot/{seeded['id']}",
        json={"name": new_name, "botToken": SECRET_PLACEHOLDER, "signingSecret": SECRET_PLACEHOLDER},
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    config = body["config"]
    assert config["id"] == seeded["id"]
    assert config["name"] == new_name
    assert config["agentId"] is None
    assert config["createdAt"] == seeded["createdAt"]
    assert config["updatedAt"] >= seeded["updatedAt"]
    assert config["botToken"] == SECRET_PLACEHOLDER
    assert config["signingSecret"] == SECRET_PLACEHOLDER
    assert _stored(config_client, seeded["id"]) == config


def test_update_links_and_unlinks_an_agent(config_client: ConfigClient, seed_slack_bot: SeedSlackBot) -> None:
    seeded = seed_slack_bot()
    agent_id = f"spec-audit-agent-{uuid.uuid4().hex}"

    linked = config_client.put(f"/slack-bot/{seeded['id']}", json=slack_bot_body(agentId=agent_id))
    assert linked.status_code == 200, linked.text[:500]
    assert_strict_openapi_exchange(linked, ROUTE)
    assert linked.json()["config"]["agentId"] == agent_id

    # Re-sending its own agent is not a conflict.
    again = config_client.put(f"/slack-bot/{seeded['id']}", json=slack_bot_body(agentId=agent_id))
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)

    # Leaving agentId out of a full replace removes the link.
    unlinked = config_client.put(f"/slack-bot/{seeded['id']}", json=slack_bot_body())
    assert unlinked.status_code == 200, unlinked.text[:500]
    assert_strict_openapi_exchange(unlinked, ROUTE)
    assert unlinked.json()["config"]["agentId"] is None


def test_update_drops_fields_the_validator_does_not_know(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    seeded = seed_slack_bot()

    with outside_request_contract("id and createdAt are not fields of the body"):
        resp = config_client.put(
            f"/slack-bot/{seeded['id']}",
            json={**slack_bot_body(), "id": "spec-audit-other-id", "createdAt": "2000-01-01T00:00:00.000Z"},
        )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    config = resp.json()["config"]
    assert (config["id"], config["createdAt"]) == (seeded["id"], seeded["createdAt"])


def test_update_unknown_config_is_not_found(config_client: ConfigClient) -> None:
    resp = config_client.put(f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}", json=slack_bot_body())
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Slack Bot configuration not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_body_is_validated_before_the_config_is_looked_up(config_client: ConfigClient) -> None:
    resp = config_client.put(f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}", json={})

    assert_validation_error(resp, "body.name", "body.botToken", "body.signingSecret")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({k: v for k, v in slack_bot_body().items() if k != "name"}, ["body.name"], id="missing-name"),
        pytest.param({"name": "spec-audit"}, ["body.botToken", "body.signingSecret"], id="name-only"),
        pytest.param(slack_bot_body(signingSecret=""), ["body.signingSecret"], id="empty-signing-secret"),
        pytest.param(slack_bot_body(botToken=["x"]), ["body.botToken"], id="bot-token-not-a-string"),
        pytest.param(slack_bot_body(agentId=None), ["body.agentId"], id="agent-id-null"),
    ],
)
def test_update_invalid_body_is_rejected_and_nothing_changes(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot, body: dict[str, Any], fields: list[str]
) -> None:
    seeded = seed_slack_bot()

    resp = config_client.put(f"/slack-bot/{seeded['id']}", json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client, seeded["id"]) == seeded


def test_update_to_agent_linked_elsewhere_is_rejected(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    agent_id = f"spec-audit-agent-{uuid.uuid4().hex}"
    seed_slack_bot(agentId=agent_id)
    other = seed_slack_bot()

    resp = config_client.put(f"/slack-bot/{other['id']}", json=slack_bot_body(agentId=agent_id))

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", DUPLICATE_AGENT_MESSAGE)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client, other["id"]) == other


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_update_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.put(
        f"/slack-bot/{MISSING_SLACK_BOT_CONFIG_ID}", auth=False, headers=headers, json=slack_bot_body()
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_as_member_is_forbidden_and_config_survives(
    config_client: ConfigClient, second_user: SecondUser, seed_slack_bot: SeedSlackBot
) -> None:
    seeded = seed_slack_bot()

    resp = request_as(second_user, "PUT", f"/slack-bot/{seeded['id']}", json=slack_bot_body())

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client, seeded["id"]) == seeded
