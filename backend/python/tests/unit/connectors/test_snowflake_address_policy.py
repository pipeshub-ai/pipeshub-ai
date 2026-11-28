"""Every Snowflake client carries the connector's address rule.

The host is always under snowflakecomputing.com, but a private-link account name can resolve to a
private address, so a personal connector reaches public addresses only; a team connector keeps the
deployment rule (PIPESHUB_BLOCK_PRIVATE_ADDRESSES).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.sources.external.snowflake.warehouse as warehouse
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.sources.client.snowflake.snowflake import SnowflakeClient
from app.utils.execute_query import _execute_query_impl

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_AUTH = {"accountIdentifier": "acme.privatelink", "warehouse": "WH", "patToken": "pat"}


def _public_only(http: object) -> bool:
    """What the client's transport is built with on its first request (see HTTPClient._ensure_client)."""
    return getattr(http, "_public_only", False)


class TestTheConnector:
    @staticmethod
    async def _init(scope: str) -> SnowflakeConnector:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"auth": _AUTH, "scope": scope})
        connector = SnowflakeConnector(MagicMock(), MagicMock(), MagicMock(), config_service, "c1", scope=scope)
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            assert await connector.init() is True
        return connector

    @pytest.mark.parametrize(
        ("scope", "public_only"), [(ConnectorScope.PERSONAL.value, True), (ConnectorScope.TEAM.value, False)],
    )
    async def test_its_sync_client_carries_its_rule(self, scope, public_only) -> None:
        connector = await self._init(scope)
        assert _public_only(connector.data_source.http) is public_only


class TestTheRunner:
    """The runner relation discovery, column statistics and the SQL tool open on a saved connector."""

    @staticmethod
    @asynccontextmanager
    async def _saved_connector() -> AsyncIterator[None]:
        with patch.object(SnowflakeClient, "_get_connector_config", AsyncMock(return_value={"auth": _AUTH})):
            yield

    @pytest.mark.parametrize("public_only", [True, False])
    async def test_it_carries_the_rule_it_is_given(self, public_only) -> None:
        async with self._saved_connector(), warehouse.connector_runner(
            logger=MagicMock(), config_service=MagicMock(), connector_id="c1", query_tag="t",
            public_only=public_only,
        ) as runner:
            assert _public_only(runner.data_source.http) is public_only

    async def test_without_a_rule_it_is_the_stricter_one(self) -> None:
        async with self._saved_connector(), warehouse.connector_runner(
            logger=MagicMock(), config_service=MagicMock(), connector_id="c1", query_tag="t",
        ) as runner:
            assert _public_only(runner.data_source.http) is True


class TestTheSqlTool:
    @pytest.mark.parametrize("public_only", [True, False])
    async def test_it_opens_the_runner_under_the_connectors_rule(self, monkeypatch, public_only) -> None:
        opened: list[dict[str, Any]] = []

        @asynccontextmanager
        async def runner(**kwargs: Any) -> AsyncIterator[MagicMock]:  # noqa: ANN401
            opened.append(kwargs)
            fake = MagicMock()
            fake.run = AsyncMock(return_value=MagicMock(column_names=["n"], rows=[(1,)]))
            yield fake

        monkeypatch.setattr(warehouse, "connector_runner", runner)
        result = await _execute_query_impl(
            "SELECT 1", "snowflake", MagicMock(), "c1", public_only=public_only,
        )
        assert result["ok"] is True
        assert [kwargs["public_only"] for kwargs in opened] == [public_only]
