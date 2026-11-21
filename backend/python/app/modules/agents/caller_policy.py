"""Edition hooks for who an agent run acts as.

`get_agent_caller_policy()` returns the policy the running edition installs (see
`app.edition_services.agent_caller_policy`). `AgentCallerPolicy` itself is the
default: a service-account agent runs as its own identity (the service account
it is bound to, else its creator) with the credentials kept for the agent, and
tells the model the caller's name; every other agent runs as the person asking.
An edition only overrides what it changes.

Imports here stay lazy: `app.edition_services` imports this module early.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# A runtime import: FastAPI reads `guard_agent_route`'s annotation to hand it the request.
from fastapi import Request  # noqa: TC002 - read at runtime by FastAPI

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["AgentCallerPolicy", "default_agent_caller_policy", "get_agent_caller_policy", "guard_agent_route"]


class AgentCallerPolicy:
    def service_agents_run_as_themselves(self) -> bool:
        """Whether a service-account agent runs as its own identity, with the credentials kept for the
        agent, rather than as the person asking. Default: yes."""
        return True

    async def guard_route(self, request: Request) -> None:
        """Raise to refuse, on a route that runs an agent and before any work, a caller agents are not
        run for. Default: none is refused."""

    def caller_fields(self, user: Mapping[str, Any]) -> dict[str, Any]:
        """Keys a route's caller context (`_get_user_context`) adds from the authenticated principal
        `user`, for `check_caller`. Default: none."""
        return {}

    def check_caller(self, user_context: Mapping[str, Any]) -> None:
        """Raise to refuse, before any work, a caller agents are not run for. Default: none is refused."""

    async def admit_agent(
        self, agent: Mapping[str, Any], *, graph_provider: IGraphDBProvider, org_id: str, agent_id: str,
    ) -> None:
        """Raise to refuse running `agent` as the person asking, before their record is read. Default: the
        permission check on the agent decides."""

    def admit_person(self, user_doc: Mapping[str, Any]) -> None:
        """Raise to refuse the person asking, from their own record. Default: the permission check decides."""

    def run_query_fields(self, *, service_agent: bool) -> dict[str, Any]:
        """Keys a chat route adds to the query it hands the agent loop. `service_agent` is the agent
        record's own flag. Default: none."""
        return {}

    def run_loop_options(self, *, service_agent: bool) -> dict[str, Any]:
        """Keyword arguments a chat route adds to its call of the agent loop. Default: none."""
        return {}


_DEFAULT = AgentCallerPolicy()


def default_agent_caller_policy() -> AgentCallerPolicy:
    return _DEFAULT


def get_agent_caller_policy() -> AgentCallerPolicy:
    from app import edition_services

    return edition_services.agent_caller_policy()


async def guard_agent_route(request: Request) -> None:
    """The dependency of every route that runs an agent (`AgentCallerPolicy.guard_route`). It stays
    `async`: FastAPI runs a sync dependency in a worker thread, whose context changes never reach the
    endpoint, so what a check records for the run would be lost."""
    await get_agent_caller_policy().guard_route(request)
