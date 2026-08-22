"""
Abstract base class for client factories.
"""

import asyncio
import concurrent.futures
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from app.modules.agents.qna.chat_state import ChatState


class ToolsetAuthError(Exception):
    """Raised by a toolset client factory to surface a specific, user-facing reason
    a freshly authenticated OAuth setup is unusable (e.g. the token reaches multiple
    sites). Generic callers — the tool wrapper and the OAuth callback — show
    ``str(error)`` to the user and skip marking the toolset authenticated. Factories
    that never raise it are unaffected.

    ``title`` is an optional notification heading the factory supplies, so the generic
    OAuth callback passes it straight through — no provider naming lives in the route."""

    def __init__(self, message: str, *, title: str | None = None) -> None:
        super().__init__(message)
        self.title = title


class ClientFactory(ABC):
    """
    Abstract factory for creating tool clients.

    TOOLSET-ONLY ARCHITECTURE:
    - All clients are created from toolset_config (from etcd)
    - toolset_config is REQUIRED - no fallbacks to connector instances
    - Clean, single-purpose design
    """

    @abstractmethod
    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: Dict[str, Any],
        state: Optional[ChatState] = None
    ) -> object:
        """
        Create and return a client instance asynchronously.

        Args:
            config_service: Configuration service instance
            logger: Logger instance (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            Client instance
        """
        pass

    def create_client_sync(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: Dict[str, Any],
        state: Optional[ChatState] = None
    ) -> object:
        """
        Synchronous wrapper for client creation.

        Handles both sync and async contexts automatically.

        Args:
            config_service: Configuration service instance
            logger: Logger instance (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            Client instance
        """
        try:
            # Check if we're in an async context
            asyncio.get_running_loop()

            # We're in an async context, use thread pool to run async code
            return self._run_in_thread_pool(config_service, logger, toolset_config, state)

        except RuntimeError:
            # No running loop, we can use asyncio.run directly
            return asyncio.run(self.create_client(config_service, logger, toolset_config, state))

    def _run_in_thread_pool(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: Dict[str, Any],
        state: Optional[ChatState] = None
    ) -> object:
        """
        Run async client creation in a thread pool.
        Args:
            config_service: Configuration service instance
            logger: Logger instance (optional)
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            Client instance
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                self.create_client(config_service, logger, toolset_config, state)
            )
            return future.result()

    async def test_connection(
        self,
        *,
        access_token: str,
        auth_config: dict[str, Any],
        config_service: object,
        logger: object | None,
    ) -> None:
        """Optionally validate a freshly authenticated OAuth setup for this toolset.

        ``auth_config`` is the toolset's merged auth/config dict (per-user/instance
        auth overlaid on the shared OAuth-app config) — the factory inspects whatever
        fields it needs. Override to raise ``ToolsetAuthError`` (with a user-facing
        message) when the setup is unusable — e.g. the OAuth token can reach multiple
        sites and none is selected. Default: no-op, so toolsets that don't implement
        it are unaffected."""
        return
