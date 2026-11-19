"""``accessible_node_ids`` answers for a record to read: a placeholder is left out unless the caller only names records."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.utils.chat_helpers import accessible_node_ids


def _provider() -> SimpleNamespace:
    """Provider."""
    return SimpleNamespace(
        get_user_by_user_id=AsyncMock(return_value={"id": "u-key"}),
        check_access=AsyncMock(
            return_value=SimpleNamespace(node_ids=frozenset({"real", "stub"}), placeholder_ids=frozenset({"stub"}))
        ),
    )


async def test_a_placeholder_is_not_accessible_by_default() -> None:
    """A placeholder is not accessible by default."""
    assert await accessible_node_ids(_provider(), {"real", "stub"}, "u", "org") == {"real"}


async def test_a_caller_that_only_names_records_keeps_the_placeholder() -> None:
    """A caller that only names records keeps the placeholder."""
    assert await accessible_node_ids(_provider(), {"real", "stub"}, "u", "org", with_placeholders=True) == {"real", "stub"}
