"""The synchronous client of ``/table-structure`` for the parse worker (emit mode).

Same contract as the plain async client: one attempt per request, no retry, no loop on a 429, and nothing reported to the
resource governor's downstream feedback, the backpressure coordinator or the parse-class limiter. The worker calls it from
its own thread, so it uses ``httpx.Client``. An error text never carries the URL or any header.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import ValidationError

from app.modules.parsers.pdf.table_hybrid_client import (
    CONNECT_TIMEOUT_S,
    BatchOutcome,
    _error_code,
    service_url,
)
from app.services.docling.table_structure_schema import DEADLINE_HEADER, TableResponse


class SyncTableClient:
    def __init__(self, base_url: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = (base_url or service_url()).rstrip("/")
        self._transport = transport

    def post_batch(self, meta: dict[str, Any], files: dict[str, bytes], *, deadline_ms: int, timeout_s: float) -> BatchOutcome:
        timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
        end = time.monotonic() + timeout_s
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as http, http.stream(
                "POST",
                f"{self._base_url}/table-structure",
                data={"meta": json.dumps(meta, separators=(",", ":"))},
                files={name: (f"{name}.png", data, "image/png") for name, data in files.items()},
                headers={DEADLINE_HEADER: str(max(0, int(deadline_ms)))},
            ) as streamed:
                # httpx bounds each read, not the whole answer: the worker's wait ends at timeout_s however the answer
                # trickles in. shortcut: the upload is bounded per write only; a server that reads the request slowly
                # can hold it longer (connect is capped at CONNECT_TIMEOUT_S).
                body = bytearray()
                for chunk in streamed.iter_bytes():
                    body += chunk
                    if time.monotonic() > end:
                        return BatchOutcome("timeout")
                # the body is decoded already: the answer is read from it, by its status and JSON only
                resp = httpx.Response(streamed.status_code, content=bytes(body))
        except httpx.ConnectError:
            return BatchOutcome("service_down")
        except httpx.TimeoutException:
            return BatchOutcome("timeout")
        except httpx.HTTPError:
            return BatchOutcome("error")
        code = resp.status_code
        if code == 429:
            return BatchOutcome("service_busy", status_code=code)
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
