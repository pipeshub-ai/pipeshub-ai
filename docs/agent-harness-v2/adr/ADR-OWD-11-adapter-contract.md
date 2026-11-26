# ADR-OWD-11 — Harness adapter contract, without an in-sandbox daemon

## Context

[OWD-11](../one-way-doors.md) fixes the adapter interface (capabilities, lifecycle verbs, event mapping) and the protocol between an in-sandbox `sandboxd` and the orchestrator. Every adapter and the conformance suite depend on it.

## Decision

Adopt OWD-11's adapter contract, with the supervisor outside the sandbox:

- Normalize on ACP semantics, and use native protocols where ACP loses fidelity (Claude Code stream-json, Codex app-server). Capabilities are declared explicitly and the UI and policy degrade when one is missing. Adapter plugins stay internal: ACP profiles are signed release data, not a plugin API.
- **No in-sandbox `sandboxd`.** Accepted divergence. The worker's `EngineController` supervises each engine from outside the sandbox, over the broker's exec stdio or a tunnelled port. Nothing in the sandbox is trusted to report on itself, there is one less component to ship in every image, and the crash and conformance suites pass on it.
- **C2.29 (local ring buffer, replay from `ack_seq`), ADR half.** Decided: no in-sandbox buffer. If the worker-to-sandbox link drops, the turn ends and is **re-driven from the last checkpoint** instead of re-attached. Replay to clients is one layer up: journal `seq` plus the LiveBus. Proven by the re-drive and replay tests below, and by the CH-12 partition row (WP-20).

## As built

Audited at `959c3bda72`. Decision rows: [ARCHITECTURE.md](../ARCHITECTURE.md) §4.1; [ENGINES-PLAN.md](../ENGINES-PLAN.md); ACP driver; C1.05 (control channel: broker exec stdio or a tunnelled port).

- Drivers: `backend/python/harness_engines/` (`codex`, `claude_code`, `opencode`, `acp`). Capabilities: `EngineCapabilities` in `backend/python/harness_core/engines/types.py`. ACP profiles: `backend/python/harness_engines/acp_agents.json`.
- Passing in the gate:
  - `backend/python/tests/unit/harness_engines/acp/test_conformance.py` (`TestAcpConformance`)
  - `backend/python/tests/unit/harness/recs_audit/test_r2_r6_isolation_and_protocols.py::test_r6_each_released_engine_is_driven_through_its_native_protocol` and `::test_r6_the_drive_each_adapter_declares_matches_its_protocol`
  - Real binaries (docker.xml): `test_real_claude_code_conformance.py` (42), `test_real_codex_conformance.py` (41), `test_real_conformance.py` (opencode, 30), `test_real_acp_conformance.py` (30), all in `backend/python/tests/unit/harness/engines/`
  - C2.29: `backend/python/tests/unit/harness_core/engines/durable/test_lcfix_interrupted.py::test_lcfix_a_turn_whose_engine_vanished_is_redriven_from_the_revision_before_it`; `backend/python/tests/unit/harness_core/engines/durable/test_crash_points.py::test_ek14_a_worker_killed_at_each_boundary_resumes_without_duplicates`; `backend/nodejs/apps/tests/modules/agent-harness/projection/run-projector.test.ts` "L-2: after an expired-buffer restart, resuming from any id already sent yields exactly the rest"

## Deviations

- No `sandboxd`, and re-drive instead of re-attach: accepted above.
- CH-12: the worker-to-sandbox link silently partitioned for 2 minutes mid-turn (FM-11, `backend/python/tests/unit/harness/engines/test_p9_fault_matrix.py::test_fm11_ch12_worker_sandbox_partition`, WP-20): the attempt is released, the turn re-driven, the effect runs once, no sandbox is left. The lost link is now `BrokerUnavailable` and the attempt waits `sandbox_wait` (FB-9, fixed in the broker client and the adapters' failed-open cleanup; FM-11's docker rerun pending).
- The build's own conformance cases are numbered CF1–CF20 and do not match the spec's CF-01..CF-20 one to one. The spec's timed CF-09, CF-13 and CF-20 land in WP-04.

## Owner amendments

None. ACP is part of this build.

## Status

**Accepted** (2026-10-26). Built and tested. C2.29 is decided here and its CH-12 partition test passes (WP-20).
