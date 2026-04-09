"""Comprehensive tests for RedshiftClientFactory.

Tests cover:
- Factory instantiation and inheritance
- create_client delegation to RedshiftClient.build_from_toolset
- Argument passthrough verification
- Return value forwarding
- Error propagation from build_from_toolset
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.tools.factories.base import ClientFactory


class TestRedshiftClientFactory:
    """Tests for RedshiftClientFactory."""

    def test_factory_is_subclass_of_client_factory(self):
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        assert issubclass(RedshiftClientFactory, ClientFactory)

    def test_factory_instantiation(self):
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()
        assert isinstance(factory, ClientFactory)

    @pytest.mark.asyncio
    async def test_create_client_calls_build_from_toolset(self):
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()
        mock_client = MagicMock()
        mock_config_service = MagicMock()
        mock_logger = MagicMock()
        toolset_config = {"host": "localhost", "port": 5439}

        with patch(
            "app.agents.tools.factories.redshift.RedshiftClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=mock_client)
            result = await factory.create_client(
                config_service=mock_config_service,
                logger=mock_logger,
                toolset_config=toolset_config,
                state=None,
            )

            MockClient.build_from_toolset.assert_awaited_once_with(
                toolset_config=toolset_config,
                logger=mock_logger,
                config_service=mock_config_service,
            )
            assert result is mock_client

    @pytest.mark.asyncio
    async def test_create_client_returns_build_from_toolset_result(self):
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()
        expected = MagicMock(name="redshift_client_instance")

        with patch(
            "app.agents.tools.factories.redshift.RedshiftClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=expected)
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "value"},
            )
            assert result is expected

    @pytest.mark.asyncio
    async def test_create_client_without_state(self):
        """Test that state parameter defaults to None and does not affect the call."""
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()

        with patch(
            "app.agents.tools.factories.redshift.RedshiftClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"db": "mydb"},
            )
            MockClient.build_from_toolset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_client_with_state(self):
        """Test that providing a state value does not break the call."""
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()
        mock_state = MagicMock()

        with patch(
            "app.agents.tools.factories.redshift.RedshiftClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=mock_state,
                toolset_config={"db": "mydb"},
                state=mock_state,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_create_client_propagates_exception(self):
        """If build_from_toolset raises, the exception should propagate."""
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()

        with patch(
            "app.agents.tools.factories.redshift.RedshiftClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(
                side_effect=ConnectionError("connection failed")
            )
            with pytest.raises(ConnectionError, match="connection failed"):
                await factory.create_client(
                    config_service=MagicMock(),
                    logger=MagicMock(),
                    toolset_config={"host": "bad_host"},
                )

    @pytest.mark.asyncio
    async def test_create_client_passes_none_logger(self):
        """Test with logger=None to verify it is passed through."""
        from app.agents.tools.factories.redshift import RedshiftClientFactory

        factory = RedshiftClientFactory()

        with patch(
            "app.agents.tools.factories.redshift.RedshiftClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=MagicMock(),
                logger=None,
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["logger"] is None
