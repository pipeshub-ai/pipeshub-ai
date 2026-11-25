"""Which cited sources the caller may read now (P1b C-6, C-18).

Node asks this once per shared-conversation view, with the viewer's own
credential, and hides every citation whose source is not readable. The org
and user come from the token, never from the body. A record is decided by
its ACL; a live item (a file a tool read straight from a connector) by the
ACL of its indexed copy in the viewer's org, and ``unresolved`` when there is
none. An artifact (``artifactIds``, SS0) is decided by its provenance
(``ArtifactViewerAccess``), never by the permission edge sharing gave the
reader. The body and answer are the contract's ``PipeshubRecordAccessQuery``
and ``PipeshubRecordAccessAnswer``.
"""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.core.org_context import bound_org_id
from app.harness.contracts.generated.pipeshub.citation_source import (
    PipeshubLiveSource,
    PipeshubRecordSource,
)
from app.harness.contracts.generated.pipeshub.record_access_answer import (
    PipeshubArtifactDecision,
    PipeshubRecordAccessAnswer,
    PipeshubSourceDecision,
)
from app.harness.contracts.generated.pipeshub.record_access_query import (
    PipeshubRecordAccessQuery,  # noqa: TC001 - FastAPI reads the body annotation at runtime
)
from app.harness.knowledge.service import KnowledgePublications
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.viewer_access import artifact_viewer_access
from app.services.record_content import TieredRecordAuthorizer

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_RECORD_IDS = 1000

Source = PipeshubRecordSource | PipeshubLiveSource


@router.post(
    "/records/access",
    dependencies=[
        Depends(require_scopes(OAuthScopes.CONVERSATION_READ, OAuthScopes.AGENT_READ))
    ],
    response_model_exclude_none=True,
)
async def check_record_access(
    request: Request, body: PipeshubRecordAccessQuery
) -> PipeshubRecordAccessAnswer:
    user = getattr(request.state, "user", None) or {}
    user_id, org_id = user.get("userId"), bound_org_id()
    if not user_id or not org_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    record_ids = [rid for rid in dict.fromkeys(body.record_ids or []) if rid]
    references = _distinct(body.references or [])
    artifact_ids = [aid for aid in dict.fromkeys(body.artifact_ids or []) if aid]
    if len(record_ids) + len(references) + len(artifact_ids) > MAX_RECORD_IDS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_RECORD_IDS} sources per call")
    asked: list[Source] = [PipeshubRecordSource(kind="indexed", record_id=rid) for rid in record_ids]
    graph_provider = await request.app.container.graph_provider()
    actor = Actor(org_id=org_id, user_id=user_id)
    config_service = getattr(request.app.container, "config_service", None)
    publications = KnowledgePublications(graph_provider, config_service() if config_service else None)
    statuses = await TieredRecordAuthorizer(graph_provider).decide(
        actor, [*asked, *references], publications=publications
    )
    artifacts = (
        await artifact_viewer_access(graph_provider).decide(actor, artifact_ids)
        if body.artifact_ids is not None
        else None
    )
    decided = list(zip([*asked, *references], statuses, strict=True))
    readable = sorted(
        {s.record_id for s, status in decided if isinstance(s, PipeshubRecordSource) and status == "readable"}
    )
    counts = Counter(status for _, status in decided)
    logger.info(
        "records.access_checked requested=%d readable=%d denied=%d unresolved=%d live=%d",
        len(decided),
        counts["readable"],
        counts["denied"],
        counts["unresolved"],
        sum(isinstance(s, PipeshubLiveSource) for s, _ in decided),
    )
    return PipeshubRecordAccessAnswer(
        readable=readable,
        decisions=(
            [PipeshubSourceDecision(source=s, status=status) for s, status in decided[len(asked):]]
            if body.references is not None
            else None
        ),
        artifacts=(
            [PipeshubArtifactDecision(artifact_id=aid, status=status) for aid, status in artifacts.items()]
            if artifacts is not None
            else None
        ),
    )


def _distinct(references: list[Source]) -> list[Source]:
    seen: dict[str, Source] = {}
    for reference in references:
        seen.setdefault(reference.model_dump_json(), reference)
    return list(seen.values())
