"""The instance listing turns the action items stored on the app doc back into a list."""

import json

import pytest

from app.connectors.core.registry.connector_registry import _parsed_action_items


def test_stored_items_are_returned_as_written() -> None:
    items = [{"code": "certificate_sign_in", "severity": "warning", "title": "Switch", "message": "m", "action": "authenticate"}]

    assert _parsed_action_items(json.dumps(items)) == items


@pytest.mark.parametrize("raw", [None, "", "not json", json.dumps({"code": "x"}), json.dumps(["text", 3])])
def test_nothing_or_anything_unreadable_is_no_items(raw: object) -> None:
    """The card shows no badge rather than failing the whole listing."""
    assert _parsed_action_items(raw) == []
