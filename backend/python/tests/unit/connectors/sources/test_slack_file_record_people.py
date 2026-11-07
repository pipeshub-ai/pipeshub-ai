"""Slack file records name their uploader as author (team and individual connectors)."""

import importlib
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.models.entities import SourcePerson

CONNECTORS = [
    ("app.connectors.sources.slack.team.connector", "SlackConnector"),
    ("app.connectors.sources.slack.individual.connector", "SlackIndividualConnector"),
]

# A file as conversations.history returns it inside a message (no download URL,
# so the test makes no HTTP call).
FILE = {
    "id": "F0A1B2C3D4E",
    "created": 1767225600,
    "name": "budget.xlsx",
    "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "filetype": "xlsx",
    "user": "U0ANN",
    "user_team": "T12345",
    "size": 4096,
    "permalink": "https://acme.slack.com/files/U0ANN/F0A1B2C3D4E/budget.xlsx",
}


def _connector(module: ModuleType, class_name: str) -> object:
    cls = getattr(module, class_name)
    c = cls.__new__(cls)
    c.logger = MagicMock()
    c.data_entities_processor = MagicMock(org_id="org-slack")
    c.connector_id = "slack-1"
    return c


def _ctx(module: ModuleType) -> object:
    return module.ProcessingContext(
        channel_id="C1",
        channel_groups_map={"C1": "rg-1"},
        user_id_to_email={"U0ANN": "ann@acme.com"},
        user_id_to_name={"U0ANN": "Ann Author"},
        channel_id_to_name={},
        rate_limiter=MagicMock(),
    )


@pytest.fixture(params=CONNECTORS, ids=["team", "individual"])
def slack(request: pytest.FixtureRequest) -> tuple[Any, Any]:
    module = importlib.import_module(request.param[0])
    return _connector(module, request.param[1]), _ctx(module)


async def test_uploader_is_the_file_author(slack: tuple[Any, Any]) -> None:
    connector, ctx = slack

    record = await connector._process_file_raw(FILE, ctx)

    assert record.authored_by == SourcePerson(source_id="U0ANN", email="ann@acme.com", display_name="Ann Author")
    assert record.created_by is None


async def test_uploader_not_in_cache_is_named_by_id(slack: tuple[Any, Any]) -> None:
    connector, ctx = slack

    record = await connector._process_file_raw({**FILE, "user": "U0NEW"}, ctx)

    assert record.authored_by == SourcePerson(source_id="U0NEW")


@pytest.mark.parametrize("fields", [{"user": "USLACKBOT"}, {"user": "B0BOT"}, {"user": "U0APP", "bot_id": "B0APP"}])
async def test_bot_uploads_are_service_accounts(slack: tuple[Any, Any], fields: dict) -> None:
    connector, ctx = slack

    record = await connector._process_file_raw({**FILE, **fields}, ctx)

    assert record.authored_by is not None and record.authored_by.is_service_account


async def test_file_without_uploader_names_no_one(slack: tuple[Any, Any]) -> None:
    connector, ctx = slack

    record = await connector._process_file_raw({k: v for k, v in FILE.items() if k != "user"}, ctx)

    assert record.authored_by is None
