# ADR-OWD-10 — Where the runtime lives and which store owns what

## Context

[OWD-10](../one-way-doors.md) fixes which service hosts runs, gateways and the event store. Ports, Compose and Helm files, network policies, auth paths and OpenAPI paths all get published, and moving the Tool Gateway later changes its auth and network topology.

## Decision

Adopt OWD-10's placement, with leases in Mongo:

- **`app.harness_main`** (Python) hosts the run worker, the per-run Tool Gateway, policy and approvals.
- **Node is the public edge**: auth, rate limits and quotas, admission, the journal API, and every public route, documented in `pipeshub-openapi.yaml`.
- **The Tool Gateway and the Model Gateway are internal-only.** Sandboxes reach them solely through the egress proxy. The Model Gateway is its own process (`app.model_gateway_main`).
- **Stores:** Mongo for runs, journal and claims; blob for payloads, checkpoints and snapshots; graph only for permission edges; MessagingFactory for wake-ups.
- **Leases and claims live in Mongo, not in KV.** Accepted divergence: the claim *is* the queue (REPORT D20), so the lease, its attempt fence and the run's state commit in one transaction; a KV lease would need a second store kept in step with it. The broker message is only a wake hint.
- **C2.19 (cluster-wide governor), ADR half.** Decided: per-org and per-actor active-run and admission-rate quotas, enforced at admission in the shared Mongo store, plus the sandbox broker's capacity probe that counts every sandbox of the namespace whichever broker made it, are the cluster-wide governor for harness runs. No separate KV governor is added.
  - **Lead's check (2026-10-30).** Two parts of that were not so. (1) The broker's capacity probe only sized the warm pool; a cold create on a full host was never refused, so nothing capped sandboxes across orgs. (2) The per-process `SandboxResourceGovernor` is not legacy-only: a durable native run pinned to `agent-code` gets `run_code` through `app/harness/assembly/tool_sources.py` `SandboxSource` → `build_coding_sandbox_manager`, which still takes the per-process governor.
  - **Fix.** The broker now enforces the install's ceiling, `SANDBOX_MAX_TOTAL` (default 50, the legacy governor's variable and default; 0 is none): every live sandbox of its namespace counts, whichever broker process made it, plus creates in flight in this process (`harness_core/sandbox/capacity.py` `SandboxCap`). A create at the ceiling raises `BrokerUnavailable`, which the engine kernel waits out as capacity (QA31F), never a crash. Engine sandboxes and, with `SANDBOX_MODE=broker` (the Compose default), native `run_code` sandboxes both go through it. Per org and per user stay the admission quotas. The per-process governor remains a second, stricter bound only on the legacy backends (`docker`, `e2b`, `local`) and goes with them. Across several broker processes the count is read, not reserved, so two brokers can overshoot by their in-flight creates; Compose runs one.

## As built

Audited at `959c3bda72`. Decision rows: the v2 architecture report §5.3 placement, D16, D20; [ARCHITECTURE.md](../ARCHITECTURE.md) §7. The process and port table is in [AGENTS.md](../../../AGENTS.md) "What lives where".

- Entry points: `backend/python/app/harness_main.py`, `backend/python/app/model_gateway_main.py`, `backend/python/app/egress_proxy_main.py`, `backend/python/app/sandbox_broker_main.py`. Compose aliases `tool-gateway`, `model-gateway` and `harness-state` exist only on the internal `sandbox_plane` network (`deployment/docker-compose/docker-compose.yml`).
- Leases: `backend/nodejs/apps/src/modules/agent-harness/domain/execution.ts`. Quotas: `backend/nodejs/apps/src/modules/agent-harness/domain/admission-quotas.ts`.
- Passing in the gate:
  - `backend/nodejs/apps/tests/cohesion/openapi-harness-routes.test.ts` "C-10: agent-harness routes are in the OpenAPI spec"
  - `backend/python/tests/unit/harness/test_node_journal_contract.py::test_stale_fence_is_rejected_after_a_reclaim` (Mongo lease fence)
  - `backend/nodejs/apps/tests/modules/agent-harness/support/execution-store-contract/claims.ts` "records how each claim got the run: queue, expired-lease takeover, or queue after a sweep"
  - C2.19: `backend/nodejs/apps/tests/modules/agent-harness/support/execution-store-contract/admission.ts` "P-5: the org quota counts every actor of the org" and "P-12: concurrent admissions at the boundary admit exactly the quota"; `backend/python/tests/unit/harness_core/sandbox/test_c2_19_sandbox_cap.py` (SBX-21: twenty parallel creates at the boundary admit exactly the room left); `backend/python/tests/unit/harness/sandbox_broker/test_docker_broker.py::test_c2_19_the_install_ceiling_counts_every_live_sandbox_and_frees_on_release` (docker.xml: a second broker counts the first one's sandbox); `backend/python/tests/unit/harness/sandbox_broker/test_broker_process.py::test_c2_19_the_install_sandbox_ceiling`
  - `backend/python/tests/unit/harness/engines/test_recs_audit_real_engine.py::test_r1_a_real_engine_mid_turn_holds_no_secret_in_its_container_processes_or_files` (the sandbox reaches only the gateways)

## Deviations

- Leases in Mongo, not KV: accepted above.
- Public routes are not under `/api/v1/harness/...`: amended, see [ADR-OWD-13](ADR-OWD-13-public-api-naming.md).

## Owner amendments

None to OWD-10. Route naming follows the OWD-13 amendment.

## Status

**Accepted** (2026-10-26; C2.19 corrected and accepted 2026-10-30). Built and tested. C2.19 is decided here; its tests pass.
