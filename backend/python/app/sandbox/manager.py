"""SandboxManager -- factory that returns the appropriate executor.

The mode comes from the ``SANDBOX_MODE`` environment variable and has no
default:
- unset / unknown -- no executor; ``get_executor`` raises ``SandboxUnavailableError``
- ``docker``      -- DockerExecutor (container-based; what compose and Helm set)
- ``local``       -- LocalExecutor (subprocess in this service; explicit opt-in)
"""

from __future__ import annotations

import logging

from app.agent_loop_lib.sandbox.coding.settings import (
    SandboxUnavailableError,
    resolve_sandbox_mode,
)
from app.sandbox.base_executor import BaseExecutor
from app.sandbox.models import SandboxMode

__all__ = [
    "SandboxMode",
    "SandboxUnavailableError",
    "get_executor",
    "get_sandbox_mode",
    "reset_executor",
]

logger = logging.getLogger(__name__)

_executor_instance: BaseExecutor | None = None


def get_sandbox_mode() -> SandboxMode:
    """Resolve ``SANDBOX_MODE`` for this executor stack, failing closed.

    Shares the parser with the agent-loop sandbox so both stacks accept the
    same spellings; a value that stack supports but this one does not
    (``e2b``) is still unavailable here rather than downgraded to ``local``.
    """
    backend = resolve_sandbox_mode()
    try:
        return SandboxMode(backend)
    except ValueError:
        raise SandboxUnavailableError(
            f"SANDBOX_MODE={backend!r} is not supported by this executor; "
            f"use one of: {', '.join(m.value for m in SandboxMode)}."
        ) from None


def get_executor() -> BaseExecutor:
    """Return a singleton executor based on the configured sandbox mode.

    Raises ``SandboxUnavailableError`` when no mode is configured or the
    value is not recognised; nothing is cached in that case, so fixing the
    environment and calling again works without a restart.
    """
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

        logger.warning(
            "Initializing LocalExecutor for sandbox: generated code runs as a "
            "subprocess of this service"
        )
        _executor_instance = LocalExecutor()

    return _executor_instance


def reset_executor() -> None:
    """Reset the singleton (useful for testing)."""
    global _executor_instance
    _executor_instance = None
