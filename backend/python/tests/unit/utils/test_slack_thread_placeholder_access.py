"""The Slack thread tool opens a record live from Slack, so a placeholder is refused like an unreadable record."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.fetch_slack_thread import user_can_access_node


def _graph(node: dict | None) -> MagicMock:
    """Graph."""
    graph = MagicMock()
    graph.get_knowledge_hub_node_access = AsyncMock(return_value=node)
    return graph


@pytest.mark.parametrize(
    ("node", "allowed"),
    [
        ({"id": "r", "nodeType": "record", "isPlaceholder": False}, True),
        ({"id": "r", "nodeType": "record"}, True),
        ({"id": "r", "nodeType": "record", "isPlaceholder": True}, False),
        (None, False),
    ],
    ids=["real record", "node without the flag", "placeholder", "not accessible"],
)
async def test_a_placeholder_is_not_a_node_to_open(node: dict | None, allowed: bool) -> None:
    """A placeholder is not a node to open."""
    assert await user_can_access_node(_graph(node), "r", "user-key", "org") is allowed
