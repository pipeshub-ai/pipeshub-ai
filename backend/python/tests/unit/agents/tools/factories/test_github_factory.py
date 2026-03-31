"""Comprehensive tests for GitHubClientFactory.

Tests cover:
- Factory instantiation and inheritance
- create_client delegation to GitHubClient.build_from_toolset
- Argument passthrough verification (toolset_config, logger only)
- Return value forwarding
- Error propagation from build_from_toolset
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.tools.factories.base import ClientFactory


class TestGitHubClientFactory:
    """Tests for GitHubClientFactory."""

    def test_factory_is_subclass_of_client_factory(self):
        from app.agents.tools.factories.github import GitHubClientFactory

        assert issubclass(GitHubClientFactory, ClientFactory)

    def test_factory_instantiation(self):
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()
        assert isinstance(factory, ClientFactory)

    @pytest.mark.asyncio
    async def test_create_client_calls_build_from_toolset(self):
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()
        mock_client = MagicMock()
        mock_logger = MagicMock()
        toolset_config = {"token": "ghp_abc123", "org": "myorg"}

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=mock_client)
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=mock_logger,
                toolset_config=toolset_config,
                state=None,
            )

            MockClient.build_from_toolset.assert_awaited_once_with(
                toolset_config=toolset_config,
                logger=mock_logger,
            )
            assert result is mock_client

    @pytest.mark.asyncio
    async def test_create_client_does_not_pass_config_service(self):
        """GitHub factory only passes toolset_config and logger, not config_service."""
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"token": "abc"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert "config_service" not in call_kwargs

    @pytest.mark.asyncio
    async def test_create_client_returns_build_result(self):
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()
        expected = MagicMock(name="github_client")

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=expected)
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"token": "val"},
            )
            assert result is expected

    @pytest.mark.asyncio
    async def test_create_client_without_state(self):
        """State parameter defaults to None and is not passed to build_from_toolset."""
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"repo": "myrepo"},
            )
            assert result is not None
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert "state" not in call_kwargs

    @pytest.mark.asyncio
    async def test_create_client_with_state(self):
        """Providing state does not break the call (state is accepted but not forwarded)."""
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()
        mock_state = MagicMock()

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(),
                logger=MagicMock(),
                toolset_config={"repo": "myrepo"},
                state=mock_state,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_create_client_propagates_exception(self):
        """If build_from_toolset raises, the exception should propagate."""
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(
                side_effect=ValueError("invalid token")
            )
            with pytest.raises(ValueError, match="invalid token"):
                await factory.create_client(
                    config_service=MagicMock(),
                    logger=MagicMock(),
                    toolset_config={"token": "bad"},
                )

    @pytest.mark.asyncio
    async def test_create_client_with_none_logger(self):
        """Test with logger=None to verify it is passed through."""
        from app.agents.tools.factories.github import GitHubClientFactory

        factory = GitHubClientFactory()

        with patch(
            "app.agents.tools.factories.github.GitHubClient"
        ) as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            await factory.create_client(
                config_service=MagicMock(),
                logger=None,
                toolset_config={"key": "val"},
            )
            call_kwargs = MockClient.build_from_toolset.call_args.kwargs
            assert call_kwargs["logger"] is None
