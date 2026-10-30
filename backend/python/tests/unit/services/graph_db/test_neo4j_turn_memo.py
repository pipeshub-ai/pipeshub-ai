"""Neo4j lookups memoized for one chat turn (`app.utils.turn_memo`)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils import turn_memo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import AccessibleContainers


def _provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


async def _in_turn[T](coro_fn: Callable[[], Awaitable[T]]) -> T:
    async def run() -> T:
        turn_memo.begin_turn()
        return await coro_fn()

    return await asyncio.create_task(run())


class TestUserByUserId:
    @pytest.mark.asyncio
    async def test_one_query_per_turn_and_each_caller_gets_a_copy(self) -> None:
        provider = _provider()
        provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "u1", "userId": "uid1"}}])

        async def turn() -> tuple[dict | None, dict | None]:
            first = await provider.get_user_by_user_id("uid1")
            first["email"] = "edited"
            second = await provider.get_user_by_user_id("uid1")
            return first, second

        first, second = await _in_turn(turn)
        assert provider.client.execute_query.await_count == 1
        assert "email" not in second
        assert second["_key"] == "u1"

    @pytest.mark.asyncio
    async def test_nothing_survives_into_the_next_turn(self) -> None:
        provider = _provider()
        provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "u1", "userId": "uid1"}}])
        await _in_turn(lambda: provider.get_user_by_user_id("uid1"))
        await _in_turn(lambda: provider.get_user_by_user_id("uid1"))
        assert provider.client.execute_query.await_count == 2

    @pytest.mark.asyncio
    async def test_a_failure_is_not_cached_and_honours_raise_on_error(self) -> None:
        provider = _provider()
        provider.client.execute_query = AsyncMock(
            side_effect=[RuntimeError("graph down"), RuntimeError("graph down"),
                         [{"u": {"id": "u1", "userId": "uid1"}}]],
        )

        async def turn() -> dict | None:
            assert await provider.get_user_by_user_id("uid1") is None
            with pytest.raises(RuntimeError):
                await provider.get_user_by_user_id("uid1", raise_on_error=True)
            return await provider.get_user_by_user_id("uid1")

        assert (await _in_turn(turn))["_key"] == "u1"
        assert provider.client.execute_query.await_count == 3

    @pytest.mark.asyncio
    async def test_other_users_are_asked_separately(self) -> None:
        provider = _provider()
        provider.client.execute_query = AsyncMock(return_value=[])

        async def turn() -> None:
            await provider.get_user_by_user_id("uid1")
            await provider.get_user_by_user_id("uid2")

        await _in_turn(turn)
        assert provider.client.execute_query.await_count == 2


class TestAccessibleContainers:
    def _provider(self) -> Neo4jProvider:
        provider = _provider()
        provider.client.execute_query = AsyncMock(return_value=[{"id": "a", "ready": True}])
        provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
        provider.get_knowledge_hub_access_context_v2 = AsyncMock(
            return_value={"gated_app_ids": ["a"], "grantee_ids": [], "by_connector": {}},
        )
        return provider

    @pytest.mark.asyncio
    async def test_one_lookup_per_turn_for_the_same_arguments(self) -> None:
        provider = self._provider()

        async def turn() -> list[AccessibleContainers]:
            return await asyncio.gather(
                provider.get_accessible_containers(user_id="uid1", org_id="o1"),
                provider.get_accessible_containers(user_id="uid1", org_id="o1"),
            )

        first, second = await _in_turn(turn)
        assert first == second and first.app_ids == frozenset({"a"})
        assert provider.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_different_filters_and_turns_are_asked_again(self) -> None:
        provider = self._provider()

        async def turn() -> None:
            await provider.get_accessible_containers(user_id="uid1", org_id="o1")
            await provider.get_accessible_containers(user_id="uid1", org_id="o1", filters={"apps": ["a"]})

        await _in_turn(turn)
        await _in_turn(lambda: provider.get_accessible_containers(user_id="uid1", org_id="o1"))
        assert provider.client.execute_query.await_count == 3

    @pytest.mark.asyncio
    async def test_a_failed_query_is_asked_again_in_the_same_turn(self) -> None:
        """query_failed is returned, not raised; keeping it would put the whole
        turn on the slow unscoped path after one transient error."""
        provider = self._provider()
        provider.client.execute_query = AsyncMock(side_effect=[RuntimeError("graph down"), [{"id": "a", "ready": True}]])

        async def turn() -> tuple[AccessibleContainers, AccessibleContainers]:
            first = await provider.get_accessible_containers(user_id="uid1", org_id="o1")
            second = await provider.get_accessible_containers(user_id="uid1", org_id="o1")
            return first, second

        first, second = await _in_turn(turn)
        assert first.fallback_reason == "query_failed"
        assert second.app_ids == frozenset({"a"})
        assert provider.client.execute_query.await_count == 2
