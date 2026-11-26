"""``RefusalSink`` over Node (SEC2 O-2, OB-03): a gateway's decision on a run's request, recorded on the run's journal.

``POST /api/v1/agent-harness/internal/runs/{runId}/refusals`` with the run's own gateway token. Node verifies
the token is this run's, appends ``gateway.refused`` once per run, gateway, reason and host, and at most a fixed
number per run; it answers the outcome (``recorded``, ``duplicate``, ``capped``).
``POST …/runs/{runId}/egress`` the same way: ``egress.allowed`` once per run, host and route.
``POST …/runs/{runId}/egress-volume`` the same way: ``egress.volume_alert`` once per run, kind and direction (C6.12).
``POST …/runs/{runId}/secret-hidden`` the same way: ``gateway.secret_hidden`` once per run, gateway and kind (WP-103).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from app.harness.journal_node.client import INTERNAL_PREFIX
from app.harness.journal_node.http import NodeServiceClient
from harness_core.gateway.refusal_reports import (
    EgressAllowance,
    EgressVolumeAlert,
    ModelFallbackReport,
    RecordOutcome,
    RefusalNotAccepted,
    RefusalReport,
    SecretHidden,
    SiteReport,
)

if TYPE_CHECKING:
    import httpx

_OUTCOMES: frozenset[str] = frozenset({"recorded", "duplicate", "capped"})


def _site(site: SiteReport) -> dict[str, object]:
    return {"path": site.path, "access": site.access, "use": site.use}


class NodeRefusalSink:
    def __init__(self, *, base_url: str, http_client: httpx.AsyncClient | None = None, timeout_s: float = 5.0) -> None:
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.refusals"
        )

    async def record(self, run_id: str, token: str, report: RefusalReport) -> RecordOutcome:
        body: dict[str, object] = {"gateway": report.gateway, "reason": report.reason, "status": report.status}
        if report.host is not None:
            body["host"] = report.host
        if report.param is not None:
            body["param"] = report.param
        if report.site is not None:
            body["site"] = _site(report.site)
        return await self._send(run_id, "refusals", token, body)

    async def allowed(self, run_id: str, token: str, allowance: EgressAllowance) -> RecordOutcome:
        body: dict[str, object] = {"host": allowance.host, "via": allowance.via}
        if allowance.site is not None:
            body["site"] = _site(allowance.site)
        return await self._send(run_id, "egress", token, body)

    async def volume(self, run_id: str, token: str, alert: EgressVolumeAlert) -> RecordOutcome:
        body: dict[str, object] = {
            "kind": alert.kind,
            "direction": alert.direction,
            "bytes": alert.bytes,
            "thresholdBytes": alert.threshold_bytes,
            "alias": alert.alias,
        }
        if alert.baseline_bytes is not None:
            body["baselineBytes"] = alert.baseline_bytes
        return await self._send(run_id, "egress-volume", token, body)

    async def fallback(self, run_id: str, token: str, report: ModelFallbackReport) -> RecordOutcome:
        body: dict[str, object] = {
            "from": {"provider": report.from_provider, "model": report.from_model},
            "to": {"provider": report.to_provider, "model": report.to_model},
            "reason": report.reason,
        }
        return await self._send(run_id, "model-fallback", token, body)

    async def hidden(self, run_id: str, token: str, report: SecretHidden) -> RecordOutcome:
        return await self._send(run_id, "secret-hidden", token, {"gateway": report.gateway, "kind": report.kind})

    async def _send(self, run_id: str, route: str, token: str, body: dict[str, object]) -> RecordOutcome:
        response = await self._node.send(
            "POST", f"{INTERNAL_PREFIX}/runs/{quote(run_id, safe='')}/{route}", bearer=token, json=body
        )
        # 409: the token's attempt no longer holds the run (SEC-C-11); retrying cannot change that.
        if response.status_code in (400, 401, 403, 404, 409):
            raise RefusalNotAccepted(f"Node refused the report ({response.status_code})")
        _ = response.raise_for_status()
        answer = response.json()
        outcome = answer.get("outcome") if isinstance(answer, dict) else None
        if outcome not in _OUTCOMES:
            raise RefusalNotAccepted("Node answered no outcome")
        return cast("RecordOutcome", outcome)

    async def aclose(self) -> None:
        await self._node.aclose()


__all__ = ["NodeRefusalSink"]
