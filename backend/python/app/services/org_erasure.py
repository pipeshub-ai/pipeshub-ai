"""Erasing what the query service holds for a deleted org (CLEANUP-G1).

Node calls this when an org is deleted, and again from the org's erasure job
once every session's erasure completed; this takes what no session names: every artifact, chat
attachment (sent or not), skill candidate and agent version of the org, one
bounded page per call, in that order. Each eraser is idempotent and stores'
bytes go before the graph nodes, so Node repeats the call while ``remaining``
and after a crash. The org's knowledge publications are erased by the
org-deleted event (``_handle_org_deleted``), not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.agent_loop.skills.graph_store import erase_org_skill_candidates
from app.agents.versions import AgentVersionStore
from app.services.artifact_registry.erasure import ArtifactEraser
from app.services.chat_attachments.erasure import AttachmentBlobs, AttachmentEraser

ORG_ERASE_PAGE = 50

__all__ = ["ORG_ERASE_PAGE", "OrgErasureReport", "erase_org_page"]


@dataclass(frozen=True)
class OrgErasureReport:
    attachments: int
    artifacts: int
    blobs: int
    skill_candidates: int
    remaining: bool
    # The frozen definitions, prompts included, of the org's agents (OWD-14).
    agent_versions: int = 0


async def _nobody(_user_id: str, _record_ids: list[str]) -> set[str]:
    return set()


async def erase_org_page(
    graph: Any,  # noqa: ANN401 - IGraphDBProvider
    blobs: AttachmentBlobs,
    org_id: str,
    *,
    page: int = ORG_ERASE_PAGE,
) -> OrgErasureReport:
    """One page of one kind, the next kind only once the one before is gone."""
    artifacts = await ArtifactEraser(graph, blobs, page=page).erase_org(org_id)
    if artifacts.remaining or artifacts.artifacts:
        return OrgErasureReport(0, artifacts.artifacts, artifacts.blobs, 0, remaining=True)
    # The org is erased whole, so no uploader's ownership is asked.
    uploads = await AttachmentEraser(graph, blobs, _nobody, page=page).erase_org_uploads(org_id)
    if uploads.remaining or uploads.attachments:
        return OrgErasureReport(uploads.attachments, 0, uploads.blobs, 0, remaining=True)
    candidates, more = await erase_org_skill_candidates(graph, org_id, page=page)
    if more or candidates:
        return OrgErasureReport(0, 0, 0, candidates, remaining=True)
    versions, more = await AgentVersionStore(graph).erase_org(org_id, page=page)
    return OrgErasureReport(0, 0, 0, 0, remaining=more or versions > 0, agent_versions=versions)
