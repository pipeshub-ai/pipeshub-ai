"""OWD-16: a workspace belongs to one principal.

In a shared conversation the session's workspace (``ws:{sessionId}``) is its owner's. A collaborator's
loop run (``ENABLE_COLLABORATOR_TURNS`` on, SS2) runs as the collaborator, so ``run_code`` must read and
save the collaborator's own workspace, never the owner's: otherwise files fetched under two people's
access would mix in one place.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import run_workspace
from harness_core.logs import HarnessLog
from harness_core.workspace.forks import (
    JoinedChild,
    child_fork_id,
    principal_workspace_id,
    session_workspace_id,
    workspace_of_principal,
)

from . import world as w

OWNER = "owner-1"


class _Route:
    """Enough of an attempt route for the workspace store and key provider to be built."""

    org_id = w.ORG
    run_id = "run-1"

    def log(self, name: str) -> HarnessLog:
        return HarnessLog.named(name)


def _spec(role: str | None) -> RunSpec:
    spec = dump_contract(w.pinned_spec(tool_ids=[]))
    if role is not None:
        spec["extensions"]["x.pipeshub"]["audience"] = {  # type: ignore[index]
            "sessionRole": role,
            "ownerRef": f"p_{OWNER}" if role == "collaborator" else f"p_{w.ACTOR}",
            "ownRuns": [],
        }
    return validate_contract(RunSpec, spec)


async def _workspace_id(role: str | None) -> str | None:
    claim = dataclasses.replace(w.claim(_spec(role)), attempt_route=_Route())  # type: ignore[arg-type]
    assembler = PipesHubRuntimeAssembler(w.Services())
    workspace = await assembler._run_code_workspace(claim, RunContext.from_claim(claim))
    return None if workspace is None else workspace.workspace_id


@pytest.mark.asyncio
async def test_owd16_a_collaborators_run_never_gets_the_owners_session_workspace() -> None:
    """OWD-16 / SEC-AZ-04: with collaborator turns on, the collaborator's run_code works in their own workspace."""
    workspace_id = await _workspace_id("collaborator")
    assert workspace_id is not None
    assert workspace_id != session_workspace_id("ses-1")
    assert workspace_id == principal_workspace_id("ses-1", w.ACTOR, collaborator=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "owner"])
async def test_owd16_the_sessions_owner_keeps_the_session_workspace(role: str | None) -> None:
    """The owner's runs, alone or in a shared conversation, keep ``ws:{sessionId}`` (engines read it too)."""
    assert await _workspace_id(role) == session_workspace_id("ses-1")


@pytest.mark.parametrize(
    ("workspace_id", "owner", "collaborator"),
    [
        ("ws:s1", True, False),
        ("ws:s1:fork:c1", True, False),
        ("ws:s1:p:u2", False, True),
        ("ws:s1:p:u2:fork:c1", False, True),
        ("ws:s1:p:u3", False, False),
        ("ws:s1:p:u2x", False, False),
        ("ws:s2", False, False),
    ],
)
def test_owd16_each_principal_reaches_only_its_own_workspaces(
    workspace_id: str, owner: bool, collaborator: bool
) -> None:
    """The rule Node's workspace routes apply too: the owner's tree and a collaborator's never overlap."""
    assert workspace_of_principal(workspace_id, "s1", "u1", collaborator=False) is owner
    assert workspace_of_principal(workspace_id, "s1", "u2", collaborator=True) is collaborator


def test_owd16_principal_workspace_ids_are_what_node_expects() -> None:
    """Pinned against ``principalWorkspaceId`` in Node's ``domain/workspace.ts``."""
    expected: dict[str, Any] = {
        "owner": "ws:s1",
        "collaborator": "ws:s1:p:u2",
    }
    assert principal_workspace_id("s1", "u1", collaborator=False) == expected["owner"]
    assert principal_workspace_id("s1", "u2", collaborator=True) == expected["collaborator"]


def test_owd16_a_collaborators_handoff_forks_and_merges_their_own_workspace() -> None:
    """A collaborator's turn that mounts ``/workspace`` (a code_task hand-off) forks and merges back into the
    collaborator's workspace; the owner's children keep today's ``ws:{sessionId}:fork:{child}``."""
    spec = dump_contract(_spec("collaborator"))
    spec["extensions"]["x.pipeshub"]["mounts"] = ["/workspace"]  # type: ignore[index]
    mounted = validate_contract(RunSpec, spec)
    own = principal_workspace_id("ses-1", w.ACTOR, collaborator=True)
    assert run_workspace(mounted, "ses-1") == own
    joined = JoinedChild("org", "ses-1", "run-1", own, "c1", "answered")
    assert joined.fork_id == f"{own}:fork:c1"
    assert JoinedChild("org", "ses-1", "run-1", "ws:ses-1:fork:c0", "c1", None).fork_id == child_fork_id("ses-1", "c1")
