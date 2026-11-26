"""What a remote model call spent for a stage job, kept in Redis under the caller's request id.

A caller (the indexing service) asks another service (Extraction) to make model calls for one of its stage jobs. The
reply carries what those calls spent (``JobTrace.usage``), but a reply can be lost after the work was done, and an HTTP
client may then send the same request again. So the caller names each logical request (``request_id``, the same across
its transport retries) and the service:

* claims the id before it spends anything (``claim``): a second delivery of the same request is refused without
  spending, so a lost reply is never paid for twice;
* records the request's usage under the id when it finishes, whatever the outcome (``record``).

A caller that did not get a reply reads the record back (``read``). When there is none (the service is still running it,
or died), the caller charges the whole allowance it passed: conservative, since the service never spends more.

The keys share the stage job's hash tag (``{<state key>}``), live a day and hold counts only, never content.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.services.llm_gateway.fleet_redis import get_fleet_redis

logger = logging.getLogger(__name__)

USAGE_TTL_SECONDS = 24 * 3600
REDIS_OP_TIMEOUT_S = 2.0
_RUNNING = "running"
MAX_REQUEST_ID = 64


def _key(job_key: str, request_id: str) -> str | None:
    handle = get_fleet_redis()
    if handle is None or not job_key or not request_id or len(request_id) > MAX_REQUEST_ID:
        return None
    return handle.key(f"messaging:pause:{{{job_key}}}:remote:{request_id}")


async def claim(job_key: str, request_id: str) -> bool | dict[str, Any] | None:
    """Claim ``request_id`` for its first delivery. True: this delivery may spend. Otherwise the request was delivered
    before: its recorded usage (a dict), or None while that delivery has not finished. Without a bound Redis or a
    request id, every delivery may spend (True)."""
    key = _key(job_key, request_id)
    if key is None:
        return True
    client = get_fleet_redis().client()  # type: ignore[union-attr]
    if await asyncio.wait_for(client.set(key, _RUNNING, nx=True, ex=USAGE_TTL_SECONDS), REDIS_OP_TIMEOUT_S):
        return True
    return await read(job_key, request_id)


async def record(job_key: str, request_id: str, usage: dict[str, Any] | None) -> None:
    """Record what the request spent, for a caller that lost the reply. Best effort: a caller that finds nothing
    charges the whole allowance it passed."""
    key = _key(job_key, request_id)
    if key is None:
        return
    try:
        await asyncio.wait_for(
            get_fleet_redis().client().set(key, json.dumps(usage or {}), ex=USAGE_TTL_SECONDS),  # type: ignore[union-attr]
            REDIS_OP_TIMEOUT_S,
        )
    except Exception:
        logger.warning("could not record the usage of a remote model request", exc_info=True)


async def read(job_key: str, request_id: str) -> dict[str, Any] | None:
    """The usage recorded for ``request_id``, or None (not finished, not recorded, or unreadable)."""
    key = _key(job_key, request_id)
    if key is None:
        return None
    try:
        raw = await asyncio.wait_for(get_fleet_redis().client().get(key), REDIS_OP_TIMEOUT_S)  # type: ignore[union-attr]
    except Exception:
        return None
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    if text == _RUNNING:
        return None
    try:
        usage = json.loads(text)
    except ValueError:
        return None
    return usage if isinstance(usage, dict) else None
