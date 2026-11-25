"""PipesHub's side of a run's repository (GIT-FLOW): which repository, whose credential, and the pull request tool.

The run's spec names the repository (``x.pipeshub.repository``: its URL, the base branch, and the GitHub toolset
instance whose connection reads and pushes it). The credential is the person's own toolset token, sealed for each
phase's placeholder: the egress proxy is the only holder that opens it (OWD-1), and its git rule limits it to that
one repository, fetch-only in the setup phase and the session branch's fast-forwards in the agent phase.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from app.harness.assembly.tool_sources import PLATFORM
from app.harness.credentials.tool_credentials import toolset_reference
from app.harness.engines.pull_request import (
    OpenPullRequestTool,
    github_create_tool,
    github_target,
)
from harness_core.engines.git_checkout import GitCheckout, GitPhaseName, session_branch
from harness_core.kernel.durable.effects import classify_tool_effect

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, MutableMapping

    from pydantic import JsonValue

    from app.harness.write_identity.disclosure import WriteIdentitySettings
    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.kernel.durable.effects import ToolEffect
    from harness_core.logs import HarnessLog
    from harness_core.sandbox.egress.sealed import CredentialSealer
    from harness_core.tools.registry import ToolRegistry

REPOSITORY_KEY = "repository"
# GitHub's documented user name for a token sent as a Basic password.
TOKEN_USER = "x-access-token"
DEFAULT_BASE = "main"


@dataclass(frozen=True, slots=True)
class RunRepository:
    url: str
    base_ref: str | None
    toolset_instance: str | None


def run_repository(spec: RunSpec) -> RunRepository | None:
    """The repository the run's spec names, or ``None``."""
    extension = cast("dict[str, JsonValue]", (spec.extensions or {}).get("x.pipeshub") or {})
    raw = extension.get(REPOSITORY_KEY)
    if not isinstance(raw, dict):
        return None
    url, base, instance = raw.get("url"), raw.get("baseRef"), raw.get("toolsetInstanceId")
    if not isinstance(url, str):
        return None
    return RunRepository(
        url, base if isinstance(base, str) else None, instance if isinstance(instance, str) else None
    )


class ToolsetGitCredentials:
    """The person's GitHub toolset token, read when a phase starts and sealed for its placeholder only."""

    def __init__(self, context: Any, instance_id: str, owner: str, sealer: CredentialSealer) -> None:  # noqa: ANN401
        self._context = context
        self._instance = instance_id
        self._owner = owner
        self._sealer = sealer

    async def credential_ref(self, phase: GitPhaseName, placeholder: str) -> str | None:
        del phase
        reference = cast("dict[str, Any]", self._context.toolset_configs.get(self._instance)) or toolset_reference(
            self._instance, self._owner
        )
        record: object = await self._context.tool_credentials().toolset_config(reference)
        stored = cast("dict[str, object]", record).get("credentials") if isinstance(record, dict) else None
        token = cast("dict[str, object]", stored).get("access_token") if isinstance(stored, dict) else None
        if not isinstance(token, str) or not token:
            return None
        return self._sealer.seal(placeholder, f"{TOKEN_USER}:{token}")


def run_checkout(
    repository: RunRepository, *, session_id: str, context: Any, owner: str, sealer: CredentialSealer | None  # noqa: ANN401
) -> GitCheckout:
    """The run's checkout; without a toolset or a sealing key, a public read-only clone."""
    credentials = (
        ToolsetGitCredentials(context, repository.toolset_instance, owner, sealer)
        if repository.toolset_instance is not None and sealer is not None
        else None
    )
    return GitCheckout(
        url=repository.url,
        session_branch=session_branch(session_id),
        base_ref=repository.base_ref,
        credentials=credentials,
    )


def with_pull_request(
    registry: ToolRegistry,
    granted: dict[str, ToolEffect],
    checkout: GitCheckout | None,
    ref_to_url: Callable[[], Mapping[str, str]],
    identity: WriteIdentitySettings | None,
    allowed: Callable[[ToolEffect], bool],
    log: HarnessLog,
    tool_state: MutableMapping[str, Any] | None = None,
) -> dict[str, ToolEffect]:
    """GT-04: a run on a GitHub repository whose grant holds the GitHub toolset can open its pull request.

    It is approved exactly as that toolset's ``create_pull_request`` is (main #4313, owner 2026-10-22: a toolset
    write runs unless a rule asks): the same tags, so the same effect class, and in ``tool_state`` the same approval
    target, so an admin's or the person's rule for ``create_pull_request`` on that instance (Ask, Block) holds it too."""
    if checkout is None or checkout.credentials is None:
        return granted
    target = github_target(checkout.url, checkout.session_branch, checkout.base_ref or DEFAULT_BASE)
    create = github_create_tool(registry)
    if target is None or create is None:
        log.info("engine.git.no_pull_request", reasonCode="no_github_toolset" if target else "not_github")
        return granted
    tool = OpenPullRequestTool(create, target, ref_to_url, identity, log)
    registry.register_tool(tool)
    if tool_state is not None:
        # Imported here: the module pulls in the loop's message stack, which an idle worker never loads (D7).
        from app.agents.agent_loop.tool_approvals import TOOLSET_TOOLS_BY_PATH

        targets = cast("dict[str, Any]", tool_state.setdefault(TOOLSET_TOOLS_BY_PATH, {}))
        own = targets.get(registry.path_for_name(create.name) or "")
        if own is not None:
            targets[tool.path] = replace(own, tool=tool)
    effect = replace(
        classify_tool_effect(tool.name, registry.tags_for_name(create.name)), tool_id=f"{PLATFORM}@git/{tool.name}"
    )
    if not allowed(effect):
        return granted
    return {**granted, tool.name: effect}


def ref_links(context: Any) -> Callable[[], Mapping[str, str]]:  # noqa: ANN401
    """The run's citation refs and their links, read when the tool runs (refs are minted as tools return)."""

    def read() -> Mapping[str, str]:
        mapper = context.tool_state.get("citation_ref_mapper")
        links = getattr(mapper, "ref_to_url", None)
        return cast("Mapping[str, str]", links) if isinstance(links, dict) else {}

    return read


__all__ = [
    "REPOSITORY_KEY",
    "RunRepository",
    "ToolsetGitCredentials",
    "ref_links",
    "run_checkout",
    "run_repository",
    "with_pull_request",
]
