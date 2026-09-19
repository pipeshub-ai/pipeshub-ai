from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arcadedb.arcadedb_client import ArcadeDBClient


def _make_client() -> ArcadeDBClient:
    return ArcadeDBClient(
        uri="bolt://localhost:7687",
        username="root",
        password="playwithdata",
        database="pipeshub",
        logger=MagicMock(),
        http_uri="http://localhost:2480",
    )


class _FakeSession:
    def __init__(self, run_result: list[dict]) -> None:
        self._run_result = run_result

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def run(self, *args: object, **kwargs: object) -> AsyncMock:
        result = AsyncMock()
        result.data = AsyncMock(return_value=self._run_result)
        return result


class TestEnsureDatabaseExists:
    @pytest.mark.asyncio
    async def test_skips_creation_when_database_already_exists(self) -> None:
        client = _make_client()
        driver = MagicMock()
        driver.session = MagicMock(return_value=_FakeSession([{"name": "pipeshub"}]))
        client.driver = driver

        with patch.object(
            ArcadeDBClient, "_create_database_over_http", new=AsyncMock()
        ) as mock_create:
            await client._ensure_database_exists()

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_over_http_when_missing(self) -> None:
        client = _make_client()
        driver = MagicMock()
        driver.session = MagicMock(return_value=_FakeSession([]))
        client.driver = driver

        with patch.object(
            ArcadeDBClient, "_create_database_over_http", new=AsyncMock()
        ) as mock_create:
            await client._ensure_database_exists()

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_matches_target_database_by_name_client_side(self) -> None:
        """ArcadeDB's SHOW DATABASES accepts a WHERE clause but does not
        apply it — it always returns every database. _ensure_database_exists
        must filter the result by name itself rather than trust the
        driver-side query to have already done so."""
        client = _make_client()
        driver = MagicMock()
        driver.session = MagicMock(
            return_value=_FakeSession([{"name": "other-db"}, {"name": "system"}])
        )
        client.driver = driver

        with patch.object(
            ArcadeDBClient, "_create_database_over_http", new=AsyncMock()
        ) as mock_create:
            await client._ensure_database_exists()

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_creation_posts_create_database_command(self) -> None:
        client = _make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.services.graph_db.arcadedb.arcadedb_client.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            await client._create_database_over_http()

        mock_session.post.assert_called_once_with(
            "http://localhost:2480/api/v1/server",
            json={"command": "create database pipeshub"},
        )

    @pytest.mark.asyncio
    async def test_http_creation_failure_raises(self) -> None:
        client = _make_client()

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="server error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.services.graph_db.arcadedb.arcadedb_client.aiohttp.ClientSession",
            return_value=mock_session,
        ), pytest.raises(RuntimeError, match="ArcadeDB database creation failed"):
            await client._create_database_over_http()
