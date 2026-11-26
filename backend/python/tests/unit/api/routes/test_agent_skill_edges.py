"""Which skills an agent can be linked to: the caller's own, and builtins the seeder wrote."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.routes.agent import _create_skill_edges

_LOG = logging.getLogger("test_agent_skill_edges")


def _doc(name: str, *, source: str = "manual", created_by: str = "user-a-key") -> dict:
    return {"orgId": "org-1", "name": name, "source": source, "createdBy": created_by, "status": "active"}


async def _linked(docs: dict[str, dict], user_key: str, names: list[str]) -> list[str]:
    provider = MagicMock()
    provider.get_document = AsyncMock(side_effect=lambda key, _collection, transaction=None: docs.get(key))
    provider.batch_create_edges = AsyncMock(return_value=True)
    return await _create_skill_edges("agent-1", names, "org-1", user_key, provider, _LOG)


async def test_the_caller_links_their_own_skill() -> None:
    docs = {"org-1_mine": _doc("mine")}
    assert await _linked(docs, "user-a-key", ["mine"]) == ["mine"]


async def test_a_co_workers_skill_is_not_linked() -> None:
    docs = {"org-1_theirs": _doc("theirs", created_by="user-b-key")}
    assert await _linked(docs, "user-a-key", ["theirs"]) == []


async def test_a_builtin_the_seeder_wrote_is_linked_for_everyone() -> None:
    docs = {"org-1_pack": _doc("pack", source="builtin", created_by=SEED_IDENTITY)}
    assert await _linked(docs, "user-a-key", ["pack"]) == ["pack"]


async def test_a_skill_that_only_says_builtin_is_not_linked_for_a_co_worker() -> None:
    docs = {"org-1_claimed": _doc("claimed", source="builtin", created_by="user-b-key")}
    assert await _linked(docs, "user-a-key", ["claimed"]) == []
    assert await _linked(docs, "user-b-key", ["claimed"]) == ["claimed"]
