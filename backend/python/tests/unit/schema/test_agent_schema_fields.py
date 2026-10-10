import pytest
from jsonschema import Draft4Validator

from app.schema.arango.documents import agent_schema

VALIDATOR = Draft4Validator(agent_schema["rule"])

BASE = {
    "name": "Offer drafter", "description": "d", "startMessage": "hi", "systemPrompt": "sp",
    "models": [], "createdBy": "k-1", "createdAtTimestamp": 1,
}


def _errors(**extra: object) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors({**BASE, **extra})]


def test_schema_is_strict() -> None:
    assert agent_schema["level"] == "strict" and agent_schema["rule"]["additionalProperties"] is False


def test_legacy_agent_without_the_new_fields_still_validates() -> None:
    assert _errors() == []


def test_new_fields_validate() -> None:
    assert _errors(
        orgId="org-1", handle="offer-drafter", createdVia="chat",
        sourceConversationId="c-1", sourceMessageId="m-1",
    ) == []


@pytest.mark.parametrize("handle", ["Bad Handle", "a", "x" * 41, "UPPER", "a_b", ""])
def test_bad_handle_is_rejected(handle: str) -> None:
    assert _errors(handle=handle)


@pytest.mark.parametrize("handle", ["ab", "a-b", "x" * 40, "007"])
def test_good_handle_is_accepted(handle: str) -> None:
    assert _errors(handle=handle) == []


def test_created_via_is_an_enum() -> None:
    assert _errors(createdVia="api")
    assert _errors(createdVia="ui") == [] and _errors(createdVia="chat") == []


def test_the_neo4j_side_adapter_keeps_the_fields() -> None:
    from app.schema.node_schema_registry import adapt_schema

    adapted = adapt_schema(agent_schema)
    properties = adapted["rule"]["properties"] if "rule" in adapted else adapted["properties"]
    assert {"orgId", "handle", "createdVia", "sourceConversationId", "sourceMessageId"} <= set(properties)
