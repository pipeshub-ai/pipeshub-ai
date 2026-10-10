"""Strict OpenAPI audit of POST /api/v1/configurationManager/slack-bot.

Chain: authenticate -> userAdminCheck -> zod body -> createSlackBotConfig. The route asks for
no OAuth scope. Every config this file creates is deleted again.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterator

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
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

ROUTE = "/api/v1/configurationManager/slack-bot"
PATH = "/slack-bot"
DUPLICATE_AGENT_MESSAGE = "Selected agent is already linked to another Slack Bot configuration"


@pytest.fixture
def created_ids(config_client: ConfigClient) -> Iterator[list[str]]:
    """Ids of configs the test created through the route under test; deleted afterwards."""
    ids: list[str] = []
    yield ids
    for config_id in ids:
        resp = config_client.delete(f"{PATH}/{config_id}")
        assert resp.status_code in (200, 404), resp.text[:300]


def _listed(config_client: ConfigClient) -> dict[str, dict[str, Any]]:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    return {config["id"]: config for config in resp.json()["configs"]}


def test_create_slack_bot_returns_masked_config(
    config_client: ConfigClient, created_ids: list[str]
) -> None:
    body = slack_bot_body()

    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 200, resp.text[:500]
    created_ids.append(resp.json()["config"]["id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    payload = resp.json()
    assert payload["status"] == "success"
    config = payload["config"]
    assert set(config) == {"id", "name", "agentId", "createdAt", "updatedAt", "botToken", "signingSecret"}
    assert config["name"] == body["name"]
    assert config["agentId"] is None
    assert config["createdAt"] == config["updatedAt"]
    assert config["botToken"] == SECRET_PLACEHOLDER
    assert config["signingSecret"] == SECRET_PLACEHOLDER
    assert body["botToken"] not in resp.text
    assert _listed(config_client)[config["id"]] == config


def test_create_slack_bot_links_the_agent_id_as_given(
    config_client: ConfigClient, created_ids: list[str]
) -> None:
    # The agent id is never looked up: a made-up one is stored.
    agent_id = f"spec-audit-agent-{uuid.uuid4().hex}"

    resp = config_client.post(PATH, json=slack_bot_body(agentId=f"  {agent_id} "))

    assert resp.status_code == 200, resp.text[:500]
    created_ids.append(resp.json()["config"]["id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["config"]["agentId"] == agent_id


@pytest.mark.parametrize("agent_id", ["", "   "], ids=["empty", "whitespace"])
def test_create_slack_bot_with_a_blank_agent_id_links_no_agent(
    config_client: ConfigClient, created_ids: list[str], agent_id: str
) -> None:
    resp = config_client.post(PATH, json=slack_bot_body(agentId=agent_id))

    assert resp.status_code == 200, resp.text[:500]
    created_ids.append(resp.json()["config"]["id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["config"]["agentId"] is None


def test_create_slack_bot_trims_the_name(config_client: ConfigClient, created_ids: list[str]) -> None:
    name = f"spec-audit {uuid.uuid4().hex[:8]}"

    resp = config_client.post(PATH, json=slack_bot_body(name=f" \t{name}  "))

    assert resp.status_code == 200, resp.text[:500]
    created_ids.append(resp.json()["config"]["id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["config"]["name"] == name


def test_create_slack_bot_drops_fields_the_validator_does_not_know(
    config_client: ConfigClient, created_ids: list[str]
) -> None:
    with outside_request_contract("id and specAudit are not fields of the body"):
        resp = config_client.post(
            PATH, json={**slack_bot_body(), "id": "spec-audit-chosen-id", "specAudit": "x"}
        )

    assert resp.status_code == 200, resp.text[:500]
    created_ids.append(resp.json()["config"]["id"])
    assert_strict_openapi_response(resp, ROUTE)
    config = resp.json()["config"]
    assert config["id"] != "spec-audit-chosen-id"
    assert "specAudit" not in config


def test_create_slack_bot_rejects_agent_already_linked(
    config_client: ConfigClient, seed_slack_bot: SeedSlackBot
) -> None:
    agent_id = f"spec-audit-agent-{uuid.uuid4().hex}"
    seed_slack_bot(agentId=agent_id)
    before = _listed(config_client)

    resp = config_client.post(PATH, json=slack_bot_body(agentId=agent_id))

    if resp.status_code == 200:
        config_client.delete(f"{PATH}/{resp.json()['config']['id']}")
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", DUPLICATE_AGENT_MESSAGE)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _listed(config_client).keys() == before.keys()


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.name", "body.botToken", "body.signingSecret"], id="empty-object"),
        pytest.param(slack_bot_body(botToken=None), ["body.botToken"], id="bot-token-null"),
        pytest.param({k: v for k, v in slack_bot_body().items() if k != "botToken"}, ["body.botToken"], id="missing-bot-token"),
        pytest.param({k: v for k, v in slack_bot_body().items() if k != "signingSecret"}, ["body.signingSecret"], id="missing-signing-secret"),
        pytest.param({k: v for k, v in slack_bot_body().items() if k != "name"}, ["body.name"], id="missing-name"),
        pytest.param(slack_bot_body(name=""), ["body.name"], id="empty-name"),
        pytest.param(slack_bot_body(name="   "), ["body.name"], id="whitespace-name"),
        pytest.param(slack_bot_body(name=42), ["body.name"], id="name-not-a-string"),
        pytest.param(slack_bot_body(agentId=42), ["body.agentId"], id="agent-id-not-a-string"),
        # Optional means "may be left out": an explicit null is refused.
        pytest.param(slack_bot_body(agentId=None), ["body.agentId"], id="agent-id-null"),
        pytest.param([slack_bot_body()], ["body"], id="body-is-a-list"),
    ],
)
def test_create_slack_bot_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = _listed(config_client)

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _listed(config_client).keys() == before.keys()


def test_create_slack_bot_without_a_body_is_rejected_like_an_empty_one(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.name", "body.botToken", "body.signingSecret")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_create_slack_bot_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=slack_bot_body())
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_slack_bot_member_is_forbidden(config_client: ConfigClient, second_user: SecondUser) -> None:
    before = _listed(config_client)

    # userAdminCheck runs before zod, so even this valid body never reaches the store.
    resp = request_as(second_user, "POST", PATH, json=slack_bot_body())

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _listed(config_client).keys() == before.keys()
