"""A placeholder table at the end of a foreign key is named in the relation list, and contributes no content."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.chat_helpers import enrich_virtual_record_id_to_result_with_fk_children


def _graph() -> AsyncMock:
    """Graph."""
    graph = AsyncMock()
    graph.get_child_record_ids_by_relation_type = AsyncMock(
        side_effect=lambda rid, _rel: [{"record_id": "t-real"}, {"record_id": "t-stub"}] if rid == "t-hit" else [],
    )
    graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
    graph.get_virtual_record_ids_for_record_ids = AsyncMock(return_value={})
    return graph


@pytest.mark.asyncio
async def test_a_placeholder_table_is_named_but_its_content_is_never_resolved() -> None:
    """A placeholder table is named but its content is never resolved."""
    records = {"v": {"record_type": "SQL_TABLE", "id": "t-hit"}}
    flattened = [{"virtual_record_id": "v"}]
    graph = _graph()
    seen: list[set[str]] = []

    async def readable(_graph: object, ids: set[str], _user: str, _org: str, **_kw: object) -> set[str]:
        """Readable."""
        seen.append(set(ids))
        return set(ids) - {"t-stub"}  # a placeholder is left out unless the caller only names records

    with (
        patch("app.utils.chat_helpers.accessible_node_ids_for_enrichment",
              AsyncMock(side_effect=lambda _g, ids, _u, _o, **_: set(ids))),
        patch("app.utils.chat_helpers.accessible_node_ids", AsyncMock(side_effect=readable)),
        patch("app.utils.chat_helpers.live_record_ids", AsyncMock(side_effect=lambda _g, ids, _o: set(ids))),
    ):
        await enrich_virtual_record_id_to_result_with_fk_children(
            records, MagicMock(), "o", graph, flattened, user_id="u",
        )

    assert [rel["record_id"] for rel in flattened[0]["fk_child_relations"]] == ["t-real", "t-stub"]  # named
    graph.get_virtual_record_ids_for_record_ids.assert_awaited_once_with(["t-real"])             # not resolved
    assert seen and "t-stub" in seen[0]
