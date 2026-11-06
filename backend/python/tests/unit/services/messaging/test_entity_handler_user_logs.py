"""User events carry a person's email, names and phones; the handler may log only ids."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.kafka.handlers.entity import EntityEventService

LOGGER_NAME = "tests.entity_handler.user_logs"
PERSONAL = {
    "email": "alice@example.com",
    "fullName": "Alice Liddell",
    "firstName": "Alicia",
    "middleName": "Pleasance",
    "lastName": "Liddell",
    "designation": "Chief Curiosity Officer",
    "businessPhones": ["+1-555-0100"],
}
PAYLOAD = {"orgId": "org-1", "userId": "u-1", **PERSONAL}


def _service(graph_provider: AsyncMock) -> EntityEventService:
    svc = EntityEventService(
        logger=logging.getLogger(LOGGER_NAME),
        graph_provider=graph_provider,
        app_container=MagicMock(),
    )
    svc._get_or_create_knowledge_base = AsyncMock(return_value={})
    return svc


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]


def _assert_no_personal_data(messages: list[str]) -> None:
    values = [v for v in PERSONAL.values() if isinstance(v, str)] + PERSONAL["businessPhones"]
    for value in values:
        assert not any(value in m for m in messages), f"{value!r} was logged"


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)


def _user_added_provider(existing_user: object = None) -> AsyncMock:
    gp = AsyncMock()
    gp.get_user_by_email = AsyncMock(return_value=existing_user)
    gp.get_document = AsyncMock(return_value={"_key": "org-1", "accountType": "enterprise"})
    gp.get_nodes_by_filters = AsyncMock(return_value=[])
    return gp


async def test_user_added_new_user_logs_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    assert await _service(_user_added_provider()).process_event("userAdded", dict(PAYLOAD)) is True

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Processing user added event for user u-1 in org org-1" in messages
    assert "Created or updated user u-1 in org org-1" in messages


async def test_user_added_existing_user_logs_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    existing = MagicMock()
    existing.id = "graph-key-1"

    assert await _service(_user_added_provider(existing)).process_event("userAdded", dict(PAYLOAD)) is True

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Created or updated user u-1 in org org-1" in messages


async def test_user_added_adopting_a_person_logs_the_user_key(caplog: pytest.LogCaptureFixture) -> None:
    existing = MagicMock()
    existing.id = "graph-key-1"
    gp = _user_added_provider(existing)
    gp.migrate_person_to_user = AsyncMock(return_value="merged")

    assert await _service(gp).process_event("userAdded", dict(PAYLOAD)) is True

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Adopted existing person for user graph-key-1 (merged)" in messages


async def test_user_added_whose_adoption_fails_logs_the_user_key(caplog: pytest.LogCaptureFixture) -> None:
    """Adoption is best effort: the user is still created, and the error names the key."""
    existing = MagicMock()
    existing.id = "graph-key-1"
    gp = _user_added_provider(existing)
    gp.migrate_person_to_user = AsyncMock(side_effect=RuntimeError("graph down"))

    assert await _service(gp).process_event("userAdded", dict(PAYLOAD)) is True

    _assert_no_personal_data(_messages(caplog))
    errors = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.ERROR]
    assert errors == ["Failed to adopt existing person for user graph-key-1: graph down"]


async def test_user_added_that_fails_still_logs_no_personal_data(caplog: pytest.LogCaptureFixture) -> None:
    gp = _user_added_provider()
    gp.get_document = AsyncMock(return_value=None)

    assert await _service(gp).process_event("userAdded", dict(PAYLOAD)) is False

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Organization not found: org-1" in messages


async def test_user_updated_logs_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"id": "graph-key-1", "isActive": True})

    assert await _service(gp).process_event("userUpdated", dict(PAYLOAD)) is True

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Processing user updated event for user u-1 in org org-1" in messages
    assert "Updated user u-1" in messages


async def test_user_deleted_logs_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    gp = AsyncMock()
    gp.get_entity_id_by_email = AsyncMock(return_value="graph-key-1")
    svc = _service(gp)
    svc._remove_mcp_data = AsyncMock()
    svc._remove_personal_toolsets = AsyncMock()
    svc._remove_api_toolset_data = AsyncMock()

    assert await svc.process_event("userDeleted", dict(PAYLOAD)) is True

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Processing user deleted event for user u-1 in org org-1" in messages
    assert "Soft-deleted graph user graph-key-1" in messages


async def test_user_deleted_without_user_id_still_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    """`userId` is optional on this event; logging it must not turn into a KeyError."""
    gp = AsyncMock()
    gp.get_entity_id_by_email = AsyncMock(return_value="graph-key-1")
    payload = {"orgId": "org-1", "email": PERSONAL["email"]}

    assert await _service(gp).process_event("userDeleted", payload) is True

    messages = _messages(caplog)
    _assert_no_personal_data(messages)
    assert "Soft-deleted graph user graph-key-1" in messages


async def test_user_deleted_with_no_graph_user_logs_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    gp = AsyncMock()
    gp.get_entity_id_by_email = AsyncMock(return_value=None)

    assert await _service(gp).process_event("userDeleted", dict(PAYLOAD)) is False

    errors = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.ERROR]
    _assert_no_personal_data(_messages(caplog))
    assert errors == ["No graph user has the deleted user's email (user u-1, org org-1)"]
