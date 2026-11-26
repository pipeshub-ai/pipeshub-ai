"""SandboxManager -- factory that returns the appropriate executor.

The mode comes from the ``SANDBOX_MODE`` environment variable and has no
default:
- unset / unknown -- no executor; ``get_executor`` raises ``SandboxUnavailableError``
- ``broker``      -- BrokerExecutor (sandboxes through the sandbox broker; what compose and Helm set)
- ``docker``      -- DockerExecutor (container-based, through the Docker API)
- ``local``       -- LocalExecutor (subprocess in this service); also needs
  ``SANDBOX_ALLOW_LOCAL=true``, otherwise refused like an unknown value
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.coding.settings import (
    SandboxUnavailableError,
    resolve_sandbox_mode,
)
from app.sandbox.base_executor import BaseExecutor
from app.sandbox.models import SandboxMode
from harness_core.tools.decorators import BoundMethodTool
from harness_core.tools.tags import SINK_TAG_KEY, TAG_SINK_SELF

if TYPE_CHECKING:
    from harness_core.tools.base import Tool

__all__ = [
    "SandboxMode",
    "SandboxUnavailableError",
    "get_executor",
    "get_sandbox_mode",
    "reset_executor",
    "with_executor_sink",
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
    if _executor_instance is None:
        _executor_instance = _new_executor(get_sandbox_mode())
    return _executor_instance


def _new_executor(mode: SandboxMode) -> BaseExecutor:
    if mode == SandboxMode.BROKER:
        from app.harness.sandbox.client import process_broker_client
        from app.sandbox.broker_executor import BrokerExecutor

        logger.info("Initializing BrokerExecutor for sandbox")
        return BrokerExecutor(broker=process_broker_client.get)
    if mode == SandboxMode.DOCKER:
        from app.sandbox.docker_executor import DockerExecutor

        logger.info("Initializing DockerExecutor for sandbox")
        return DockerExecutor()
    from app.sandbox.local_executor import LocalExecutor

    logger.warning(
        "Initializing LocalExecutor for sandbox: generated code runs as a "
        "subprocess of this service"
    )
    return LocalExecutor()


def reset_executor() -> None:
    """Reset the singleton (useful for testing)."""
    global _executor_instance
    _executor_instance = None


def with_executor_sink(tools: list[Tool]) -> list[Tool]:
    """``tools`` (declared ``sink=external``) with ``sink=self`` where this process's executor isolates the run.

    Docker and broker run the code with no network at all, and their install step sends only allowlisted package
    names to the index; local runs it on the host with the host's network, and an unset mode runs nothing.
    """
    try:
        mode = get_sandbox_mode()
    except SandboxUnavailableError:
        return tools
    if mode not in (SandboxMode.DOCKER, SandboxMode.BROKER):
        return tools
    return [
        t.with_tags([*(tag for tag in t.tags if tag.key != SINK_TAG_KEY), TAG_SINK_SELF])
        if isinstance(t, BoundMethodTool)
        else t
        for t in tools
    ]
