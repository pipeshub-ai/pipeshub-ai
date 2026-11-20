"""A plain client of the Docling service's ``/table-structure`` endpoint.

It is deliberately not a ``BaseServiceClient``: it makes one attempt per batch, never retries and never loops on a 429,
and it never reports to the resource governor's downstream feedback, the backpressure coordinator or the parse-class
limiter, so an outage or a busy service here cannot shrink the index pools or pause record consumers.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from app.services.docling.table_structure_schema import DEADLINE_HEADER, TableResponse

UNSUPPORTED_LATCH_S = 300.0  # an old service (404) is not asked again for this long
DOWN_LATCH_S = 30.0  # after a refused connection no document starts shadow work for this long
CONNECT_TIMEOUT_S = 5.0


@dataclass
class BatchOutcome:
    """One batch's result. ``kind`` is ``ok`` or the reason every table of the batch carries."""

    kind: str  # ok | service_busy | deadline | invalid_input | unsupported | mode_fast | service_down | timeout | error
    response: TableResponse | None = None
    status_code: int | None = None


def service_url() -> str:
    return os.getenv("DOCLING_SERVICE_URL", "http://localhost:8081").rstrip("/")


class TableStructureClient:
    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = (base_url or service_url()).rstrip("/")
        self._transport = transport

    async def post_batch(
        self, meta: dict[str, Any], files: dict[str, bytes], *, deadline_ms: int, timeout_s: float
    ) -> BatchOutcome:
        """Send one batch. No retry: every failure is an outcome."""
        timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as http:
                resp = await http.post(
                    f"{self._base_url}/table-structure",
                    data={"meta": json.dumps(meta, separators=(",", ":"))},
                    files={name: (f"{name}.png", data, "image/png") for name, data in files.items()},
                    headers={DEADLINE_HEADER: str(max(0, int(deadline_ms)))},
                )
        except httpx.ConnectError:
            return BatchOutcome("service_down")
        except httpx.TimeoutException:
            return BatchOutcome("timeout")
        except httpx.HTTPError:
            return BatchOutcome("error")
        code = resp.status_code
        if code == 429:
            return BatchOutcome("deadline" if _error_code(resp) == "deadline" else "service_busy", status_code=code)
        if code == 422:
            return BatchOutcome("invalid_input", status_code=code)
        if code in (404, 405):
            return BatchOutcome("unsupported", status_code=code)
        if code == 409:
            return BatchOutcome("mode_fast" if _error_code(resp) == "mode_not_supported" else "unsupported", status_code=code)
        if code != 200:
            return BatchOutcome("error", status_code=code)
        try:
            return BatchOutcome("ok", TableResponse.model_validate(resp.json()), code)
        except (ValueError, ValidationError):
            return BatchOutcome("error", status_code=code)


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return body.get("errorCode") if isinstance(body, dict) else None
