"""Comprehensive tests for GoogleClientFactory.

Tests cover:
- Factory instantiation with service_name and version, including defaults
- service_name and version attribute storage
- create_client delegation to GoogleClient.build_from_toolset
- The chained .get_client() call on the build result
- Argument passthrough verification (service_name, version, config_service)
- Return value forwarding (from get_client, not from build_from_toolset)
- Error propagation from build_from_toolset and get_client
- Different service_name and version combinations
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.tools.factories.base import ClientFactory


class TestGoogleClientFactory:
    """Tests for GoogleClientFactory."""

    def test_factory_is_subclass_of_client_factory(self):
        from app.agents.tools.factories.google import GoogleClientFactory

        assert issubclass(GoogleClientFactory, ClientFactory)

    def test_factory_instantiation_default_version(self):
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail")
        assert factory.service_name == "gmail"
        assert factory.version == "v3"

    def test_factory_instantiation_custom_version(self):
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="calendar", version="v1")
        assert factory.service_name == "calendar"
        assert factory.version == "v1"

    def test_factory_instantiation_drive(self):
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="drive", version="v3")
        assert factory.service_name == "drive"
        assert factory.version == "v3"
        assert isinstance(factory, ClientFactory)

    @pytest.mark.asyncio
    async def test_create_client_calls_build_from_toolset(self):
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail", version="v1")
        mock_inner_client = MagicMock(name="inner_client")
        mock_google_client = MagicMock()
        mock_google_client.get_client.return_value = mock_inner_client
        mock_config_service = MagicMock()
        mock_logger = MagicMock()
        toolset_config = {"credentials": "cred_data"}

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(
                return_value=mock_google_client
            )
            result = await factory.create_client(
                config_service=mock_config_service,
                logger=mock_logger,
                toolset_config=toolset_config,
                state=None,
            )

            MockClient.build_from_toolset.assert_awaited_once_with(
                toolset_config=toolset_config,
                service_name="gmail",
                logger=mock_logger,
                config_service=mock_config_service,
                version="v1",
            )
            mock_google_client.get_client.assert_called_once()
            assert result is mock_inner_client

    @pytest.mark.asyncio
    async def test_create_client_returns_get_client_result(self):
        """The factory returns the result of .get_client(), not the GoogleClient itself."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="drive")
        inner = MagicMock(name="raw_google_service")
        google_client = MagicMock()
        google_client.get_client.return_value = inner

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=google_client)
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            assert result is inner
            assert result is not google_client

    @pytest.mark.asyncio
    async def test_create_client_passes_service_name(self):
        """Verify service_name from __init__ is forwarded to build_from_toolset."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="calendar", version="v3")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["service_name"] == "calendar"

    @pytest.mark.asyncio
    async def test_create_client_passes_version(self):
        """Verify version from __init__ is forwarded to build_from_toolset."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail", version="v1")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["version"] == "v1"

    @pytest.mark.asyncio
    async def test_create_client_passes_config_service(self):
        """Verify config_service is forwarded to build_from_toolset."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="drive")
        mock_config_service = MagicMock(name="config_svc")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            await factory.create_client(
                config_service=mock_config_service,
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["config_service"] is mock_config_service

    @pytest.mark.asyncio
    async def test_create_client_default_version_v3(self):
        """When no version is given, default v3 should be passed to build_from_toolset."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="drive")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["version"] == "v3"

    @pytest.mark.asyncio
    async def test_create_client_without_state(self):
        """State defaults to None and is not forwarded to build_from_toolset."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
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
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail")
        mock_state = MagicMock()

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"key": "val"},
                state=mock_state,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_create_client_propagates_build_exception(self):
        """If build_from_toolset raises, the exception should propagate."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(
                side_effect=RuntimeError("auth failed")
            )
            with pytest.raises(RuntimeError, match="auth failed"):
                await factory.create_client(
                    config_service=MagicMock(),
                    logger=MagicMock(),
                    toolset_config={"bad": "cred"},
                )

    @pytest.mark.asyncio
    async def test_create_client_propagates_get_client_exception(self):
        """If get_client() raises, the exception should propagate."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.side_effect = ValueError("client not initialized")
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            with pytest.raises(ValueError, match="client not initialized"):
                await factory.create_client(
                    config_service=MagicMock(),
                    logger=MagicMock(),
                    toolset_config={"key": "val"},
                )

    @pytest.mark.asyncio
    async def test_create_client_with_none_logger(self):
        """Test with logger=None to verify it is passed through."""
        from app.agents.tools.factories.google import GoogleClientFactory

        factory = GoogleClientFactory(service_name="gmail")

        with patch(
            "app.agents.tools.factories.google.GoogleClient"
        ) as MockClient:
            mock_gc = MagicMock()
            mock_gc.get_client.return_value = MagicMock()
            MockClient.build_from_toolset = AsyncMock(return_value=mock_gc)
            await factory.create_client(
                config_service=MagicMock(),
                logger=None,
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["logger"] is None
