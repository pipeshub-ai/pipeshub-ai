"""Node's inline claim hint (P1a S5, D-4): ids in, a claim through Node, the run in-process.

Authenticated with the per-deployment worker credential (the one this
service presents when it claims), not a user or service JWT, so the query
app's JWT middleware skips this prefix. The credential is a dependency of the
router itself: every route added here is guarded, and the guard runs before
the body is validated (C-26).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.harness.inline import InlineClaimer

_ID = r"^[A-Za-z0-9._:-]{1,128}$"


class ClaimHint(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    org_id: str = Field(alias="orgId", pattern=_ID)


def inline_claimer(request: Request) -> InlineClaimer:
    claimer = getattr(request.app.state, "harness_inline", None)
    if claimer is None:
        from app.harness.inline import InlineClaimer

        claimer = InlineClaimer(request.app.container)
        request.app.state.harness_inline = claimer
    return claimer


def _bearer(request: Request) -> str | None:
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def require_worker_credential(request: Request) -> None:
    if not await inline_claimer(request).credentials.accepts(_bearer(request)):
        run_id = request.path_params.get("run_id")
        HarnessLog.named("app.harness.inline", runId=run_id if isinstance(run_id, str) else None).warning(
            "inline.hint_refused", reason="worker_credential_mismatch"
        )
        raise HTTPException(status_code=401, detail="worker credential required")


router = APIRouter(dependencies=[Depends(require_worker_credential)])


@router.post("/runs/{run_id}/claim", status_code=202)
async def claim_hint(
    request: Request, body: ClaimHint, run_id: str = Path(pattern=_ID)
) -> JSONResponse:
    claimer = inline_claimer(request)
    log = HarnessLog.named("app.harness.inline", orgId=body.org_id, runId=run_id)
    claimed = await claimer.hint(body.org_id, run_id)
    log.info("inline.hint", claimed=claimed)
    return JSONResponse(status_code=202, content={"claimed": claimed})
