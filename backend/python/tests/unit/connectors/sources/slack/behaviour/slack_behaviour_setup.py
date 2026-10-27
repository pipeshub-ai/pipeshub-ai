"""Builds real Slack connectors wired to the behaviour-test fakes."""

import logging
from typing import Any, Optional

from slack_behaviour_fakes import (
    BOT_TOKEN,
    USER_TOKEN,
    FakeCheckpoints,
    FakeConfigService,
    FakeSlackStore,
    SlackWorkspace,
)

from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector

WORKSPACE_CONNECTOR_ID = "slack-workspace-1"
PERSONAL_CONNECTOR_ID = "slack-personal-1"


def workspace_config(token: str = BOT_TOKEN, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"auth": {"authType": "API_TOKEN", "apiToken": token}, **({"filters": filters} if filters else {})}


def personal_config(token: str = USER_TOKEN, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "auth": {"authType": "OAUTH"},
        "credentials": {"access_token": token},
        **({"filters": filters} if filters else {}),
    }


async def workspace_connector(
    store: FakeSlackStore, checkpoints: FakeCheckpoints, config: Optional[dict[str, Any]] = None,
) -> tuple[SlackConnector, FakeConfigService]:
    config_service = FakeConfigService(WORKSPACE_CONNECTOR_ID, config or workspace_config())
    connector = SlackConnector(
        logging.getLogger("test.slack_workspace"), store, checkpoints, config_service,
        WORKSPACE_CONNECTOR_ID, "team", "creator-1",
    )
    assert await connector.init() is True
    return connector, config_service


async def personal_connector(
    store: FakeSlackStore, checkpoints: FakeCheckpoints, config: Optional[dict[str, Any]] = None,
) -> tuple[SlackIndividualConnector, FakeConfigService]:
    config_service = FakeConfigService(PERSONAL_CONNECTOR_ID, config or personal_config())
    connector = SlackIndividualConnector(
        logging.getLogger("test.slack_personal"), store, checkpoints, config_service,
        PERSONAL_CONNECTOR_ID, "personal", "creator-1",
    )
    assert await connector.init() is True
    return connector, config_service


ALICE, BOB, CAROL, NOEMAIL, BOT = "U0ALICE", "U0BOB", "U0CAROL", "U0NOMAIL", "B0BOT"
GENERAL, SECRET, PARTNERS = "C0GENERAL", "G0SECRET", "G0PARTNER"


def standard_workspace(slack: SlackWorkspace) -> SlackWorkspace:
    """Alice and Bob are members, Carol is a guest; one public and two private channels."""
    slack.add_user(ALICE, "alice@acme.com", "Alice")
    slack.add_user(BOB, "bob@acme.com", "Bob")
    slack.add_user(CAROL, "carol@partner.com", "Carol", guest=True)
    slack.add_user(NOEMAIL, None, "Nomail")
    slack.add_user(BOT, None, "Deploybot", bot=True)
    slack.add_channel(GENERAL, "general", members=[ALICE, BOB, CAROL])
    slack.add_channel(SECRET, "secret", kind="private", members=[ALICE, BOB])
    slack.add_channel(PARTNERS, "partners", kind="private", members=[ALICE, CAROL, NOEMAIL])
    return slack


def boolean_filter(value: bool) -> dict[str, Any]:
    return {"operator": "is", "value": value, "type": "boolean"}
