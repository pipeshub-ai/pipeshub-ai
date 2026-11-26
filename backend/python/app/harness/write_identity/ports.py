"""Who a run's external writes are from (OWD-18): an extension point, read with ``write_identity_port()``.

This build's default, ``UserWriteIdentity``, sends every write with the user's own account and the fixed AI disclosure
(``disclosure.DISCLOSURES``), whatever the run's pin says. An edition's port may send writes
of some action types from another identity; its three calls are where a run reaches it:

- ``prepare``: before the run's tools load (it may confirm connections the run will send from);
- ``tools``: each toolset instance's tools as the loader built them, which it may replace with the same tools over
  another connection (``build`` makes the toolset's tools over another client, with the gate its calls await);
- ``settings``: the run's ``WriteIdentitySettings``, which the write path's middleware and the pull request tool read.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Final, override

from app import edition_services
from app.harness.write_identity.settings import user_write_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.instance_creator import ToolInstanceCreator
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import ToolEnvironment
    from app.harness.run_policy.ports import RunPolicy
    from app.harness.write_identity.disclosure import WriteIdentitySettings
    from harness_core.logs import HarnessLog
    from harness_core.tools.base import Tool


class WriteIdentityPort(ABC):
    @abstractmethod
    async def prepare(self, run: RunContext, env: ToolEnvironment, policy: RunPolicy, log: HarnessLog) -> None:
        """Before the run's tools load."""

    @abstractmethod
    async def tools(
        self,
        context: AgentContext,
        creator: ToolInstanceCreator,
        action_class: type,
        app_name: str,
        instance: str,
        tools: Sequence[Tool],
        build: Callable[[object, Any], Sequence[Tool]],
    ) -> list[Tool]:
        """``instance``'s tools as the run uses them."""

    @abstractmethod
    async def settings(
        self, run: RunContext, env: ToolEnvironment, policy: RunPolicy, log: HarnessLog
    ) -> WriteIdentitySettings:
        """The run's write identity, once its tools are loaded."""


class UserWriteIdentity(WriteIdentityPort):
    """Every write as the user, with the AI disclosure."""

    @override
    async def prepare(self, run: RunContext, env: ToolEnvironment, policy: RunPolicy, log: HarnessLog) -> None:
        return None

    @override
    async def tools(
        self,
        context: AgentContext,
        creator: ToolInstanceCreator,
        action_class: type,
        app_name: str,
        instance: str,
        tools: Sequence[Tool],
        build: Callable[[object, Any], Sequence[Tool]],
    ) -> list[Tool]:
        return list(tools)

    @override
    async def settings(
        self, run: RunContext, env: ToolEnvironment, policy: RunPolicy, log: HarnessLog
    ) -> WriteIdentitySettings:
        return await user_write_identity(run, env, log)


USER_WRITE_IDENTITY: Final = UserWriteIdentity()


def write_identity_port() -> WriteIdentityPort:
    """The edition's port; ``USER_WRITE_IDENTITY`` in this build."""
    return edition_services.harness_edition().port(WriteIdentityPort, USER_WRITE_IDENTITY)


__all__ = ["USER_WRITE_IDENTITY", "UserWriteIdentity", "WriteIdentityPort", "write_identity_port"]
