"""The knowledge hub scope keeper: each org's connectors, read and stamped inside that org."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.containers import connector
from app.core import org_context


@pytest.mark.asyncio
async def test_the_scope_keeper_works_org_by_org_and_one_failure_does_not_stop_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound: list[tuple[str, str]] = []

    async def restamp(org_id: str) -> list:
        bound.append((org_id, org_context.current_org_id()))
        if org_id == "org-1":
            raise RuntimeError("graph down")
        return []

    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}, {"_key": "org-2"}])
    graph.kh_scope_restamp_stale = AsyncMock(side_effect=restamp)
    monkeypatch.setattr(connector, "_kh_graph_migrations", AsyncMock(return_value=True))
    monkeypatch.setattr(connector.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))

    with pytest.raises(asyncio.CancelledError):
        await connector._kh_listing_background(graph, MagicMock(), logging.getLogger("t"), kb_apps_done=True)

    assert bound == [("org-1", "org-1"), ("org-2", "org-2")]
    assert org_context.peek() is None
