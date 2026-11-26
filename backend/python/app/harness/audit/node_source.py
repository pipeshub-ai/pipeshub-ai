"""``RunEvidenceSource`` over Node's ``GET /internal/runs/{runId}/audit`` (worker credential; ids and counts only)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import quote

from app.harness.journal_node.client import INTERNAL_PREFIX
from app.harness.journal_node.http import NodeServiceClient
from harness_core.audit.types import (
    ExecutedCall,
    JournalUsageRecord,
    LedgerAccountView,
    PinnedGrant,
    RunEvidence,
)
from harness_core.budget.types import Amount, LedgerUsage

if TYPE_CHECKING:
    import httpx
    from pydantic import JsonValue


class WorkerCredentialSource(Protocol):
    async def worker_credential(self) -> str: ...


class AuditSourceUnavailable(Exception):
    reason_code = "audit_source_unavailable"


class NodeRunEvidenceSource:
    def __init__(
        self,
        *,
        base_url: str,
        credentials: WorkerCredentialSource,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._credentials = credentials
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.audit"
        )

    async def aclose(self) -> None:
        await self._node.aclose()

    async def evidence(self, org_id: str, run_id: str) -> RunEvidence | None:
        path = f"{INTERNAL_PREFIX}/runs/{quote(run_id, safe='')}/audit"
        response = await self._node.send(
            "GET",
            path,
            bearer=await self._credentials.worker_credential(),
            params={"orgId": org_id},
            unreachable=lambda exc: AuditSourceUnavailable(f"Node unreachable: {type(exc).__name__}"),
        )
        if response.status_code in (400, 404):
            # 400: an id Node's schema refuses names no run it holds (e.g. a foreign org id).
            return None
        if response.status_code != 200:
            raise AuditSourceUnavailable(f"Node answered {response.status_code}")
        return _evidence(cast("dict[str, JsonValue]", response.json()), org_id, run_id)


def _strings(value: object) -> frozenset[str]:
    return frozenset(str(v) for v in cast("list[object]", value)) if isinstance(value, list) else frozenset()


def _count(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usage(item: dict[str, object]) -> JournalUsageRecord:
    included = item.get("inputIncludesCached")
    transport = item.get("transport")
    return JournalUsageRecord(
        type=str(item.get("type", "")),
        input_tokens=_count(item, "inputTokens"),
        output_tokens=_count(item, "outputTokens"),
        cache_read_tokens=_count(item, "cacheReadTokens"),
        cache_write_tokens=_count(item, "cacheWriteTokens"),
        input_includes_cached=included if isinstance(included, bool) else None,
        provider=str(item.get("provider", "")),
        transport=transport if isinstance(transport, str) else None,
    )


def _ledger(raw: object) -> LedgerAccountView | None:
    if not isinstance(raw, dict):
        return None
    body = cast("dict[str, object]", raw)
    used = body.get("used")
    reserved = cast("dict[str, object]", body.get("reserved")) if isinstance(body.get("reserved"), dict) else {}
    counts = cast("dict[str, object]", used) if isinstance(used, dict) else None
    return LedgerAccountView(
        used=None
        if counts is None
        else LedgerUsage(
            _count(counts, "inputTokens"),
            _count(counts, "outputTokens"),
            _count(counts, "cacheReadTokens"),
            _count(counts, "cacheWriteTokens"),
        ),
        reserved=Amount(_count(reserved, "usdMicros"), _count(reserved, "tokens")),
    )


def _evidence(body: dict[str, JsonValue], org_id: str, run_id: str) -> RunEvidence:
    intents = body.get("intents")
    calls = tuple(
        ExecutedCall(
            "effect_log",
            org_id,
            run_id,
            str(item.get("toolId", "")),
            str(item["attemptId"]) if item.get("attemptId") else None,
            str(item.get("invocationId", "")) or None,
        )
        for item in (cast("list[dict[str, object]]", intents) if isinstance(intents, list) else [])
    )
    usage = body.get("usage")
    return RunEvidence(
        PinnedGrant(_strings(body.get("toolIds")), _strings(body.get("routeIds"))),
        calls,
        ended=body.get("ended") is True,
        usage=tuple(_usage(item) for item in cast("list[dict[str, object]]", usage if isinstance(usage, list) else [])),
        ledger=_ledger(body.get("ledger")),
    )


__all__ = ["AuditSourceUnavailable", "NodeRunEvidenceSource", "WorkerCredentialSource"]
