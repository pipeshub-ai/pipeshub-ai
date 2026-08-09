"""RedisWorkflowStateStore: IWorkflowStateStore over a Redis hash per workflow.

One hash per (org_id, workflow_id) keeps a workflow's whole state readable and
deletable in one operation, and keeps `ctx.state.get` a single HGET rather than
a key scan.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.services.workflows.adapters.redis import keys as k

if TYPE_CHECKING:
    from redis.asyncio import Redis

__all__ = ["RedisWorkflowStateStore"]

logger = logging.getLogger(__name__)


class RedisWorkflowStateStore:
    def __init__(self, redis_client: "Redis") -> None:
        self._redis = redis_client

    async def get(self, *, org_id: str, workflow_id: str, key: str) -> Any:
        raw = await self._redis.hget(k.workflow_state_key(org_id, workflow_id), key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "workflow state: corrupt value at %s/%s/%s — treating as unset",
                org_id, workflow_id, key,
            )
            return None

    async def set(self, *, org_id: str, workflow_id: str, key: str, value: Any) -> None:
        await self._redis.hset(
            k.workflow_state_key(org_id, workflow_id), key, json.dumps(value),
        )
