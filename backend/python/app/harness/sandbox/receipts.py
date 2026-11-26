"""``ExecutionReceipts`` over ``ArtifactRegistryService`` (P5.11).

One STAGING document per call, in the run's conversation, named by the call's
effect tag so no org, run or invocation id is stored in clear. The registry is
already where ``run_code`` keeps what it produced (its program, its outputs):
tenant-scoped, durable across workers, hidden from the user at STAGING, and
erased with the session's artifacts. Each state is a new version of the same
document.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from app.models.entities import ArtifactType, ArtifactVisibility
from harness_core.effects.receipts import ExecutionReceipt, check_forward
from harness_core.effects.reconcile import effect_tag

if TYPE_CHECKING:
    from app.services.artifact_registry.models import Actor

RECEIPT_TOOL = "harness/execution_receipt"
_MIME = "application/json"


def receipt_name(key: str) -> str:
    return f"receipt-{effect_tag(key)}.json"


def _encode(receipt: ExecutionReceipt) -> bytes:
    return json.dumps(
        {
            "state": receipt.state,
            "startedAt": receipt.started_at.isoformat(),
            "content": receipt.content,
            "isError": receipt.is_error,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode(raw: bytes) -> ExecutionReceipt:
    body = cast("dict[str, Any]", json.loads(raw))
    return ExecutionReceipt(
        state=body["state"],
        started_at=datetime.fromisoformat(body["startedAt"]),
        content=body.get("content"),
        is_error=bool(body.get("isError", False)),
    )


class RegistryExecutionReceipts:
    def __init__(self, registry: Any, actor: Actor, conversation_id: str) -> None:  # noqa: ANN401 - ArtifactRegistryService
        self._registry = registry
        self._actor = actor
        self._conversation_id = conversation_id

    async def get(self, key: str) -> ExecutionReceipt | None:
        found = await self._registry.resolve_by_logical_name(
            self._actor, receipt_name(key), conversation_id=self._conversation_id
        )
        if found is None:
            return None
        raw = await self._registry.get_content(
            actor=self._actor, artifact_id=found.artifact_id, version=found.version
        )
        return _decode(raw)

    async def put(self, key: str, receipt: ExecutionReceipt) -> None:
        check_forward(await self.get(key), receipt)
        await self._registry.register_output(
            actor=self._actor,
            name=receipt_name(key),
            artifact_type=ArtifactType.DATA_FILE,
            mime_type=_MIME,
            content=_encode(receipt),
            conversation_id=self._conversation_id,
            source_tool=RECEIPT_TOOL,
            visibility=ArtifactVisibility.STAGING,
        )


__all__ = ["RECEIPT_TOOL", "RegistryExecutionReceipts", "receipt_name"]
