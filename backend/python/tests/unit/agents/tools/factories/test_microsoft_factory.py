"""Comprehensive tests for MSGraphClientFactory.

Tests cover:
- Factory instantiation with service_name and inheritance
- service_name attribute storage
- create_client delegation to MSGraphClient.build_from_toolset
- Argument passthrough verification (including service_name and config_service)
- Return value forwarding
- Error propagation from build_from_toolset
- Different service_name values (one_drive, sharepoint, etc.)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.tools.factories.base import ClientFactory


class TestMSGraphClientFactory:
    """Tests for MSGraphClientFactory."""

    def test_factory_is_subclass_of_client_factory(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        assert issubclass(MSGraphClientFactory, ClientFactory)

    def test_factory_instantiation_stores_service_name(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")
        assert factory.service_name == "one_drive"

    def test_factory_instantiation_sharepoint(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="sharepoint")
        assert factory.service_name == "sharepoint"
        assert isinstance(factory, ClientFactory)

    def test_factory_instantiation_outlook(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="outlook")
        assert factory.service_name == "outlook"

    @pytest.mark.asyncio
    async def test_create_client_calls_build_from_toolset(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")
        mock_client = MagicMock()
        mock_config_service = MagicMock()
        mock_logger = MagicMock()
        toolset_config = {"tenant_id": "abc", "client_id": "xyz"}

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
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
                service_name="one_drive",
                logger=mock_logger,
                config_service=mock_config_service,
            )
            assert result is mock_client

    @pytest.mark.asyncio
    async def test_create_client_passes_service_name(self):
        """Verify service_name from __init__ is forwarded to build_from_toolset."""
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="sharepoint")

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["service_name"] == "sharepoint"

    @pytest.mark.asyncio
    async def test_create_client_passes_config_service(self):
        """Verify config_service is forwarded to build_from_toolset."""
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")
        mock_config_service = MagicMock(name="config_svc")

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=mock_config_service,
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["config_service"] is mock_config_service

    @pytest.mark.asyncio
    async def test_create_client_returns_build_result(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")
        expected = MagicMock(name="ms_graph_client")

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=expected)
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"tenant": "t1"},
            )
            assert result is expected

    @pytest.mark.asyncio
    async def test_create_client_without_state(self):
        """State defaults to None and is accepted but not forwarded."""
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            assert result is not None
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert "state" not in call_kwargs

    @pytest.mark.asyncio
    async def test_create_client_with_state(self):
        """Providing state does not break the call."""
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")
        mock_state = MagicMock()

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
                state=mock_state,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_create_client_propagates_exception(self):
        """If build_from_toolset raises, the exception should propagate."""
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(
                side_effect=PermissionError("access denied")
            )
            with pytest.raises(PermissionError, match="access denied"):
                await factory.create_client(
                    config_service=MagicMock(),
                    logger=MagicMock(),
                    toolset_config={"bad": "config"},
                )

    @pytest.mark.asyncio
    async def test_create_client_with_none_logger(self):
        """Test with logger=None to verify it is passed through."""
        from app.agents.tools.factories.microsoft import MSGraphClientFactory

        factory = MSGraphClientFactory(service_name="one_drive")

        with patch(
            "app.agents.tools.factories.microsoft.MSGraphClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=MagicMock(),
                logger=None,
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["logger"] is None
