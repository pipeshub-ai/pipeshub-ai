"""SandboxManager -- factory that returns the appropriate executor.

The mode is determined by the ``SANDBOX_MODE`` environment variable:
- ``local``  (default)  -- uses LocalExecutor (subprocess-based)
- ``docker``            -- uses DockerExecutor (container-based, set for production)
"""

from __future__ import annotations

import logging
import os

from app.sandbox.base_executor import BaseExecutor
from app.sandbox.models import SandboxMode

logger = logging.getLogger(__name__)

_executor_instance: BaseExecutor | None = None


def get_sandbox_mode() -> SandboxMode:
    raw = os.environ.get("SANDBOX_MODE", "local").lower()
    try:
        return SandboxMode(raw)
    except ValueError:
        logger.warning("Unknown SANDBOX_MODE=%s, falling back to local", raw)
        return SandboxMode.LOCAL


def get_executor() -> BaseExecutor:
    """Return a singleton executor based on the configured sandbox mode."""
    global _executor_instance
    if _executor_instance is not None:
        return _executor_instance

    mode = get_sandbox_mode()

    if mode == SandboxMode.DOCKER:
        from app.sandbox.docker_executor import DockerExecutor

        logger.info("Initializing DockerExecutor for sandbox")
        _executor_instance = DockerExecutor()
    else:
        from app.sandbox.local_executor import LocalExecutor

        logger.info("Initializing LocalExecutor for sandbox")
        _executor_instance = LocalExecutor()

    return _executor_instance


def reset_executor() -> None:
    """Reset the singleton (useful for testing)."""
    global _executor_instance
    _executor_instance = None
