"""Internal on-demand seed route for the org-wide indexing progress bar.

Hosted on the query service (always up, owns the graph abstraction). The Node
ticker calls this when an admin opens the widget and Redis has no counts for the
org yet — so the bar survives a full indexing-service outage. Idempotent: it just
runs the light count query and overwrites the org's Redis counters to DB truth.
"""

from fastapi import APIRouter, Request  # type: ignore
from pydantic import BaseModel

from app.services.progress.progress_counter import get_progress_counter

router = APIRouter()


class SeedRequest(BaseModel):
    orgId: str


@router.post("/progress/seed")
async def seed_progress(body: SeedRequest, request: Request) -> dict:
    counter = get_progress_counter()
    graph_provider = getattr(request.app.state, "graph_provider", None)
    if counter is None or graph_provider is None:
        return {"seeded": False, "reason": "progress counter unavailable"}
    try:
        rows = await graph_provider.get_indexing_status_counts(body.orgId)
        await counter.seed_org(body.orgId, rows)
        return {"seeded": True, "orgId": body.orgId, "rows": len(rows)}
    except Exception as e:  # noqa: BLE001 - never surface internals; bar is best-effort
        return {"seeded": False, "reason": str(e)}
