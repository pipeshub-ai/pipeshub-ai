"""D-MCP-5: whether a run uses only the MCP tools an admin approved is the admission policy's; this build approves none.

Shared code that branches on a policy requiring approval (pinning a run to approved tools, the claim's checks, the
agent-save refusal) is tested with ``ApprovalsRequired``, a stand-in for such a policy over an in-memory list of
approved tools: ``pytest.mark.usefixtures("mcp_approvals_required")`` after importing the fixture, and
``approve_tools`` for what an admin approved. Tests of the open default whose configuration double cannot answer a
store read use ``mcp_approvals_open``: the seam's own default, whichever policy the build binds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app import edition_config
from app.harness.mcp_hub.manifests import descriptor_of
from app.harness.mcp_hub.tool_admission import OpenMcpToolAdmission
from harness_core.mcp_hub.manifest import (
    ApprovedManifest,
    ManifestReview,
    ToolDescriptor,
    review_manifest,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from app.agents.mcp.models import MCPToolInfo


class ApprovalsRequired(OpenMcpToolAdmission):
    """A policy that requires an admin's approval for every org, over ``approved`` (instance -> tool -> digest)."""

    def __init__(self) -> None:
        self.approved: dict[str, dict[str, str]] = {}

    async def requires_admin_approval(self, org_id: str, config_service: object) -> bool:
        return True

    def _manifest(self, instance_id: str) -> ApprovedManifest | None:
        tools = self.approved.get(instance_id)
        return None if tools is None else ApprovedManifest(dict(tools), approved_by="p_admin", approved_at=1)

    async def admit(
        self,
        config_service: Any,  # noqa: ANN401
        infos: Sequence[MCPToolInfo],
        *,
        tenant: str,
        instance_id: str,
        at_ms: int,
        instance: Mapping[str, Any] | None = None,
        raw: Mapping[str, ToolDescriptor] | None = None,
    ) -> tuple[list[MCPToolInfo], ManifestReview]:
        review = review_manifest(self._manifest(instance_id), [descriptor_of(i) for i in infos])
        admitted = {t.name for t in review.admitted}
        return [i for i in infos if i.name in admitted], review

    async def approved_manifest(self, config_service: Any, instance_id: str) -> ApprovedManifest | None:  # noqa: ANN401
        return self._manifest(instance_id)

    async def unapproved(self, config_service: Any, instance_id: str, names: Iterable[str]) -> list[str]:  # noqa: ANN401
        known = self.approved.get(instance_id, {})
        return sorted({name for name in names if name not in known})


def approve_tools(instance_id: str, tools: Iterable[ToolDescriptor]) -> None:
    """What an admin approved for ``instance_id``, where the bound policy is ``ApprovalsRequired``; else nothing."""
    policy = getattr(edition_config, "mcp_tool_admission", None)
    if isinstance(policy, ApprovalsRequired):
        policy.approved[instance_id] = {t.name: t.digest() for t in tools}


@pytest.fixture
def mcp_approvals_required(monkeypatch: pytest.MonkeyPatch) -> Iterator[ApprovalsRequired]:
    policy = ApprovalsRequired()
    monkeypatch.setattr(edition_config, "mcp_tool_admission", policy, raising=False)
    yield policy


@pytest.fixture
def mcp_approvals_open(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(edition_config, "mcp_tool_admission", None, raising=False)
    yield
