from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arcadedb.arcadedb_provider import ArcadeDBProvider


@pytest.fixture
def arcadedb_provider() -> ArcadeDBProvider:
    provider = ArcadeDBProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


class TestConnectionManagement:
    @pytest.mark.asyncio
    async def test_connect_success_uses_env_values(self):
        provider = ArcadeDBProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict(
            "os.environ",
            {
                "ARCADEDB_URI": "bolt://arcadedb.test:7687",
                "ARCADEDB_HTTP_URI": "http://arcadedb.test:2480",
                "ARCADEDB_USERNAME": "arcade-user",
                "ARCADEDB_PASSWORD": "secret",
                "ARCADEDB_DATABASE": "graphdb",
            },
            clear=False,
        ), patch(
            "app.services.graph_db.arcadedb.arcadedb_provider.ArcadeDBClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()

        assert result is True
        mock_client_cls.assert_called_once()
        _, kwargs = mock_client_cls.call_args
        assert kwargs["uri"] == "bolt://arcadedb.test:7687"
        assert kwargs["http_uri"] == "http://arcadedb.test:2480"
        assert kwargs["username"] == "arcade-user"
        assert kwargs["password"] == "secret"
        assert kwargs["database"] == "graphdb"
        assert provider.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_uses_defaults_for_optional_env_vars(self):
        provider = ArcadeDBProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {"ARCADEDB_PASSWORD": "secret"}, clear=True), patch(
            "app.services.graph_db.arcadedb.arcadedb_provider.ArcadeDBClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()

        assert result is True
        _, kwargs = mock_client_cls.call_args
        assert kwargs["uri"] == "bolt://localhost:7687"
        assert kwargs["http_uri"] == "http://localhost:2480"
        assert kwargs["username"] == "root"
        assert kwargs["database"] == "pipeshub"

    @pytest.mark.asyncio
    async def test_connect_returns_false_when_password_missing(self):
        provider = ArcadeDBProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {}, clear=True):
            result = await provider.connect()

        assert result is False
        assert provider.client is None

    @pytest.mark.asyncio
    async def test_connect_returns_false_when_client_connect_returns_false(self):
        provider = ArcadeDBProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {"ARCADEDB_PASSWORD": "secret"}, clear=True), patch(
            "app.services.graph_db.arcadedb.arcadedb_provider.ArcadeDBClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()

        assert result is False
        assert provider.client is None


class TestSchema:
    def test_required_field_constraints_are_skipped(self, arcadedb_provider: ArcadeDBProvider) -> None:
        """ArcadeDB enforces NOT NULL existence constraints eagerly on node
        creation inside MERGE, before the write's own SET clause has populated
        the property — breaking the MERGE + SET pattern used throughout this
        provider. Skipping them leaves the same coverage as Neo4j Community,
        where this DDL is rejected outright."""
        assert arcadedb_provider._generate_required_field_constraints() == []

    @pytest.mark.asyncio
    async def test_ensure_schema_still_creates_unique_constraints_and_indexes(
        self, arcadedb_provider: ArcadeDBProvider
    ) -> None:
        arcadedb_provider.client.execute_query = AsyncMock(return_value=[])
        with patch.object(
            ArcadeDBProvider, "_initialize_departments", new=AsyncMock()
        ):
            result = await arcadedb_provider.ensure_schema()

        assert result is True
        executed = [c.args[0] for c in arcadedb_provider.client.execute_query.call_args_list]
        assert any("CREATE CONSTRAINT" in q and "IS UNIQUE" in q for q in executed)
        assert not any("IS NOT NULL" in q for q in executed)
        assert any("CREATE INDEX" in q for q in executed)
