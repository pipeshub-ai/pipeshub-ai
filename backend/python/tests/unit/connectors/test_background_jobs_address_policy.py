"""The connector address rule, read for work outside a connector, and an agent's MariaDB tool.

A personal connector, or a personal toolset, may reach public addresses only; a team connector,
or an organisation's toolset, keeps the deployment rule (PIPESHUB_BLOCK_PRIVATE_ADDRESSES).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.sources.client.mariadb.mariadb as mariadb_client
from app.connectors.core.address_policy import connector_public_only
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV, public_address_refusal

_LOGGER = logging.getLogger("test.background.address.policy")
_PRIVATE = "10.0.0.7"


@pytest.fixture(autouse=True)
def _deployment_allows_private(monkeypatch) -> None:
    monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)


@pytest.fixture
def driver(monkeypatch) -> MagicMock:
    """The binary driver: its connect is what dials; it refuses, as a server that is not there does."""
    fake = MagicMock()
    fake.connect = MagicMock(side_effect=ConnectionRefusedError())
    fake.create_async_pool = AsyncMock(side_effect=ConnectionRefusedError())
    monkeypatch.setattr(mariadb_client, "mariadb", fake)
    return fake


def _graph(scope: str | None) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=None if scope is None else {"_key": "c1", "scope": scope})
    return graph


def _dialled(driver: MagicMock) -> bool:
    return driver.connect.called or driver.create_async_pool.called


class TestTheRule:
    @pytest.mark.parametrize(("scope", "public_only"), [("team", False), ("personal", True), (None, True)])
    async def test_it_is_read_from_the_connectors_app_record(self, scope, public_only) -> None:
        assert await connector_public_only(_graph(scope), "c1") is public_only

    async def test_without_a_graph_to_ask_it_is_the_stricter_one(self) -> None:
        assert await connector_public_only(None, "c1") is True


class TestMariaDBToolset:
    @staticmethod
    async def _client(instance: dict) -> mariadb_client.MariaDBClient:
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=instance)):
            return await mariadb_client.MariaDBClient.build_from_toolset(
                toolset_config={"instanceId": "t1", "username": "reader", "password": "pw"},
                logger=_LOGGER, config_service=MagicMock(),
            )

    async def test_a_personal_toolset_is_refused_before_anything_is_dialled(self, driver) -> None:
        client = await self._client({"_id": "t1", "scope": "personal", "host": _PRIVATE, "port": 3306})
        assert client.public_only is True
        with pytest.raises(ConnectionError) as refused:
            await client.connect()
        assert str(refused.value) == public_address_refusal(_PRIVATE)
        assert not _dialled(driver)

    async def test_an_organisations_toolset_dials_its_private_address(self, driver) -> None:
        client = await self._client({"_id": "t1", "host": _PRIVATE, "port": 3306})
        assert client.public_only is False
        with pytest.raises(ConnectionError):
            await client.connect()
        assert _dialled(driver)
