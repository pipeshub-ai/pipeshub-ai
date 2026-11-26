# Agent Harness Runtime — Readiness Checklist

This checklist is how we track whether the harness runtime is complete, robust, production-ready and highly capable. Each item can be verified. Where a test covers an item, its ID points into [test-scenarios.md](./test-scenarios.md). Design terms come from [README.md](./README.md), and the decisions referred to here are in [one-way-doors.md](./one-way-doors.md).

**Priority tags**

| Tag | Meaning |
| --- | --- |
| **P0** | Required for MVP |
| **P1** | Required for v1 / general availability |
| **P2** | Hardening, and v2 work |

**Scope tags** (used in §4)

| Tag | Scope |
| --- | --- |
| **G** | Global (operator / deployment) |
| **O** | Org (admin) |
| **A** | Agent (builder) |
| **H** | Harness profile |
| **S** | Session |
| **U** | User preference |

**How to use it.**
- Copy the checklist into the tracking epic.
- An item counts as done only when its linked tests pass in CI on the release matrix.
- Any item marked "decision" needs a merged ADR before work on it starts.

---

## 1. Foundational decisions (gate everything else)

- [x] **P0** Merge an ADR for each one-way door: OWD-1 through OWD-13. — ✅ [adr/](./adr/) ADR-OWD-01…13; `backend/python/tests/cohesion/test_v2_spec_trackers.py`
- [ ] **P0** Choose the name of the subsystem. Use "Harness Runtime" / `app.harness_main`; do not reuse `agent_loop_lib.control_plane.ControlPlane`.
- [ ] **P0** Run the Phase 0 spike: Claude Code, Codex and OpenCode each run headless in Docker + gVisor against stub gateways.
- [ ] **P0** Benchmark sandbox runtimes. Compare gVisor, runc and Kata on `git clone`, `npm ci` and `pytest`, and record the default runtime chosen for each deployment target (PF-09).
- [ ] **P0** Choose the control channel: provider attach/exec stdio, or a WebSocket initiated by the daemon through the proxy.
- [ ] **P0** Decide whether to build or adopt the Model Gateway (LiteLLM proxy vs `agent_loop_lib/transport/*`). Either way it must sit behind the OWD-8 contract.
- [ ] **P0** Decide the durable-execution approach: a home-grown state machine (Mongo + KV leases + MessagingFactory) or Temporal/DBOS, hidden behind the orchestrator interface.
- [ ] **P1** Complete a license and terms-of-service review for each harness: automated use, use inside customer infrastructure, and use with org-owned keys.

---

## 2. Control plane

### 2.1 Session orchestrator

- [ ] **P0** Sessions are durable resources, independent of any HTTP request (OWD-5).
- [x] **P0** Implement the full state machine. Every illegal transition is rejected, checked with a table-driven test (SL-05). — ✅ `tests/unit/harness_core/journal/test_sl05_run_state_machine.py`, `tests/modules/agent-harness/sl05-run-state-machine.test.ts`
- [ ] **P0** Each session has exactly one owner, enforced by a KV lease. Another replica takes over within the lease TTL, and there is no split-brain (SL-15, CH-02).
- [ ] **P0** Commands are idempotent: create, send_message (keyed by client message id), interrupt, approve and terminate (SL-04, CH-17).
- [x] **P0** Interrupt escalates SIGINT → SIGTERM → destroy, with a 2 s stop and a 10 s kill (CF-09). — ✅ `harness_core/testing/engines/contract.py` `spec_cf09_*`, `tests/unit/harness_core/engines/test_cf09_stopping.py`
- [ ] **P0** An orphan sweeper destroys sandboxes that have no live session (SL-16).
- [x] **P0** Every session pins `harness@version`, `image_digest`, `catalog_hash`, `policy_version` and the `/pipeshub` contract version (SL-14). — ✅ WP-05: `backend/nodejs/apps/tests/modules/agent-harness/c207-release-pin.test.ts`, `backend/python/tests/unit/harness_core/engines/test_c207_image_pins.py`, `backend/python/tests/unit/harness_core/engines/durable/test_c207_resume_pin.py`
- [ ] **P1** Steer works mid-turn where the harness supports it, and the message is queued otherwise. The UI says which happened (J-09, CF-10).
- [ ] **P1** Idle → suspend → resume works transparently (SL-08, SL-09).
- [ ] **P1** A checkpoint consists of: native session ref, transcript blob, workspace snapshot or volume, git commit and last acked `seq`.
- [ ] **P1** Crash recovery (CH-01):
  - restore from the checkpoint
  - add a resume note to the context
  - never re-execute tool calls that already completed
- [ ] **P1** A maximum session lifetime is enforced with a clean stop and an option to resume (SL-10).
- [ ] **P2** Fork: snapshot + transcript → a new session. Budgets and approvals are not inherited by default (J-10).

### 2.2 Sandbox provider layer

- [x] **P0** Define the `ISandboxProvider` interface with `SandboxProviderFactory`: create, attach, exec, put/get files, snapshot, restore, suspend/resume, destroy, health and capabilities. — ✅ WP-67: `harness_core/sandbox/provider.py` (`ISandboxProvider`, `BrokerSandboxProvider`, `SandboxProviderFactory` keyed by `SANDBOX_PROVIDER`; Kubernetes, managed and microVM refused by name). Contract `harness_core/testing/sandbox/provider_contract.py` (SP-1..SP-7 over SB-*): H `tests/unit/harness_core/sandbox/test_c214_sandbox_provider.py`, D `test_docker_broker_contract.py::TestDockerProvider`; callers `tests/unit/harness/test_c214_provider_wiring.py` (SPW-1..SPW-6)
- [ ] **P0** Build the Docker + gVisor provider for Compose, reusing `app/sandbox/docker_proxy.py` policy and `egress_firewall.py`.
- [ ] **P0** Plain runc is allowed only behind an explicit dev/insecure flag, and the UI shows a warning while it is on (OP-01).
- [ ] **P0** Each sandbox serves one session for one principal and is never reused (SEC-ISO-05).
- [x] **P0** Resource limits cover CPU, memory, pids, ephemeral storage and disk quota (SEC-ISO-06). — ✅ `backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_exhaustion_docker.py`, `backend/python/tests/unit/harness/sandbox_broker/test_wp14_workspace_quota.py` (WP-14)
- [x] **P0** A cluster-wide governor (KV-backed) replaces the per-process `SandboxResourceGovernor` caps, with limits per org, per user and globally. — ✅ [ADR-OWD-10](./adr/ADR-OWD-10-runtime-placement-and-stores.md): per org and per user, the Mongo admission quotas (P-5, P-12); globally, the broker's install ceiling `SANDBOX_MAX_TOTAL` counted across broker processes (`test_c2_19_sandbox_cap.py`, `test_docker_broker.py::test_c2_19_the_install_ceiling_counts_every_live_sandbox_and_frees_on_release`). Not KV: the run store is Mongo and the broker counts the daemon (ADR divergence)
- [x] **P0** Images are pinned by digest. Harness binaries are baked in at a pinned version and never fetched at run time. — ✅ WP-05: `backend/python/tests/unit/harness/sandbox_broker/test_c220_image_pin.py` (incl. the docker case), `backend/python/tests/unit/harness_core/engines/test_c207_image_pins.py`
- [ ] **P1** Build the Kubernetes provider on `kubernetes-sigs/agent-sandbox` with a configurable `RuntimeClass` (gVisor or Kata) (OP-03).
- [x] **P1** Warm pools are keyed by (template, harness version) and claimed with per-tenant admission control (PF-01, SEC-DOS-05). — ✅ WP-72: `backend/python/tests/unit/harness_core/sandbox/test_sec_dos_05_warm_claim_caps.py`, pool contract WP-12 (fake and Docker)
- [ ] **P1** Snapshot and restore are scoped to one principal, and restoring into a different principal is rejected (SEC-ISO-08).
- [ ] **P2** Build SaaS providers: E2B (also self-hostable), Daytona, Modal and Vercel, each with a capability matrix.
- [ ] **P2** Support GPU templates on providers that have them.

### 2.3 `sandboxd` (supervisor inside the sandbox)

- [ ] **P0** Runs unprivileged and holds no secrets.
- [ ] **P0** Launches and supervises the harness, and turns a crash into a typed error (CF-16).
- [ ] **P0** Converts native harness events into the canonical schema.
- [x] **P0** Keeps a local ring buffer with `seq` and supports replay from `ack_seq` (CH-02, CH-12). — ✅ decided in [ADR-OWD-11](./adr/ADR-OWD-11-adapter-contract.md) (re-drive, no in-sandbox buffer); CH-12 partition: `backend/python/tests/unit/harness/engines/test_p9_fault_matrix.py::test_fm11_ch12_worker_sandbox_partition` (WP-20)
- [ ] **P0** Sends heartbeats and health reports.
- [x] **P0** Rate-limits events so an event flood cannot hurt the control plane (SEC-DOS-02). — ✅ `backend/python/tests/unit/harness_core/engines/durable/test_wp03_kernel.py` (1M-event flood), `tests/unit/harness_core/engines/test_wp03_native_and_rate.py`
- [ ] **P1** Runs a PTY for a read-only terminal view, bounded and chunked (EV-09).
- [ ] **P1** Watches the file system and emits `file.diff` events (EV-08).
- [ ] **P1** Collects outputs from `/pipeshub/outputs` without following symlinks (SEC-FS-01).

### 2.4 Harness adapters

- [ ] **P0** Define the adapter contract: `capabilities`, `materialize_config`, `start`, `send`, `steer`, `interrupt`, `resume`, `fork`, the permission bridge, `parse` and `usage` (OWD-11).
- [ ] **P0** Claude Code adapter:
  - runs through the Agent SDK, or `-p --bare` with stream-json input and output
  - bridges permissions through `canUseTool` or the prompt tool
- [ ] **P0** Build a second adapter to prove the abstraction: Codex `app-server` or OpenCode `serve`.
- [ ] **P0** Every adapter passes the conformance suite CF-01 to CF-20, and must pass it before a harness version can be enabled. — ◐ CF-13 closed by WP-16 on every real binary (the turn's usage is the gateway ledger's, OWD-8); left: CF-18 under runsc (⏸ gVisor deferred), CF-12/CF-14 P2 (no adapter declares them). CF-17 ✅ (WP-67): `spec_cf17` on every adapter, the real binaries run with a proxy offered so an update, telemetry or phone-home attempt is seen; none is. CF-03 ✅ (WP-43, WP-59): every adapter, acp-gemini included, holds the tool gateway before its first turn; on the real binaries in `test_e2e3_f6_mcp_startup_real_engines.py` and Codex's `test_e2e3_f6_mcp_startup_real_binary.py`
- [x] **P0** Native transcripts are stored verbatim so they can be re-parsed later (UP-07). — ✅ `backend/python/tests/unit/harness_engines/test_wp03_native_adapters.py`, `tests/unit/harness_core/engines/durable/test_wp03_kernel.py`
- [x] **P0** Unknown native events are passed through as `native.unknown` rather than crashing (EV-13). — ✅ `backend/python/tests/unit/harness_engines/test_wp03_native_adapters.py`, `tests/unit/harness_core/engines/durable/test_wp03_kernel.py`
- [ ] **P1** A generic ACP adapter covers Gemini CLI, Goose, Qwen Code, Cursor CLI and Kilo, with ACP v1 support and a v2 shim. — ◐ WP-76: opencode, Gemini CLI and Goose ship (Goose real-binary lane 35 passed, 10 skipped, CF-20 failed: upstream NFC); Qwen Code ships (WP-91) and passes its real-binary lane (WP-102: 41 passed, 10 skipped by capability), Cursor CLI not profileable (closed, own backend), Kilo profileable and left; ACP v2 is alpha only, no shim. See C2.41 in the project tracker.
- [ ] **P1** The PipesHub native `agent_loop_lib` is exposed as a harness kind behind the same session resource (OWD-5).
- [ ] **P2** DeepSeek Harness (`dsh`) adapter, once its developer preview stabilizes. — ◐ WP-105 (built now): `acp-dsh`, DeepSeek Harness 0.2.2-rc.1 over the generic ACP driver, its real-binary lane passing (CF-02 a strict xfail, upstream); unsigned until OB-1, its tools layer licence scan to run. See C2.43 in the project tracker.
- [ ] **P2** Terminal fallback adapter for Aider and other TUI-only tools, marked "limited" in the UI.

### 2.5 Event store and projections

- [ ] **P0** Use a versioned envelope `{v, session_id, seq, turn_id, ts, source, type, parent, payload}`, with a strictly increasing `seq` (EV-01).
- [ ] **P0** Storage (OWD-4):
  - the session index and events live in Mongo
  - large payloads and native transcripts go to blob storage via `StorageServiceInterface`
  - the graph holds only permission and lineage edges
- [ ] **P0** SSE resume works with `Last-Event-ID` and produces no gaps or duplicates (EV-02).
- [ ] **P0** AG-UI projection:
  - the existing `agui-event-handler.ts` keeps working
  - new CUSTOM events cover approval, terminal, diff and todo
  - older clients ignore those events safely (EV-11)
- [x] **P0** Every `tool.call` gets exactly one result, error or cancellation (EV-06). — ✅ H8 in `backend/python/harness_core/testing/history.py` and `backend/nodejs/apps/tests/support/harness/journal-history.ts`; `tests/unit/harness_core/engines/durable/test_wp03_kernel.py`, `backend/nodejs/apps/tests/modules/agent-harness/ev06-node-closes-open-calls.test.ts`
- [ ] **P1** Project sessions into conversation `parts` so chat history renders harness sessions (EV-12).
- [x] **P1** History is paginated, and the UI virtualizes lists of 10k+ events (EV-15). — ✅ WP-76: `frontend/app/(main)/chat/components/virtual-rows/` (measured virtualizer); `virtual-rows/__tests__/ev15-*.test.ts(x)`; `frontend/tests/e2e/perf/chat-large-run.perf.spec.ts` EV-15
- [ ] **P1** Readers handle every historical `v` (UP-03).

### 2.6 Scheduling, triggers and background runs

- [x] **P1** Cron schedules fire exactly once per firing across replicas (TR-01). — ✅ `backend/nodejs/apps/tests/modules/agent-harness/schedule-firer.test.ts` SF-2, `backend/nodejs/apps/tests/modules/agent-harness/schedule-store.mongo.itest.ts` ST-9 (WP-24 rerun)
- [x] **P1** A missed-firing catch-up policy (skip, run once, or run all) is configurable (TR-02). — ✅ WP-24: `backend/nodejs/apps/tests/modules/agent-harness/triggers2.test.ts` TR-02 (`run_all`, one per tick, at most 24), SF-3 (skip, run_latest)
- [x] **P1** MessagingFactory event triggers are idempotent on redelivery, and the event context is staged in `/pipeshub/inputs/trigger.json` (TR-03). — ✅ WP-24: `backend/nodejs/apps/tests/modules/agent-harness/triggers2.test.ts` TR-03, `backend/python/tests/unit/harness_core/engines/durable/test_tr03_trigger_input.py`, `backend/python/tests/unit/harness/test_tr03_trigger_context.py`
- [x] **P1** Webhook triggers verify signatures, cap payload size and are rate-limited (TR-04). — ✅ WP-24: `backend/nodejs/apps/tests/modules/agent-harness/triggers2.test.ts` TR-04, `backend/nodejs/apps/tests/modules/agent-harness/schedule-routes.http.test.ts` RT-6, RT-7
- [x] **P1** The trigger principal is explicit, and audit records the trigger as initiator (TR-05). — ✅ `backend/nodejs/apps/tests/modules/agent-harness/schedule-budget.test.ts` BG-1, `backend/nodejs/apps/tests/modules/agent-harness/owd2-run-attribution.test.ts` (`initiatorRef: trigger:<scheduleId>`), SF-4 (WP-24 rerun)
- [x] **P1** An overlap policy (skip, queue or allow) applies when a trigger fires while a previous run is active (TR-06). — ✅ WP-24: `backend/nodejs/apps/tests/modules/agent-harness/triggers2.test.ts` TR-06, store contract ST-11 (memory and Mongo)
- [ ] **P1** Completion notifications go to in-app, email and Slack (TR-07). — ◐ WP-24: built on the app's inbox, mail and Slack-bot paths, tested with every outside send faked (`backend/nodejs/apps/tests/modules/agent-harness/triggers2.test.ts` TR-07, `backend/nodejs/apps/tests/modules/enterprise_search/adapters/harness-schedule-channels.test.ts`). The channel picker in the schedule form and the admin Triggers card are built (UI-3: `frontend/app/(main)/workspace/schedules/__tests__/schedules-page.test.tsx` TR-UI-1..TR-UI-4, wp24-triggers-card.test.tsx (moved out of this repository)). Left: one live email and Slack delivery
- [x] **P1** Background runs never auto-approve, and approvals route to the owner (AP-14). — ✅ `backend/nodejs/apps/tests/modules/agent-harness/schedule-approvals.test.ts` AP-1..AP-3 (WP-24 rerun)
- [ ] **P2** Slack-mention trigger with replies in the thread (J-23).

### 2.7 Composition

- [x] **P1** `HarnessAgentRunner` implements the delegation interface (`DelegationTools`), so a harness session can act as a `spawn_agent` / `AgentTool` worker (CM-02). — ✅ WP-28: deep mode's `spawn_agent(role="coder")` runs on the engine (`harness_agent_runner.py`; the durable delegation port, not the in-process Protocol), `test_cm02_harness_agent_runner.py`; `code_task` beside it, `test_recs2_r7_code_task.py`
- [x] **P1** A child session: — ✅ `test_children::test_ch1_*`, `test_child_grants::test_bs1_*`, `test_policy_properties::test_tgw18_*`, `test_cm02_harness_agent_runner.py` (engine child's token share)
  - inherits the principal and can only narrow its scope
  - receives a slice of the parent's budget
  - passes its taint up to the parent (CM-03, CM-04)
- [x] **P1** Cancelling the parent cascades to all children within 5 s (CM-05). — ✅ `tests/unit/harness_core/engines/durable/test_cm05_cancel_cascade.py`; an engine child still waiting before its engine opens (a sign-in, its sandbox) ends `cancelled` at once too (WP-65: `tests/unit/harness_core/engines/test_wp65_cancel_before_open.py`, Docker `tests/unit/harness/engines/test_wp65_cancel_docker.py`)
- [x] **P1** Depth and fan-out limits are enforced (CM-06). — ✅ Node CG-7a/CG-6d/CA-3 (`child-run-contract.ts`, `child-runs.test.ts`), `test_deep_delegation.py` (fan-out per turn), `test_cm02_harness_agent_runner.py` (one coding hand-off per message), engine helpers depth 1 (`test_cf11_sub_agent_limits`) and concurrency (`claude_code/test_launch.py`, `codex/test_launch.py`)
- [x] **P1** Harness-native subagents render as a nested tree (CM-01, EV-07). — ✅ WP-28: `test_ev07_helper_steps.py`, `test_ev07_helper_frames.py`, contract `test_ev07_*` (fakes and the real Claude Code, Codex and opencode binaries), `ev07-engine-helpers.test.tsx`
- [ ] **P2** Parallel workers on the same repository use separate worktrees or branches (CM-07).

---

## 3. Capabilities exposed to the harness

### 3.1 Workspace materialization and the `/pipeshub` contract

- [x] **P0** The contract v1 layout is present, including a `VERSION` file (WS-01). — ✅ `test_contract_v1.py::test_ws01_*`, D `test_recs_audit_real_engine::test_r3_*`, `ph_e2e` ×3 (layout: guides/pipeshub-contract-reference.md)
- [x] **P0** `AGENTS.md`:
  - 100 lines or fewer
  - human-curated template
  - no secrets, timestamps or random IDs (WS-04) — ✅ `test_contract_v1.py::test_ws04_*`, `test_folder.py::test_pf7_*`
- [x] **P0** `CLAUDE.md` contains `@AGENTS.md`, and Gemini's `contextFileName` is set to `AGENTS.md`. — ✅ decided form (SEC1): engine instruction files stay empty and read-only; `/pipeshub/AGENTS.md` is rendered read-only and named by every engine's base prompt (`AGENTS_POINTER`); no Gemini engine. `test_contract_v1.py::test_c3_03_*`
- [ ] **P0** Output is byte-deterministic for the same (agent version, catalog, principal scope) (WS-02, WS-03).
- [x] **P0** Native configs are injected for each harness (WS-05): — ✅ Gemini (WP-13): `backend/python/tests/unit/harness/engines/test_real_gemini_acp_conformance.py::test_rxgem1_ws05_*` (real Gemini CLI 0.63.0 over ACP, `~/.gemini/settings.json` `mcpServers`), `backend/python/tests/unit/harness_engines/acp/test_gemini_profile.py::TestWS05*`; Claude/Codex/OpenCode/ACP as audited. WP-59: the gateway's only declaration is Gemini's system settings, a sealed copy of the image's with the entry beside them, in the read-only `/pxh-sealed` mount (`mcp.declare: config`; an ACP entry dropped the run's tool timeout, a user-settings one a repository could replace), proven by a hung gateway tool cut at the rendered timeout and a repository's own `tools` entry (other URL, longer timeout, extra headers) changing nothing, on new and loaded sessions (`test_e2e3_f6_mcp_startup_real_engines.py::test_wp59_*`)
  - Claude: `--mcp-config` and `--settings`
  - Codex: `config.toml`
  - OpenCode: `opencode.json`
  - Gemini: `settings.json`
  - ACP: `session/new`
- [ ] **P0** Configuration that a repository provides is ignored by default: `.mcp.json`, hooks, `.codex/config.toml`, `opencode.json` (WS-07).
- [ ] **P0** Read-only mounts cover `tools/`, `skills/` and `AGENTS.md`; `workspace/` and `outputs/` are writable (WS-13).
- [x] **P0** Inputs are staged with sanitized names and size limits (WS-10, SEC-PI-07). — ✅ a9b895bd78: `durable/test_input_names.py::test_sec_pi_07_*`, `contracts/engine_inputs.py::test_sec_pi_07_*`, `::test_c3_08_*`
- [ ] **P0** Files in outputs become artifacts with lineage (WS-11, AR-01, AR-02).
- [x] **P1** Repository `AGENTS.md` / `CLAUDE.md` are merged as content with a lower trust label, never treated as config (WS-08). — ✅ native loaders stay off; the controller quotes them, fenced and labelled, after the request in the session's first message (ADR-OWD-06): `backend/python/tests/unit/harness_core/engines/test_ws08_repository_instructions.py` (WP-42)
- [x] **P1** A contract-version mismatch is refused with a clear error (WS-12). — ✅ `test_contract_v1.py::test_ws12_*` (`engine_contract_unsupported`)

### 3.2 `ph` CLI and generated SDK

- [x] **P0** Commands: `ph search`, `ph fetch`, `ph tools {list,search,describe,call}`, `ph ask`, `ph publish`, `ph skill`, `ph --version` (WS-15). — ✅ search, fetch, tools, --version (`test_ph_cli.py::test_ph14..ph17`); `ph skill` (SK-05, `test_sk05_ph_skill_and_policy.py`); `ph ask` and `ph publish` (WP-67: `test_c312_ph_ask_publish.py`, `test_c312_publish_tool.py`); D `ph_e2e` `test_ph_e2e_c312_*` (skill, publish, ask) on opencode, Codex and Claude Code. The image's own `ph` gets ask/publish with the next engine image (the session folder's `ph.py` has them now)
- [x] **P0** A `--json` mode with stable output, non-zero exit codes and actionable error messages. — ✅ `test_ph_cli.py::test_ph18_*` (golden `ph_json_golden.json`), `test_ph19_*`
- [ ] **P0** No token inside the sandbox: the egress proxy injects the credential.
- [x] **P1** Typed Python and TypeScript stubs are generated under `/pipeshub/sdk/` for programmatic tool calling (WS-14). — ✅ WP-57: rendered per turn from the pinned grant into `/pipeshub/session/sdk/` (`harness_engines/pipeshub/sdk.py`): `backend/python/tests/unit/harness_engines/pipeshub/test_ws14_sdk_stubs.py` (SDK-1..SDK-9); D `test_ws14_sdk_e2e_*` on opencode, Codex and Claude Code. The `/pipeshub/sdk` image link waits for the next engine image
- [x] **P1** Stubs and `ph tools call` return identical results. — ✅ WP-57: each stub call is one `ph --json tools call`; `backend/python/tests/unit/harness/gateway/test_ws14_sdk_parity.py` (SP-1..SP-4: PC-2's ten cases plus timeout, refused credentials and gateway down, through ph, the Python stub and the TypeScript stub, answers and journal equal)

### 3.3 Knowledge tools

- [x] **P0** `knowledge_search` and `knowledge_fetch` are separated from `ChatState` and run as a stateless service API. — ✅ `backend/python/tests/unit/harness/knowledge/test_wp12_knowledge_service.py` (`test_c3_17_*`, `test_parity_through_the_gateway_*`)
- [ ] **P0** Searches are filtered by ACL under the principal, using `get_accessible_virtual_record_ids` (KN-01).
- [ ] **P0** A service-account principal searches with that account's access only (KN-03).
- [x] **P0** Filters for connector, date, collection/KB and record type are applied server-side (KN-04). — ✅ `backend/python/tests/unit/harness/knowledge/test_wp12_knowledge_service.py` (`test_kn04_*`)
- [ ] **P0** Results carry stable citation IDs and deep links built with `app/utils/text_fragments/`. Citations in final answers resolve (KN-05).
- [x] **P0** Output is concise by default, with a `response_format=detailed` option. Results over the cap are offloaded to `/pipeshub/knowledge/` (KN-06). — ✅ `backend/python/tests/unit/harness/knowledge/test_wp12_knowledge_service.py` (`test_kn06_*`), `backend/python/tests/unit/harness/gateway/test_wp12_knowledge_shelf.py`
- [x] **P0** Content provenance and an `untrusted` label are attached, and the session taint is set (KN-08). — ✅ `backend/python/tests/unit/harness/knowledge/test_wp12_knowledge_service.py` (`test_kn08_*`), `backend/python/tests/unit/harness_core/effects/test_wp12_rule_of_two.py`
- [ ] **P1** No results, no access and index-not-ready are reported as distinct messages (KN-09).
- [ ] **P1** Results are the same across every graph and vector backend (KN-10).
- [ ] **P1** Knowledge-graph and entity tools respect ACLs (KN-11).
- [ ] **P1** Retrieval quality on the golden set is at least as good as native-agent retrieval (KN-12).
- [x] **P2** Quarantined map-reduce summarization tool for bulk untrusted content. — ✅ WP-41 (= C20.58): `backend/python/tests/unit/harness_core/context/test_ga28_quarantined_summarizer.py`, `backend/python/tests/unit/harness/knowledge/test_ga28_knowledge_summarize.py`

### 3.4 Toolsets through the Tool Gateway

- [ ] **P0** The default surface is small and stable: `knowledge_*`, `tools_search`, `tools_describe`, `tools_call`, `ask_user`, `artifact_publish`. — ◐ `ask_user` (WP-10); OWD-7 stable names built at the engine gateway, both forms answered (WP-22: `backend/python/tests/unit/harness_core/tool_identity/test_wp22_spec_names.py`), off per engine until each engine is measured on them
- [x] **P0** Arguments are validated against JSON Schema 2020-12, and validation errors name the field and the fix (TG-04, TG-05). — ✅ a9b895bd78: `harness_core/gateway/test_tg05_argument_validation.py`, `harness/gateway/test_tg05_tg08_over_mcp.py`, `harness_engines/pipeshub/test_tg04_describe_schema.py`
- [ ] **P0** Tools execute with the user's own connector credentials from etcd, which are never returned to the sandbox (TG-06).
- [ ] **P0** OAuth tokens refresh transparently. If refresh fails, the user gets a re-authenticate link (TG-07).
- [ ] **P0** Per-tool timeouts and cancellation propagation (TG-09).
- [x] **P0** Connector 429s are retried with backoff within budget (TG-08). — ✅ a9b895bd78: `harness_core/gateway/test_tg08_rate_limited_reads.py`, `harness/gateway/test_tg05_tg08_over_mcp.py::test_tg08_*`
- [ ] **P0** Each call is idempotent on `(session, turn, call_id)` (TG-10).
- [ ] **P0** Output is capped at about 25k tokens or offloaded (TG-11).
- [ ] **P0** Masked tools: a tool denied by policy stays listed and returns a reason (TG-13).
- [x] **P0** Toolsets with missing credentials show `needs_authentication` instead of crashing (WS-09). — ✅ a9b895bd78: `harness/assembly/test_qa23_credential_availability.py::test_ws09_*`, `harness/gateway/test_ws09_needs_authentication.py`, vitest `ctl5b-run-notice` WS-09
- [x] **P0** MCP protocol compliance for the supported spec revisions (TG-18). — ✅ a9b895bd78: `harness/gateway/test_tg18_mcp_conformance.py` (raw JSON-RPC checks + the locked TypeScript SDK client), CI step in `contracts-codegen.yml`
- [x] **P1** Per-session concurrency cap, and parallel calls are isolated from each other (TG-15). — ✅ WP-72: `backend/python/tests/unit/harness_core/gateway/test_tg15_parallel_calls.py`
- [x] **P1** Smoke test for every toolset category through the gateway (TG-16). — ✅ WP-76 tg16: D `backend/python/tests/unit/harness/engines/test_tg16_toolset_categories.py` (Slack, Google, Microsoft, image generation, storage search, knowledge hub, database_sandbox) + `test_e2e3_tool_matrix.py` (Retrieval/KG, Jira, MCP, code)
- [x] **P1** DLP and redaction on tool output (TG-12). — ✅ WP-22: `allow_with_redaction` masks credentials and personal data from a tool's result, `backend/python/tests/unit/harness_core/effects/test_wp22_allow_with_redaction.py`. Further redaction rules are an extension point with no default in this build (`ClassificationPort`; default `NO_CLASSIFICATION`: nothing more redacted); the shared parser: `backend/python/tests/unit/harness/policy/test_classification_ports.py` CL-1..CL-4
- [ ] **P2** Behavior matches the native lazy meta-tools (TG-17).

### 3.5 External MCP servers

- [x] **P1** All traffic to external MCP servers is proxied through the Tool Gateway (MX-01). — ✅ `backend/python/tests/unit/harness/mcp_hub/test_open_admission_default.py`; the admin allowlist of MCP tools (MX-02) moved out of this repository.
- [x] **P1** Tool names are namespaced per server instance (MX-05). — ✅ `backend/python/tests/unit/harness/mcp_hub/test_hub_wiring.py`, `backend/python/tests/unit/harness/mcp_hub/test_mcpfix_attachment.py`
- [x] **P1** Tool descriptions are scanned again on each listing; a changed one the scanner flags is held back (MX-03). — ✅ `backend/python/tests/unit/harness/mcp_hub/test_open_admission_default.py`; re-approval of a changed manifest moved out of this repository.
- [x] **P1** Tool descriptions are scanned for injection when a server is attached (MX-04). — ✅ WP-25: `backend/python/tests/unit/harness_core/mcp_hub/test_mx04_description_scan.py`, `backend/python/tests/unit/harness/mcp_hub/test_open_admission_default.py`.
- [x] **P1** Per-user OAuth stays on the server side, with no token passthrough (MX-06). — ✅ `backend/python/tests/unit/agents/adapter/test_mcp_session.py`, `backend/python/tests/unit/harness/mcp_hub/test_mcpe2e_discovery_refresh.py`
- [x] **P1** Custom stdio servers need `MCP_ALLOW_CUSTOM_STDIO`, run outside the harness sandbox, and receive only an allowlisted environment (MX-07). — ✅ `backend/python/tests/unit/api/routes/test_mcp_servers_handlers.py`
- [x] **P1** Timeouts and a circuit breaker for each server (MX-08). — ✅ WP-25: `backend/python/tests/unit/harness/mcp_hub/test_mx08_server_breaker.py`
- [ ] **P2** Private MCP registry that pulls selectively from the official registry.

### 3.6 Skills

- [ ] **P0** Agent-assigned skills are materialized from `GraphSkillStore` using `SkillBundle.as_sandbox_files()` (SK-01).
- [x] **P0** Skills are symlinked into `.claude/skills`, `.agents/skills`, `.opencode/skills` and `.gemini/skills` (WS-06). — ✅ decided form: each engine's own discovery path; Gemini's root-owned `~/.gemini/skills` link, `backend/python/tests/unit/harness/engines/test_real_gemini_acp_conformance.py::test_rxgem2_ws06_*` (WP-13)
- [x] **P0** Built-in packs (pdf, docx, xlsx, pptx, data-analysis) work, with their dependencies in the image (SK-03). — ✅ WP-68: every built-in pack runs its preferred path on all four `-ph5` release run images, 57 passed (`backend/python/tests/unit/harness/sandbox_broker/test_sk03_builtin_packs_docker.py`, `test_engine_tools_image_docker.py`); the SK1 third-party lane moved to SK-06's item
- [ ] **P1** The skill version is pinned for each session (SK-04).
- [x] **P1** Harnesses without native skills get `skills_search` / `skill_load` or `ph skill` (SK-05). — ✅ `ph skill {list,show}` over the read-only skills folder: `backend/python/tests/unit/harness_engines/pipeshub/test_sk05_ph_skill_and_policy.py::test_sk05_*` (WP-42)
- [ ] **P1** Only signed or approved skills can be mounted, and imports go to review (SK-06, SEC-SC-04).
- [ ] **P2** Skills learned from harness sessions go to candidates and are never auto-published (SK-07).

### 3.7 Model access

- [ ] **P0** Supported wire formats: Anthropic Messages and OpenAI Responses (MG-01, MG-02).
- [ ] **P0** Reasoning and thinking blocks are preserved round-trip, as DeepSeek, Kimi and GLM require (MG-05).
- [ ] **P0** Model aliases map to org-configured models. Disallowed models are rejected (MG-06, MG-07).
- [ ] **P0** Metering is exact, including cache read and write tokens (MG-11, EV-10).
- [ ] **P0** Bring-your-own (BYO) keys are stored encrypted and never visible in the sandbox (MG-12).
- [ ] **P0** Budget enforcement mid-stream returns a typed error (MG-08).
- [ ] **P0** Provider 429/529/5xx errors are translated into the error shape each harness expects (MG-09). — WP-77: an engine run waits a provider failure out (`llm_backoff`, 225 s), then ends cut short by the provider with a partial answer; the gateway logs the upstream status and code (`test_wp77_provider_wait.py`, `test_wp77_provider_failure_logged.py`)
- [ ] **P1** Chat Completions wire format for OpenCode, Qwen and Goose (MG-03).
- [ ] **P1** A context-overflow error is translated so the harness triggers its own compaction (MG-10). — ◐ WP-76 mg10: translated, compacted and retried on Codex, Claude Code and opencode (real binaries, contract `cf21`); open on the ACP engines (opencode over ACP, Gemini CLI)
- [x] **P1** Every model route asks one extension point before it is used (MG-15). — ✅ Every model route and builder goes through the one funnel that asks (`tests/cohesion/test_mg15_one_placement_funnel.py`, `tests/cohesion/test_mg15_one_model_funnel.py`). The extension point has no default in this build (`ModelPlacementPort`, `RunModelPlacement`, Node `ModelPlacementSource`; defaults `ALLOW_EVERY_MODEL` and `NO_RUN_MODEL_PLACEMENT`: every model call allowed, a run's policy handed out as is): `backend/python/tests/unit/harness/policy/test_placement_ports.py` RSP-1..RSP-5, `backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts`
- [ ] **P2** Gemini wire format (MG-04). Image and document passthrough with a capability check (MG-14).

### 3.8 Human in the loop

- [x] **P0** `ask_user` is a durable wait: the session moves to `AWAITING_INPUT` and survives a suspend (AP-13, J-25). — ✅ 131ded6cd8: `tests/unit/harness_core/engines/durable/test_wp10_engine_hitl.py`, `tests/unit/harness/engines/test_wp10_engine_question.py`, `test_real_*_conformance::test_cf_ap13_*` (Codex, Claude Code, opencode, ACP opencode)
- [x] **P0** Approval cards show the concrete effect (recipients, domains, diff, cost) rather than raw JSON alone (UX-03). — ✅ UI-2: the card shows an engine edit's diff and a declared cost (`wp10-approval-effect-card.test.tsx`, browser WP10-01/02); backend: edit diff and declared cost on engine approval lines and the approval view (`harness_core/effects/preview.py`, `tests/unit/harness_core/effects/test_wp10_effect_preview.py`, `projection/wp10-approval-effect.test.ts`). A loop call's card shows its labelled values; only engine edits and cost-tagged tools carry a diff or cost
- [ ] **P0** Approve once, deny with a reason that the harness receives, and expiry defaults to deny (AP-03, AP-05, AP-07). — ◐ the card's reason field is built (UI-2, `wp10-approval-effect-card.test.tsx`, browser WP10-03); AP-03 and AP-07 pass; the decision routes take a deny `reason` that reaches the gateway refusal, the engine's resume note and the loop's refusal (`deny-reason.test.ts`, `test_wp10_gateway_hitl.py`, `test_wp10_deny_reason.py`). Left: AP-05's "agent adapts" check with a real model
- [ ] **P0** Harness-native permission requests are bridged through `canUseTool`, `requestApproval` or ACP `request_permission` (AP-11).
- [ ] **P0** The sandbox can never forge an approval (AP-15).
- [x] **P1** Approve for the session (pattern-scoped), and edit-then-approve with both versions audited (AP-04, AP-06). — ✅ WP-50: the card's editor is built (Edit beside Allow once for the run's actor only, in place, receipt with old/new lines, also after a reload; F `wp50-approval-card-edit.test.tsx`, `wp50-approval-edits.test.ts`, `wp50-decide-edited.test.ts`; N authority `edit`). WP-29: AP-04 ✅ (a grant holds for the same tool and destination, in this run and the conversation's next, and the card says what it covers: `test_wp29_session_grants.py`, `wp29-conversation-scope.test.tsx`); AP-06 backend ✅ (worker offers `editable`, Node journals the edit inline with its SHA-256 beside the call's own `argsDigest`, the loop and the engine gateway run the edit and tell the model: `test_wp29_edit_then_approve.py`, `test_wp29_gateway_edits.py`, `test_wp29_edits.py`, `wp29-edit-then-approve.test.ts`, execution-store contract AP-06 on memory and Mongo). The card's editor: WP-50 (above).
- [x] **P1** Approvers other than the principal are routed correctly, and multiple concurrent approvals are supported (AP-08, AP-09). — ✅ AP-08: the decision rule and the `ApproverDirectory` extension point (`approver-directory-contract.ts` AD-5..AD-9; nobody approves by group by default); AP-09: `test_wp29_concurrent_approvals.py` (each held call decided on its own, results in call order), DECIDE409 SD-1/DC-5
- [x] **P1** Slack and email approval links are signed, single-use and expiring (AP-10). — ✅ links and their notices are extension points (`WaitingRunNotices`, `ApproverNoticeOutbox`; none bound by default, `harness-edition.test.ts` "approval routing"); the email and Slack delivery is the schedule notifier's (`harness-schedule-notifier.test.ts`); every sign-in method returns to the page a person came from (F `wp54-sign-in-returns.test.tsx`)

### 3.9 Outputs and git

- [ ] **P0** Artifacts are versioned, carry lineage and are downloaded through signed URLs (AR-01..04).
- [x] **P1** Clone happens in the setup phase with a scoped token that is removed before the agent phase (GT-01). — ✅ `tests/unit/harness_core/sandbox/egress/test_gitflow_git.py` TestGT01SetupPhase, `tests/unit/harness_core/engines/test_gitflow_controller.py`
- [x] **P1** A git proxy allows pushes only to the session branch. Force-push, tags and branch deletion are denied (GT-02, GT-03). — ✅ `tests/unit/harness_core/sandbox/egress/test_gitflow_git.py` TestGT02SessionBranchOnly, TestGT03Refusals, TestPushRuleUnits
- [x] **P1** PRs are created by the control plane after approval, with citations and a link to the session (GT-04). — ✅ `tests/unit/harness/engines/test_gitflow_pull_request.py`
- [x] **P1** Hooks and submodules from a malicious repository are neutralized (GT-05). — ✅ `tests/unit/harness_core/sandbox/egress/test_gitflow_git.py` TestGT05MaliciousRepository

### 3.10 Memory (v2)

- [ ] **P2** Scoped memory (user, agent, org) is mounted at `/pipeshub/memory` (ME-01).
- [ ] **P2** Each entry records its source session, author and connector (ME-02).
- [ ] **P2** Writes from untrusted sessions stay in user scope; org-scope writes require review (ME-04).
- [ ] **P2** Path-traversal protection, size caps, TTLs, and a user can view and delete their memory (ME-03, ME-05).

---

## 4. Configurability

### 4.1 Configuration model

- [ ] **P0** Precedence is **Global → Org → Agent → Harness profile → Session → User preference**. Lower scopes can only narrow security settings, never widen them.
- [x] **P0** Any setting can be **locked** at a scope (an admin lock), and a lower scope cannot override a locked value. — ✅ `backend/python/tests/unit/harness_core/policy/test_c4_02_locks.py`, `backend/nodejs/apps/tests/modules/agent-harness/policy/c4-02-locks.test.ts`, vectors PR-46..PR-59
- [x] **P0** All reads go through `ConfigurationService`, never `KeyValueStore` directly (AGENTS.md rule). — ✅ `backend/python/tests/cohesion/test_c4_03_config_reads.py`
- [x] **P0** The **effective config** is resolved once at session start, hashed into `policy_version`, and shown in the session details. — ✅ `CONTROL_PLANE_PIN` on by default (`backend/nodejs/apps/tests/modules/agent-harness/pin-switch.test.ts`); `/status` shows it as the settings version (`frontend/app/(main)/chat/components/message-area/__tests__/qafix-status-row.test.tsx`)
- [ ] **P0** Config changes are validated against a schema. Invalid values are rejected with field-level errors.
- [ ] **P1** Every config change is audited with old value, new value, actor and time.
- [ ] **P1** A config change affects new sessions only. A running session must refresh explicitly or be killed.
- [x] **P1** Export and import of agent + harness profile config, with secrets excluded, for promotion between environments. — ✅ WP-30 (`backend/python/tests/unit/agents/test_c4_08_portable_agent.py`, `backend/python/tests/unit/api/routes/test_c4_08_agent_export_import_routes.py`, `backend/nodejs/apps/tests/modules/agent-harness/c4-08-agent-config-transfer.test.ts`, AD-09..AD-12; builder UI mock with the owner)
- [x] **P2** A dry-run "what would this session get" preview in the agent builder. — ✅ WP-90: "Check what a chat gets" on the agent card, through explain (wp90-dry-run.test.tsx (moved out of this repository); C4.09).

### 4.2 Configuration surface

Scopes where a setting can be set are marked. "Lock" means an org admin can lock it.

| Setting | G | O | A | H | S | U | Lock | Default |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|---|
| Feature enabled | ● | ● | | | | | ● | off |
| Allowed harnesses + versions | ● | ● | ● | | | | ● | none |
| Harness default version / pin | | ● | ● | ● | | | | latest approved |
| Sandbox provider + runtime class | ● | ● | | ● | | | ● | Docker+gVisor (Compose), agent-sandbox+gVisor (Helm) |
| Sandbox CPU / memory / disk / pids | ● | ● | ● | ● | | | ● | template |
| Warm-pool size per template | ● | | | ● | | | | 0 (Compose), 2 (Helm) |
| Idle timeout / max lifetime | ● | ● | ● | | ● | | ● | 15 min / 8 h |
| Snapshot TTL / retention | ● | ● | | | | | ● | 7 days |
| Allowed models per harness | | ● | ● | ● | | | ● | org default model |
| Model alias map | | ● | | ● | | | | provider defaults |
| Egress policy (setup phase) | ● | ● | ● | ● | | | ● | git host + package mirrors |
| Egress policy (agent phase) | ● | ● | ● | ● | ● (via approval) | | ● | deny all except gateways |
| Package mirrors | ● | ● | | | | | ● | none |
| Git hosts + push policy | | ● | ● | | | | ● | session branch only |
| Toolsets / MCP servers / skills / knowledge | | ● (allowlist) | ● | | | | ● | none |
| Tool disclosure (`eager` / `lazy` / `filesystem`) | | ● | ● | ● | | | | per-harness default (§5) |
| Direct-tool count cap | ● | ● | ● | | | | | 20 |
| Tool result cap / offload threshold | ● | ● | ● | | | | | ~25k tokens |
| `response_format` default | | | ● | | ● | | | concise |
| Policy: risk-class defaults (allow / ask / deny) | | ● | ● | | | | ● | read=allow, write_internal=allow, write_external=ask, destructive=ask, financial=deny |
| Rule-of-Two enforcement | | ● | ● | | | | ● | on |
| Approval TTL / approver set | | ● | ● | | | | ● | 1 h / principal |
| "Approve for session" allowed | | ● | ● | | | | ● | on |
| Budgets: tokens / cost / turns / wall-clock / sandbox-seconds | ● | ● | ● | | ● | | ● | org-defined |
| Concurrency caps (org / user) | ● | ● | | | | | ● | 10 / 2 |
| Reasoning visibility in UI | | ● | ● | | | ● | ● | on if harness exposes it |
| Terminal view (read-only / hidden) | | ● | ● | | | ● | | read-only |
| Transcript + audit retention | ● | ● | | | | | ● | 90 days / 1 year |
| Trace content capture | ● | ● | | | | | ● | off |
| Schedules / triggers | | ● (allow) | ● | | | | ● | off |
| Notifications channels | | ● | ● | | | ● | | in-app |
| Harness telemetry export (OTel) | ● | ● | | ● | | | ● | off |
| Auto-resume after crash | | ● | ● | | | | | on, if no pending side effects |
| Dev / insecure mode (runc, open egress) | ● | | | | | | ● | off (never in prod) |

All defaults above are proposals to validate in Phase 0.

- [x] **P0** Every row above has a schema, a default, docs and a test proving precedence and lock behavior. — ✅ WP-68: every row carried in `contracts/harness/config-surface.json` (27 dimension, 4 operator env, 3 section, none partial or missing), held by `backend/python/tests/cohesion/test_c4_10_config_surface.py` (C410-1..3); new dimensions `quotas.*`, `toolResults.*`, `interactions.*`, `recovery.autoResume`, `sandbox.*`, `ui.*` with release defaults, QA-2 ladders and LK-1/LK-2 (`backend/python/tests/unit/harness_core/policy/test_qa2_stricter_only.py`, `test_c4_02_locks.py`, Node `qa2-stricter-only.test.ts`, `c4-02-locks.test.ts`), documented in guides/builder-guide.md and enforced by their consumers (`backend/python/tests/unit/harness/test_c4_10_run_tuning.py`, `backend/nodejs/apps/tests/modules/agent-harness/c410-run-policy.test.ts`)
- [ ] **P1** Admin UI for org-scope rows; agent builder for agent-scope rows; a harness profile editor for harness-scope rows. — ◐ WP-69: Workspace settings › Run settings edits the org rows of `quotas`, `toolResults`, `interactions`, `recovery`, `sandbox`, `ui` (WP69-UI-1..10); agent-builder rows for them still to do.

---

## 5. Lazy loading and progressive disclosure

Rule of thumb: load **lazily what the model or user pays for**, and load **eagerly what keeps prompt prefixes stable**. Never mutate the tool list during a session.

| What | Strategy | Why | Checks |
|---|---|---|---|
| Sandbox | **Lazy for knowledge-only agents**: no sandbox until a code, file or shell capability is first needed; native/in-process path otherwise. Eager (warm pool) for coding harnesses | Cost and latency for simple Q&A; harness CLIs need a sandbox from the start | Knowledge-only session never allocates a sandbox |
| Harness image | Pre-pulled on nodes; never pulled at session time | Cold start | PF-02 |
| Tool catalog (names) | **Eager, pinned** per session, deterministic order | Prompt cache and rug-pull defense | WS-02, WS-03, TG-14 |
| Tool schemas (full) | **Lazy**: `tools_search` / `tools_describe` / files under `/pipeshub/tools/` | ~85–98% token savings; accuracy drops past 30–50 tools | PF-11 |
| Direct tools | Eager for ≤ ~20 pinned tools only (the 3–5 most-used plus those the agent builder pins) | Lowest latency for common calls | TG-03 |
| Tool schema files on disk | Materialized at session start (cheap; deterministic); generated SDK stubs built lazily on first import if large | Fast start, still discoverable | WS-01, WS-14 |
| Toolset credentials | **Lazy**: resolved at first call; listed with auth status up front | Don't fail a session over an unused toolset | WS-09, TG-07 |
| External MCP servers | Manifest fetched and pinned at start; **connection opened lazily** on first call; pooled per session | Startup latency, rug-pull defense | MX-03, MX-08 |
| Skills | Frontmatter eager (in skill index); body on use; resources on demand | ~100 tokens/skill at rest | SK-02 |
| Knowledge results | Concise inline; full content **offloaded** to `/pipeshub/knowledge/` and fetched by path or `knowledge_fetch` | Context budget | KN-06, KN-07 |
| Large tool results | Truncate or offload, return path + summary | Context budget | TG-11 |
| Inputs / attachments | Staged at start if small; large files streamed on first read; one input up to the deployment's `HARNESS_INPUT_MAX_MB` (100 MiB), which an org's `sandbox.inputMaxMb` only lowers (WP-69) | Start latency, disk | J-26 |
| Memory | Index eager, entries on demand | Context budget, poisoning surface | ME-01 |
| Event history (UI) | Paginated; virtualized list; terminal output chunked | Large sessions stay responsive | EV-15, PF-17 |
| Transcripts / snapshots | Loaded only on resume, fork or replay | Storage I/O | PF-03 |

- [x] **P0** Every row above is implemented as stated, and each tool-disclosure mode is selectable per agent (it extends the existing `tool_disclosure` eager/lazy setting). — ✅ WP-68: every row built (audit in the project tracker, WP-68 C5.01 evidence): SDK stubs past 200 tools rendered on first import (`backend/python/tests/unit/harness_engines/pipeshub/test_c5_01_lazy_sdk.py` LS-1..LS-6, SP-5), inputs over 8 MiB placed on first read with `ph inputs get` (`backend/python/tests/unit/harness_core/engines/test_c5_01_lazy_inputs.py` LI-1..LI-7), engine images pre-pulled at broker start (PP-1, PP-2), the activity timeline virtualized (TL-1, VR-10; round 16 replaced its pages), each engine lists what the mode leaves (CF-21); Docker: TD-7 9 passed, CF-21 15 passed on the five real engines, J-26/WS-14 lanes 6 passed (`backend/python/tests/unit/harness/engines/test_c5_01_disclosure_docker.py`, `test_real_*_conformance.py`, `test_real_*controller.py`); per-harness defaults are C5.04's A/B (live models)
- [ ] **P0** Lazy loading never changes the model-visible tool list mid-session. Discovery happens through stable meta-tools or files.
- [ ] **P0** A knowledge-only agent on the native harness never allocates a sandbox.
- [ ] **P1** Each harness's tool-disclosure default is recorded in its harness profile.
- [ ] **P1** Cache-read ratio SLO (≥ 70%) is monitored, with an alarm on regression (MG-13, PF-10).
- [ ] **P1** On Claude-family models, use tool search or `defer_loading` where the harness supports it. Note that this depends on the harness, since PipesHub does not control the API call shape. — ◐ the stable `search_tools`/`call_tool` list stays for now; the owner decides by the C5.04 A/B when live calls resume, and we switch only if native tool search measurably wins (decided 2026-10-26)

---

## 6. Security

### 6.1 Isolation

- [ ] **P0** The outer sandbox (gVisor or a microVM) is the security boundary. The harness's inner sandbox is defense in depth only (OWD-3).
- [ ] **P0** Containers run hardened:
  - non-root
  - `cap-drop ALL`
  - `no-new-privileges`
  - seccomp
  - read-only rootfs
  - (SEC-ISO-02)
- [ ] **P0** No Docker socket, no Kubernetes service account token, and cloud metadata is blocked (SEC-ISO-03).
- [ ] **P0** No east-west traffic between sandboxes (SEC-ISO-04).
- [x] **P0** Escape and exhaustion tests pass, including with the inner sandbox disabled (SEC-ISO-01, -06, -07). — ✅ R31-GVISO: runc and gVisor (runsc) through the real broker, no inner sandbox (`backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_escape_docker.py`, `backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_exhaustion_docker.py`); Kata waits on its shim

### 6.2 Egress

- [ ] **P0** Default deny. The only route out is the egress proxy, enforced at the network level rather than through environment variables (SEC-EG-12).
- [x] **P0** DNS is resolved at the proxy. UDP/53, DoH/DoT, direct IPs, IPv6 and ICMP are blocked (SEC-EG-02..04). — ✅ `backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` (`test_sec_eg_02_03_04_*`, `test_sec_eg_04_*`), `backend/python/tests/unit/harness_core/sandbox/egress/test_wp14_dns_exfiltration.py` (WP-14)
- [x] **P0** The parser-trick regression suite passes: null byte, userinfo, suffix tricks, homoglyphs, trailing dot (SEC-EG-06). — ✅ `backend/python/tests/unit/harness_core/sandbox/egress/test_policy.py` (`TestSecEg06HostTricks`), `backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` (`test_sec_eg_06_*`) (WP-14)
- [ ] **P0** An empty allowlist means no network (SEC-EG-10).
- [x] **P1** Setup-phase and agent-phase policies are separate, with the switch made before the harness starts (SEC-EG-11). — ✅ `tests/unit/harness_core/engines/test_gitflow_controller.py`, `tests/unit/harness_core/sandbox/egress/test_gitflow_git.py` TestGT01SetupPhase
- [x] **P1** Allowed domains carry method and path restrictions. Echo domains (gist, pastebin) are kept out of the defaults (SEC-EG-07). — ✅ WP-63: inspected allowed websites (read = GET/HEAD/OPTIONS, credentials dropped, path prefixes, git refused, read-only hosts never written, 421 on host mismatch); no default list. `backend/python/tests/unit/harness_core/sandbox/egress/test_wp63_allowed_sites.py`, docker `backend/python/tests/unit/harness/engines/test_wp63_sites_docker.py`; see C6.11 in the project tracker.
- [x] **P1** Alert on high-entropy DNS labels and unusual egress volume. — ✅ SEC-EG-14 (WP-45): per-run egress bytes per destination alias, metrics, `egress.volume_alert` on the run's audit record (per-run threshold, org baseline, optional hard cap `EGRESS_RUN_MAX_MB`); the DNS half is moot, no DNS reaches a sandbox (C6.07). `backend/python/tests/unit/harness_core/sandbox/egress/test_wp45_egress_volume.py` (`test_sec_eg_14_*`), `backend/python/tests/unit/harness_core/gateway/test_sec2_refusal_sink_contract.py` (RS-7, RS-8), `backend/python/tests/unit/telemetry/modules/test_wp45_egress_metrics.py`, `backend/python/tests/unit/harness/test_wp45_egress_volume_role.py`, `backend/nodejs/apps/tests/modules/agent-harness/gateway-refusals.test.ts` ("SEC-EG-14 (C6.12) …"); docker (runc) `backend/python/tests/unit/harness/sandbox_broker/test_wp45_egress_volume_docker.py`. Round 11 (item E): a sandbox kept for a conversation (SIGNIN-5) is metered, alerted, kept and folded as the run it serves now; an alert whose send failed, or that was crossed as the binding went, is still journaled; a sandbox no run owns leaves memory once gone and idle; the cap is kept at the crossing chunk; its refusal says in plain words not to retry (`backend/python/tests/unit/harness_core/sandbox/egress/test_r11_egress_volume.py` R11-EGV-1..8). Control-plane web fetch and search (`fetch_url`, `web_search`, G-23) are not sandbox egress and are not metered here; an engine's call of them counts only as its tool-gateway bytes. WP-61 (decisions 1-2): the cap, the threshold alerts and the org baseline judge outside traffic only; the Model and Tool Gateways' bytes are counted for the metrics (`scope` label `gateway`/`outside`) and never cut; a capped run's next Model Gateway call is marked `X-Egress-Capped` and refused `egress_volume_exceeded` (403, every wire), which all four engines report and the run fails with, worded in 10 locales: `backend/python/tests/unit/harness_core/sandbox/egress/test_wp61_egress_scope.py` (R11D-EGS-1..5), `backend/python/tests/unit/telemetry/modules/test_wp45_egress_metrics.py` (R11D-EGS-6), `backend/python/tests/unit/harness_core/gateway/test_wp61_egress_volume_refusal.py` (R11D-EVX-1..3), `backend/python/tests/unit/harness_engines/test_wp61_egress_volume_engines.py` (R11D-EVE-1..4), `backend/python/tests/unit/harness_core/engines/durable/test_wp61_egress_volume_run_end.py` (R11D-EVK-1), F `chat/__tests__/harness-run-errors.test.ts`, `wp18-ux06-next-actions.test.ts`, N `run-error-codes-vector.test.ts`.

### 6.3 Credentials

- [x] **P0** No secrets in the sandbox, enforced by a canary scan across all phases (SEC-CR-01, OWD-1). — ✅ `backend/python/tests/cohesion/test_c13_sandbox_reaches_only_gateways.py` (`pxh-sandbox:x1` rebuilt, WP-06), `backend/python/tests/unit/harness/engines/test_recs_audit_real_engine.py`; legacy `run_code` and MCP runners hold placeholders only (WP-06)
- [x] **P0** The session credential is bound to the sandbox identity, short-lived, and revoked on terminate, suspend or kill switch (SEC-CR-02, -03). — ✅ `backend/python/tests/unit/harness_core/sandbox/egress/test_wp14_revocation.py`, `backend/python/tests/unit/harness/sandbox_broker/test_wp14_kill_switch_docker.py`, `backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` (`test_sec_cr_03_*`) (WP-14)
- [ ] **P0** Snapshots are taken only after the setup-secrets wipe (SEC-CR-04).
- [ ] **P0** PATs and tokens are never logged (SEC-CR-06, SEC-LG-01).
- [ ] **P1** Rotating credentials (model keys, git, proxy CA) requires no sandbox restart (OP-08).

### 6.4 Authorization and tenancy

- [ ] **P0** The principal is derived on the server side. Org and user IDs in tool arguments are never trusted (TG-02, SEC-AZ-03).
- [ ] **P0** Users cannot reach another user's or another org's sessions, artifacts, approvals or triggers (SEC-AZ-01, -02).
- [x] **P0** A shared agent runs with each user's own credentials and ACLs. Service-account agents are labeled as such (SEC-AZ-04). — ✅ `backend/nodejs/apps/tests/modules/agent-harness/owd2-run-attribution.test.ts`, `frontend/app/(main)/chat/components/__tests__/agent-chat-header.test.tsx`
- [ ] **P0** Cache keys include the org and the ACL scope (SEC-AZ-05).
- [ ] **P0** Every new route declares `AUTH_POLICY_ATTR` / `require_scopes`, and the route-inventory test passes (AD-07).
- [x] **P0** IDs are unguessable, and authorization is checked on every fetch (SEC-AZ-07). — ✅ 1d61d34593 (`backend/nodejs/apps/tests/modules/agent-harness/secaz07-idor-sweep.mongo.itest.ts`, `backend/python/tests/unit/connectors/api/test_secaz07_artifact_idor_sweep.py`)
- [x] **P1** Deactivating a user cleans up that user's sessions, triggers and tokens (SEC-AZ-08). — ✅ WP-73 (`backend/nodejs/apps/tests/modules/agent-harness/principal-deactivation.test.ts`, `backend/nodejs/apps/tests/modules/user_management/routes/users.permissions.test.ts` DEACT-7..10)

### 6.5 Prompt injection and the lethal trifecta

- [ ] **P0** Taint labels (`private_data_read`, `untrusted_content_read`, `external_comm_capable`) are set by the gateways, not by the model.
- [ ] **P0** The Rule of Two is enforced on external-effect actions (SEC-PI-01, -08).
- [x] **P0** The UI never auto-fetches remote images, and URLs that carry query data are sanitized (SEC-EG-09, UX-04). — ✅ 1d61d34593 (`frontend/app/(main)/chat/components/message-area/__tests__/sec-eg-09-link-data.test.tsx`)
- [x] **P0** Agent text is visually distinct from system and approval UI. Citations are resolved on the server side only (SEC-PI-04). — ✅ 1d61d34593 (`frontend/app/(main)/chat/components/message-area/__tests__/sec-pi-04-agent-text-isolation.test.tsx`)
- [ ] **P1** Prompt-injection cases (AgentDojo-style) show attack success = 0 on consequential actions (SEC-PI cases, §3.5 of test-scenarios.md).
- [ ] **P1** Canary facts are planted in restricted docs and never leak (EV-SAF-03). — ◐ WP-41: the scripted lane passes (`backend/python/tests/unit/harness/security/test_ev_saf_03_acl_canary.py`, `tests/unit/harness/security/acl_canary.py`, tenant `tests/fixtures/tenant/canary.yaml`)

### 6.6 Supply chain

- [ ] **P1** Harness images ship with an SBOM and a signature that is verified at use (SEC-SC-01).
- [ ] **P0** Harness auto-update and phone-home are disabled and blocked (SEC-SC-02, CF-17).
- [ ] **P1** Packages install only from vetted mirrors, with the typosquat policy from `package_policy.py` (SEC-SC-03).
- [x] **P1** Skills and MCP servers come from a signed or approved registry, and manifests are pinned (SEC-SC-04, -05).
- [x] **P1** A response process for harness CVEs: a version kill switch plus a fast-track upgrade through conformance (J-19). — ✅ WP-86 (ADR-OPS-01): the operator stops and their enforcement (`backend/nodejs/apps/tests/modules/agent-harness/ops-operator-stops.test.ts`), CC1 candidate gate; this build's stop is the instance kill switch, and `HARNESS_ENGINE_RUNS=false` stops every engine
- [ ] **P1** Every install of PipesHub's Python and Node dependencies (Dockerfiles, CI, the dev setup) takes only what a committed lock names and checks each file's hash: hashed Python locks that agree with `pyproject.toml`, `npm ci` against locks with an integrity hash for every package, a release denylist, base images pinned by digest (SEC-SC-07..11). — ◐ WP-75: the rules and the locks pass (`backend/python/tests/cohesion/test_sec_sc_supply_chain.py`, `scripts/test_supply_chain.py`; guide [SUPPLY-CHAIN.md](guides/SUPPLY-CHAIN.md)); left: rebuilding the images on the locks (after credential rotation and signing) and pinning the tag-only images listed in `contracts/supply-chain/image-pins.json`

### 6.7 Filesystem, logs and abuse

- [x] **P0** The outputs collector rejects symlinks, path traversal, hardlinks and device files (SEC-FS-01, -02, -05). — ✅ C6.36 in the project tracker; WP-69 SEC-FS-02: one name check per language with shared vectors (`contracts/harness/vectors/safe-names.json`), `backend/python/tests/unit/harness_core/workspace/test_sec_fs_02_names.py`, `backend/nodejs/apps/tests/modules/agent-harness/sec-fs-02-names.test.ts`, Docker hostile-engine test in `test_real_controller.py`
- [x] **P0** Risky output types are served with a safe content-type and CSP (SEC-FS-04). — ✅ 1d61d34593 (`backend/nodejs/apps/tests/libs/utils/secfs04-untrusted-content.test.ts`, `backend/python/tests/unit/utils/test_secfs04_untrusted_content.py`)
- [x] **P0** Transcript access follows the session ACL. Admin access to transcripts is itself audited (SEC-LG-02). — ✅ 1d61d34593 (`backend/nodejs/apps/tests/modules/agent-harness/seclg02-transcript-read-audit.test.ts`, `backend/nodejs/apps/tests/modules/agent-harness/secaz07-idor-sweep.mongo.itest.ts`)
- [x] **P0** Per-user and per-org concurrency caps, size limits and event-flood throttles (SEC-DOS-01..03). — ✅ flood: `backend/python/tests/unit/harness_core/engines/durable/test_wp03_kernel.py`; caps and size limits as audited (the project tracker C6.39)
- [x] **P1** Zip-bomb and huge-file limits (SEC-FS-03). Slowloris protection (SEC-DOS-04). — ✅ DOS-LIMITS: `backend/python/tests/unit/harness_core/test_archive_limits.py`, `backend/python/tests/unit/harness/test_c6_40_broker_archive.py`; `backend/python/tests/unit/harness_core/test_http_limits.py`, `backend/python/tests/unit/harness/test_c6_40_listener_read_limits.py`, `backend/nodejs/apps/tests/modules/agent-harness/read-limits.test.ts`

### 6.8 Security process

- [x] **P1** Threat model is reviewed at every phase gate. A quarterly external red-team. — ✅ WP-74: [guides/threat-model.md](./guides/threat-model.md) with its review record, gate step in the delivery process §6.1 (`backend/python/tests/cohesion/test_v2_guides.py`); the external red team is QA-28 (pen test, people), after the PR
- [ ] **P1** The security suite plus canary scans gate every release.
- [x] **P1** An incident runbook: kill switch, credential rotation, forensic export of session events and egress logs. — ✅ WP-87: [guides/runbooks.md](./guides/runbooks.md) "Incident response" (`backend/python/tests/cohesion/test_wp87_operator_docs.py`)

---

## 7. Performance and latency

- [x] **P0** Session ready from a warm pool: p50 ≤ 2 s, p95 ≤ 5 s (PF-01).
- [ ] **P0** Cold session ready: p95 ≤ 30 s, with a breakdown by stage (PF-02).
- [ ] **P0** First event after a message: p95 ≤ 1.5 s, excluding model time to first token (PF-04).
- [ ] **P0** Tool Gateway overhead: p50 ≤ 35 ms and p95 ≤ 100 ms at 40 calls/s per server (PF-05). — ◐ WP-77: the mid-load stall (a gen-2 GC over the worker's startup heap) fixed by freezing it after start; 2 of 3 runs pass, p50 34.0 / 35.2 / 34.2 ms (see C7.04)
- [x] **P0** Model Gateway adds ≤ 50 ms at p95 to time to first token, with no stream buffering (PF-07).
- [x] **P1** Resume from a snapshot: p95 ≤ 10 s (PF-03). — ✅ WP-72: `backend/python/tests/unit/harness/engines/test_pf03_resume_e2e.py` (cold p95 3.6 s, n = 20)
- [ ] **P1** Event lag from sandbox to browser: p95 ≤ 300 ms (PF-08).
- [x] **P1** Parallel tool calls take about as long as the slowest one, not the sum (PF-18). — ✅ WP-72: `backend/python/tests/unit/harness_core/durable/test_pf18_parallel_reads_wall_time.py`, `test_tg15_parallel_calls.py::test_pf18_g_*`
- [ ] **P1** Capacity for the reference Compose host is published (PF-12).
- [ ] **P1** Helm scale-out shows no hot partitions (PF-13).
- [ ] **P1** 24 h soak with no leaks (PF-14).
- [ ] **P1** Burst of 0 → 200 sessions handled (PF-15).
- [x] **P1** UI stays responsive with 10k events or 10 MB of terminal output (PF-17). — ✅ WP-76: `frontend/tests/e2e/perf/chat-large-run.perf.spec.ts` PF-17 (input latency < 100 ms), `frontend/app/(main)/chat/__tests__/pf17-activity-batching.test.ts`, `ev15-virtual-rows.test.tsx` AP-1/AP-2
- [ ] **P1** Idempotent connector reads are cached per tenant and ACL scope.

---

## 8. Reliability and resilience

- [x] **P0** Tool Gateway, Model Gateway and egress proxy restarts are survivable (CH-03, -04, -05). — ✅ `backend/python/tests/unit/harness/engines/test_p9_fault_matrix.py` FM-3/FM-4/FM-5 on the release head (WP-20, 14/14 rows)
- [ ] **P0** Duplicate client messages result in exactly one turn (CH-17).
- [x] **P1** Sandbox death, orchestrator death and network partition are recoverable with zero lost events (CH-01, -02, -12). — ✅ WP-27: `backend/python/tests/unit/harness_core/engines/durable/test_crash_points.py` (ek14 worker killed at each boundary, ek15 engine vanished, ek19 lost lease: no duplicates), CH-12 FM-11 in `test_p9_fault_matrix.py` (WP-20); the person is told when a run was carried on after a dead attempt (`x.pipeshub.run.notice` `resumed`: `backend/nodejs/apps/tests/modules/agent-harness/support/execution-store-contract/claims.ts` "CH-01 …", `frontend/app/(main)/chat/components/message-area/__tests__/ch01-resumed-notice.test.tsx`); CH-02 is re-drive from the checkpoint, not re-attach (ADR-OWD-11)
- [x] **P1** Store outages are survivable: Mongo, KV, broker, blob (CH-06..09). — ✅ WP-27: blob outage under an engine run waits (`store_wait`) and resumes (`backend/python/tests/unit/harness_core/engines/durable/test_ch09_store_outage.py`); a Mongo failover is a retryable 503 (`backend/nodejs/apps/tests/modules/agent-harness/ch06-store-transient.test.ts`); broker outage buffered and retried (`outbox.dispatcher.test.ts`, `schedule-firer.test.ts`, `test_run_claimer.py::test_o5_*`); an admin sees each store's state (`GET /api/v1/agent-harness/admin/dependencies`, `ch07-dependency-health.test.ts`); fault stack `test_store_outage.py` 2/2 on runc. The degraded-dependency rows IX-DD-2/3/4/16 run in CI's `harness-fault.yml` (decided 2026-10-30), P9 FM-1/7/8 on the docker lane
- [x] **P1** Malformed connector or model output does not crash anything or cause loops (CH-10, -11). — ✅ CH-10: `backend/python/tests/unit/agents/actions/test_clickup_toolset.py::TestEveryToolFailsHonestly`, Jira's wrong-shaped pages; CH-11 (WP-27): one corrective error on both loops, the tool never runs, and repeated malformed calls end with an answer (`backend/python/tests/harness_replay/test_ch11_malformed_loop.py`, `tests/unit/agent_loop_lib/agent/test_tool_loop_malformed_calls.py`)
- [x] **P1** Rolling deploys with active sessions (CH-15, UP-06). — ✅ WP-27 run on runc: `integration-tests/harness/fault/test_upgrades.py` (ru4a rolling deploy under continuous admission, two Nodes behind a balancer and two workers replaced one at a time, no stream stalled past the bound; ru5a; c13) 3/3; drain and handoff `test_crash_points::test_ek18_*`, `test_barrier_cancel_yield_fence.py::test_k8_*`, `test_run_claimer.py::test_ru1*`
- [x] **P1** Sandbox provider errors are retried, with fallback to another provider if configured (CH-16). — ✅ WP-27: `FailoverSandboxBroker` retries a failing create with backoff, then the next configured provider (`sandboxBroker.fallbackEndpoint`); refusals are never retried (`backend/python/tests/unit/harness_core/sandbox/test_ch16_provider_failover.py`, `tests/unit/harness/test_ch16_engine_broker.py`); all providers away: the run waits visibly (`test_sandbox_wait.py`, QA31F)
- [x] **P1** Stall and runaway-loop detection pauses the session with an explanation (BU-06). — ✅ as amended (C8.08, ADR-OWD-05): the turn ends with an answer from what it gathered, flagged `truncatedBy: stall`, and the person continues. WP-27: `backend/python/tests/harness_replay/test_bu06_stall.py` (both loops), `tests/unit/harness_core/engines/durable/test_bu06_engine_stall.py` (engines), notice `budgetend-cut-short.test.tsx`, Slack `cut-short.test.ts`; WP-62: an engine's round is one model turn (weighed after the model read it) and a spent web allowance is no stall, `test_wp62_engine_rounds.py` (WP62-STALL-1..4); WP-70: an engine's step cap is a runaway backstop scaled to its USD budget (100 steps a dollar, within the profile; release `maxSteps` 1000), the stall detector stays the no-progress stop, and every limit keeps a writing reserve (the engine is told to stop gathering and write; a cut turn's wrap-up writes the deliverable), `test_wp70_allowance.py`, `test_wp70_deepfinish.py`, replay `test_wp70_deep_research_replay.py` (WP70-STEPS, -SYNTH, -OWED, -E2E)

---

## 9. Observability, cost and audit

- [x] **P0** Usage events per model call, reconciled with the gateway ledger (EV-10). — ✅ 7a8f10a5df: `backend/python/tests/unit/harness_core/audit/test_grant_auditor.py::test_c901_*`, `tests/unit/harness/audit/test_node_evidence.py::test_au8_*`, `tests/unit/harness_core/engines/durable/test_engbudget_wrap_up.py::test_c901_*`, `backend/nodejs/apps/tests/modules/agent-harness/ev10-audit-usage.test.ts`, LV-14 in `contracts/harness/vectors/budget-ledger.json`
- [x] **P0** Append-only audit records for every tool call, approval and egress decision (OB-03), emitted by the gateways rather than by the harness. — ✅ 7a8f10a5df: `backend/python/tests/unit/harness_core/sandbox/egress/test_sec2_refusal_reports.py::test_ob03_*`, `harness_core/testing/contracts/refusal_sink.py` RS-5/RS-6, `backend/nodejs/apps/tests/modules/agent-harness/gateway-refusals.test.ts` (OB-03), `audit-chain.test.ts` (OB-03)
- [x] **P1** OTel GenAI spans, with trace IDs propagated from Node → harness service → gateways → sandbox (OB-01): — ✅ WP-72: `backend/python/tests/unit/harness_core/telemetry/test_ob01_genai_spans.py`
  - `invoke_agent`
  - `execute_tool`
  - model calls, including cache-token counts
- [x] **P1** Metrics and dashboards (OB-04): — ✅ WP-72: `backend/nodejs/apps/tests/modules/agent-harness/ob04-ops-metrics.test.ts`, `backend/python/tests/cohesion/test_ob04_monitoring.py`; `deployment/monitoring/`
  - active sessions
  - pool depth
  - claim latency
  - gateway errors — WP-47 (OB-08), round 11 (OB-10); dashboard and alert WP-72
  - cache ratio
  - cost per minute
- [x] **P1** Stuck-session detection with alerting (OB-05). — ✅ WP-72: `deployment/monitoring/prometheus/agent-harness.rules.yml`, `test_ob04_monitoring.py::test_ob05_*`
- [x] **P1** A cost ledger per session, user, agent and org; budgets (BU-03, BU-05). — ✅ The run's own budget and cost so far come from the budget ledger; spending limits beyond the run and their records are an extension point with no default (`SharedBudgetGuard`, `CostEraser`, a cost entry writer, a policy section rule, the `AgentRuntimeExtras` slot): `backend/nodejs/apps/tests/modules/agent-harness/shared-budget-ports.test.ts`, policy-section-rules.test.ts (moved out of this repository), `backend/python/tests/unit/harness_core/budget/test_bu05_cache_pricing.py`. WP-66: a call the gateway cut short is charged a best estimate, labelled "estimated (stopped early)" on the run cost line and the run summary, while the run's budget keeps its upper bound held until the provider's usage replaces it or the hold's time passes (`backend/python/tests/unit/harness_core/gateway/test_wp66_honest_meter.py`).
- [x] **P1** Sandbox-seconds metering accurate to within 1% (BU-04). — ✅ WP-72: `backend/python/tests/unit/harness_core/sandbox/test_bu04_sandbox_seconds.py`, `backend/python/tests/unit/harness/sandbox_broker/test_bu04_sandbox_seconds_docker.py`
- [x] **P1** Audit records are tamper-evident (hash chain or WORM storage), searchable and exportable (J-18).
- [ ] **P2** Ingest harness OTel (OB-02), trace replay (OB-06), and a redacted support bundle (OB-07).

---

## 10. User experience

- [x] **P0** The session view renders every canonical event type (UX-01). — ✅ `frontend/app/(main)/chat/__tests__/wp18-ux01-stream-events.test.ts`, `backend/nodejs/apps/tests/modules/agent-harness/projection/wp18-ui-stream-events.test.ts`
- [x] **P0** Every typed error has a human-readable message and a next action (UX-06). — ✅ the `nextAction` field per code is in every catalogue and vector and enforced (`tests/cohesion/test_wp18_ux06_next_actions.py`, `wp18-ux06-next-actions.test.ts`); UI-2: the chat's words for the 5 codes and a one-click next step (`wp18-error-next-step.test.tsx`, browser WP18-01..06)
- [ ] **P0** Stop, approve and answer-question controls are always reachable while a session runs. — ◐ WP-71: the "+" menu stays inside the window at 1280×720, 1280×560, 1440×900 and 390 px and scrolls, so Attach files is reachable (`chat-plus-menu-viewport.spec.ts` PM-1); Stop sends the run's own cancel route and the run ends cancelled (`em-durable-turn.spec.ts` EM-13); every "waiting for you" count is the calls waiting, the number "Allow all N" decides (WP71-COUNT). Left: the row as a whole (steer and approve under load, a phone sweep of every control).
- [ ] **P1** Controls follow harness capabilities (steer, fork, reasoning), with a tooltip when one is unavailable (UX-02).
- [ ] **P1** A todo/plan panel, elapsed time, live cost meter and current step (UX-05).
- [ ] **P1** File tree, diffs and preview of outputs. Read-only terminal view.
- [x] **P1** A "Runtime" node in the agent builder: harness profile, model, egress, budgets and schedule, validated before save (UX-07).
- [ ] **P1** The builder blocks invalid harness × model combinations, for example Codex with a model that only offers the Chat Completions API (§7 of test-scenarios).
- [ ] **P1** Strings go through i18n, and naming follows `frontend/CLAUDE.md` (UX-09, UX-10).
- [ ] **P2** Accessibility: keyboard approvals, screen reader support, contrast (UX-08). Mobile approvals (J-27).

---

## 11. Admin and governance

- [x] **P0** A harness catalog: enable or disable each harness and version. Allowed models per harness are enforced by the gateway (AD-01, AD-02). — ✅ 66dd42d70d (`backend/nodejs/apps/tests/modules/agent-harness/up01-engine-versions.test.ts` AD-01, `test_ad02_engine_models.py`, `engine-profile-editor.test.tsx` AD-01)
- [ ] **P0** Org feature flag. When it is off, routes return 403/404 and no background work runs (AD-08).
- [ ] **P0** RBAC for creating harness agents, approving actions and viewing other people's sessions (AD-07).
- [x] **P1** Egress allowlist editor with validation and audit (AD-03). — ✅ WP-63 (section, validation, audited If-Match writes, agent narrowing and the screens) and WP-90 (every change one `config.changed` entry): wp63-websites-section.http.test.ts (moved out of this repository), wp90-admin-ui.http.test.ts (moved out of this repository), allowed-websites-page.test.tsx (moved out of this repository) (C11.04).
- [x] **P1** MCP allowlist with manifest diff review (AD-04). Skills signing and approval (AD-05).
- [x] **P1** Kill switches at four levels: session, org, harness version and global egress. Each takes effect in 60 s or less (AD-06, J-19). — ✅ WP-86 (ADR-OPS-01): one control per scope, enforced through `OperatorStops`; the 60 s bound gated (`backend/nodejs/apps/tests/modules/agent-harness/ops-operator-stops.test.ts` OPS-02, `backend/python/tests/unit/harness_core/sandbox/egress/test_ops04_egress_stops.py`)
- [x] **P1** Usage and cost reports per org, user and agent, exportable. — ✅ Each run's spend in its timeline and `/usage` per conversation (`backend/nodejs/apps/tests/modules/agent-harness/slash2-session-usage.test.ts`); reports across an organization are an extension point on the admin and agent routers, none in this build (`backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts`).
- [x] **P1** Audit search UI (J-18).

---

## 12. API and integrations

- [x] **P0** Routes under `/api/v1/harness/*` are proxied by Node and kept in sync with `pipeshub-openapi.yaml` (API-01, OWD-13). — ✅ `backend/nodejs/apps/tests/cohesion/openapi-harness-routes.test.ts` (WP-19: route-vs-OpenAPI drift test in both directions, methods and path parameters, routes taken from `app.ts`'s own wiring); routes are the existing chat API as amended in [ADR-OWD-13](./adr/ADR-OWD-13-public-api-naming.md))
- [ ] **P0** Least-privilege scopes for personal access tokens (PATs) and OAuth (API-02). Idempotency keys (API-03).
- [ ] **P1** A non-streaming variant with the same error contract (API-04). Rate limits that return `Retry-After` (API-05).
- [ ] **P1** CI integrators get parity with the UI (J-22).
- [ ] **P2** Harness agents exposed through `/mcp` `pipeshub_agents` (API-06). A2A Agent Cards (API-07).

---

## 13. Compatibility

- [ ] **P0** MVP matrix: — ◐ WP-20: Neo4j + Qdrant store lane (`backend/python/tests/integration/store_lane.py`) run on the release head; left: runsc (⏸ gVisor/Kata deferred to the end), one live call per model family (spend)
  - Claude Code plus one other harness
  - Anthropic and OpenAI model families
  - Compose with Docker + gVisor
  - Neo4j + Qdrant
- [ ] **P1** Run the pairwise matrix nightly and the full matrix before each release (test-scenarios §7).
- [x] **P1** Store known incompatibilities as data in harness profiles. Examples: — ✅ WP-74: engine model APIs as adapter capability data; model-endpoint rows in `backend/python/harness_core/models/capabilities.py`, read by the prompt-cache policy and the LLM health check (`backend/python/tests/unit/harness_core/models/test_c13_03_model_capabilities.py`); Cursor and Amp have no adapter yet
  - Codex supports the Responses API only.
  - Cursor and Amp run on hosted models only.
  - DeepSeek's Anthropic-compatible endpoint has no `cache_control` and no image input.
- [ ] **P1** Open-model support:
  - Pin the vLLM version, chat template and tool-call parser for each model (Qwen3-Coder, Kimi K2, GLM).
  - Each model passes conformance.
- [ ] **P1** All graph backends (Neo4j, Arango), vector backends (Qdrant, OpenSearch, Redis), KV stores, brokers and blob stores pass the knowledge and session suites.

---

## 14. Upgrades and versioning

- [x] **P0** Old and new harness versions run side by side. A session stays pinned to the version it started with (UP-01). — ✅ 66dd42d70d (`backend/nodejs/apps/tests/modules/agent-harness/up01-engine-versions.test.ts` UP-01, `test_release_registry.py::test_up01_*`, `test_engine_image.py::test_up01_*`)
- [x] **P1** Retiring a harness version is a defined flow (UP-02). Event schema, `/pipeshub` contract and tool schemas all evolve additively (UP-03..05). — ✅ WP-86 and WP-99 (ADR-OPS-01 D6): retire suspends the version's runs in place and resumes them when allowed (`backend/python/tests/unit/harness_core/engines/durable/test_ops14_retired_round_trip.py` OPS-14); WP-99: a retired version's runs resume on the successor the release declares (the engine kernel moves the pinned profile, `backend/python/tests/unit/harness_core/engines/durable/test_wp99_budget_kernel.py` WP99-SUCC)
- [ ] **P1** Each harness upgrade goes through a pipeline:
  1. Nightly conformance run against `latest`.
  2. Promotion to the catalog.
  3. Org opt-in.
  4. Default flip.
- [ ] **P2** Canonical events can be regenerated from stored native transcripts (UP-07).

---

## 15. Deployment and operations

- [ ] **P0** The `install.sh` preflight checks the sandbox runtime and gives clear remediation (OP-01, OP-02).
- [x] **P0** The new service, its port, and the internal-only Tool Gateway are documented in `AGENTS.md` / `CLAUDE.md` (OP-09). — ✅ AGENTS.md "What lives where" → Agent harness; `backend/python/tests/cohesion/test_v2_agents_md_harness_services.py`
- [ ] **P1** Helm chart (OP-03):
  - CRDs
  - RuntimeClass
  - default-deny NetworkPolicies
  - dashboards and alerts
- [ ] **P1** Air-gapped installs (OP-04):
  - private registry
  - local models
  - mirrored skills and images
- [ ] **P1** Published sizing guide (OP-05). Backup and restore (OP-06).
- [x] **P1** Runbooks for (OP-07): — ✅ WP-87: [guides/runbooks.md](./guides/runbooks.md) (`backend/python/tests/cohesion/test_wp87_operator_docs.py`)
  - a stuck session
  - an exhausted pool
  - a gateway 5xx spike
  - a harness CVE
  - credential rotation
  - a pending-approval backlog

---

## 16. Compliance and data governance

- [x] **P1** Every stored object carries `org_id` and region. Per-tenant encryption keys for transcripts and snapshots (OWD-12). — ✅ WP-23: region stamped on every harness Mongo document, a worker handed only runs of its own region (`backend/nodejs/apps/tests/modules/agent-harness/wp23-region.test.ts` RES-1, RES-3, RES-4, `wp23-region.mongo.itest.ts`, `backend/python/tests/unit/harness_core/sandbox/test_wp23_region_placement.py`). A process's region is `default` unless an extension point names it (`HarnessEdition.region`, Python `RegionPort`); where an org's runs are placed is an extension point with no default in this build (`RunPlacement`; unbound: every run is placed here), `backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts`, `backend/python/tests/unit/harness/policy/test_placement_ports.py` RSP-1
- [ ] **P1** Retention and deletion apply across Mongo, blob, snapshots and caches, and can be verified (SEC-LG-04, SL-13).
- [x] **P1** A GDPR/DSR path: delete one user's sessions, artifacts and memory. — ✅ user deletion's erasure (CLEANUP-G2)
- [x] **P1** A vendor DPA checklist for any SaaS sandbox or model provider that is enabled. — ✅ WP-46: [guides/vendor-dpa-checklist.md](./guides/vendor-dpa-checklist.md) (`backend/python/tests/cohesion/test_v2_guides.py`); the **Legal** boxes are for legal to fill per vendor
- [ ] **P2** Map controls to SOC 2 / ISO 27001 evidence: access reviews for approvers, change management for manifests and harness versions.

---

## 17. Quality gates

- [ ] **P0** CI runs: unit tests, integration with a stub model, recorded-model end-to-end journeys, the conformance suite, and the security suite with canary scans. — ◐ WP-71: the browser lane (`deployment/e2e/run.sh browser`: a throwaway stack, the harness and authenticated Playwright projects on the scripted model, torn down) is the opt-in `e2e` group of `scripts/verify.sh` (`PXH_VERIFY_E2E=1`) and a patch for the merge gate; its JUnit check is H `tests/unit/harness/test_wp71_browser_lane.py`.
- [ ] **P1** A golden-trace loop: every incident becomes a regression case before its fix ships (EV-OPS-01).

---

## 18. Documentation

- [x] **P0** ADRs for every one-way door. — ✅ [adr/](./adr/) ADR-OWD-01…19; `backend/python/tests/cohesion/test_v2_spec_trackers.py`
- [ ] **P0** OpenAPI spec for all new routes.
- [x] **P1** Admin guide: enabling harnesses, egress, budgets, kill switches, audit. — ✅ WP-74: [guides/admin-guide.md](./guides/admin-guide.md) (`backend/python/tests/cohesion/test_v2_guides.py`)
- [x] **P1** Builder guide: — ✅ WP-46: [guides/builder-guide.md](./guides/builder-guide.md) (`backend/python/tests/cohesion/test_v2_guides.py`)
  - choosing a harness and model
  - choosing a tool-disclosure mode
  - writing skills
- [x] **P1** User guide: sessions, approvals, artifacts, git. — ✅ WP-46: [guides/user-guide.md](./guides/user-guide.md) (`backend/python/tests/cohesion/test_v2_guides.py`)
- [x] **P1** `/pipeshub` contract reference and `ph` CLI reference, both versioned. — ✅ WP-74: [guides/pipeshub-contract-reference.md](./guides/pipeshub-contract-reference.md), checked against `ph`'s parser, versions and exit codes (`backend/python/tests/cohesion/test_v2_guides.py`)
- [x] **P1** "Adding a harness adapter" guide plus a conformance kit. — ✅ WP-74: [guides/adding-a-harness-adapter.md](./guides/adding-a-harness-adapter.md) around `EngineAdapterContract` (`backend/python/tests/cohesion/test_v2_guides.py`)
- [x] **P1** Security whitepaper: isolation, egress, credentials, Rule of Two, audit. — ✅ WP-74: [guides/security-whitepaper.md](./guides/security-whitepaper.md) (`backend/python/tests/cohesion/test_v2_guides.py`)
- [x] **P1** Operator guide: sizing, runbooks, upgrades. — ✅ WP-87: [guides/operator-guide.md](./guides/operator-guide.md) (`backend/python/tests/cohesion/test_wp87_operator_docs.py`, `backend/python/tests/cohesion/test_v2_guides.py`)

---

## 19. Release readiness (per release)

- [ ] Every P0 item is checked (MVP), or every P0 and P1 item is checked (v1).
- [ ] Every enabled harness version passes conformance on the release matrix.
- [ ] Security suite passes. The red-team report has no open high-severity findings.
- [ ] Performance targets are met on reference hardware, and the numbers are in the release notes.
- [ ] Chaos suite is green in staging.
- [ ] Docs, OpenAPI and runbooks are published. The changelog entry includes the harness versions supported.

---

## 20. Additions from gap review

Details, rationale and acceptance criteria: [gaps-and-additions.md](./gaps-and-additions.md). Tests: GA-xx in [test-scenarios.md §13](./test-scenarios.md#13-additions-from-gap-review).

**Decisions**
- [x] **P0** ADRs for OWD-14 (agent versioning), OWD-15 (classification), OWD-16 (workspace sharing), OWD-17 (vendor logins), OWD-18 (write identity/disclosure), OWD-19 (multi-participant sessions). — ✅ [adr/](./adr/) ADR-OWD-14…19 (OWD-17 as amended); `backend/python/tests/cohesion/test_v2_spec_trackers.py`

**Lifecycle**
- [x] **P1** Immutable agent versions; sessions/triggers pin versions; rollback (G-01, GA-01). — ✅ WP-21: `backend/python/tests/unit/api/routes/test_ga01_agent_version_routes.py`, `backend/nodejs/apps/tests/modules/agent-harness/owd14-agent-versions.test.ts` (builder UI: mock awaiting owner review)
- [ ] **P2** Staged rollout/canary of agent and harness versions (G-02).
- [x] **P1** Shadow / dry-run mode with write tools stubbed in the gateway (G-03, GA-02). — ✅ WP-31: `backend/python/tests/unit/harness_core/gates/test_ga02_dry_run.py`, `backend/nodejs/apps/tests/modules/agent-harness/ga02-ga04-run-gates.test.ts`; WP-51 turn-on UI (composer "+" menu and chips): `frontend/app/(main)/chat/components/message-area/__tests__/wp51-run-gates-ui.test.tsx`
- [x] **P1** Plan-approval gate as a policy option (G-04, GA-03). — ✅ WP-31: `backend/python/tests/unit/harness_core/gates/test_ga03_plan_approval.py`; WP-51 plan card `frontend/app/(main)/chat/components/message-area/__tests__/wp51-run-gates-ui.test.tsx` and agent-level plan approval (builder, Python store, Node merge) `frontend/app/(main)/agents/agent-builder/__tests__/wp51-agent-safety-checks.test.tsx`, `backend/python/tests/unit/api/routes/test_wp51_agent_gates.py`, `backend/nodejs/apps/tests/modules/agent-harness/ga02-ga04-run-gates.test.ts`
- [x] **P1** Outcome verification gate (tests, citation text existence, file validity) (G-05, GA-04). — ✅ WP-31: `backend/python/tests/unit/harness_core/gates/test_ga04_verification.py`, `backend/python/tests/unit/harness/test_ga04_pipeshub_verify_ports.py`, `backend/nodejs/apps/tests/modules/agent-harness/ga02-ga04-run-gates.test.ts`; WP-51 verification panel `frontend/app/(main)/chat/components/message-area/__tests__/wp51-run-gates-ui.test.tsx` and agent-level checks `frontend/app/(main)/agents/agent-builder/__tests__/wp51-agent-safety-checks.test.tsx`, `backend/python/tests/unit/api/routes/test_wp51_agent_gates.py`
- [ ] **P2** Workspace rewind + compensating actions for external writes (G-06).
- [x] **P1** End-of-session summary attached to notifications (G-07, GA-05). — ✅ WP-33: `backend/nodejs/apps/tests/modules/agent-harness/projection/ga05-run-digest.test.ts`, `frontend/app/(main)/chat/components/__tests__/ga05-run-summary.test.tsx`
- [ ] **P2** Session export and local hand-off without secrets (G-08).

**Interaction**
- [ ] **P1** Session participant roles; principal immutable (G-09, GA-06).
- [ ] **P2** Human takeover terminal with hand-back diff (G-10).
- [ ] **P2** Authenticated, expiring preview URLs (G-11).
- [x] **P1** `auth_required` pause → connect → auto-retry (G-12, GA-07). — ✅ WP-33 backend (`backend/python/tests/unit/harness_core/durable/test_ga07_connect_pause.py`, `backend/nodejs/apps/tests/modules/agent-harness/projection/ga07-connect-wait.test.ts`); WP-51 connect card and `connect_pauses` offered: `frontend/app/(main)/chat/components/message-area/__tests__/ga07-connect-card.test.tsx`, `backend/nodejs/apps/tests/modules/agent-harness/projection/ga07-connect-wait.test.ts`; WP-64: a sign-in the vendor rejects mid-run pauses for a reconnect instead of failing the run, shown in the chat, inbox, link and My Actions (WP64-TS ✅)
- [x] **P1** Unified approvals/questions inbox with safe batch approve (G-13, GA-08). — ✅ the Inbox lists the approvals waiting on the viewer (`ApprovalQueueReach`, default the viewer's own runs; `approvals-queue.test.ts` AQ-0..AQ-9) and the questions their own runs asked (`question-inbox.test.ts` IQ-1..IQ-3); a batch decide across conversations is an extension point (`HarnessEdition.routers`). WP-64: an answer keeps the asking run's gates and spending cap (WP64-ANS, N `support/execution-store-contract/answers.ts`).
- [ ] **P2** Teams / email-to-agent surfaces (G-14).
- [ ] **P1** User feedback capture (G-15).
- [ ] **P2** Speculative pre-warm with per-user cost cap (G-16).

**Environment**
- [x] **P1** Environment definitions (image, setup script, devcontainer subset, env vars) + post-setup snapshot (G-17, GA-09). — ✅ WP-91 + WP-100; WP-132: the snapshot is an extension point (`EnvironmentSnapshotter`): `backend/python/tests/unit/harness_core/engines/test_wp100_environment_pin.py`, `backend/python/tests/unit/harness_core/engines/test_wp91_environments.py`, `backend/nodejs/apps/tests/modules/agent-harness/wp100-agent-environment.test.ts`
- [ ] **P2** Service sidecars with ephemeral credentials (G-18).
- [x] **P1** Read-only org caches populated by trusted jobs (G-19, GA-10, SEC-SC-06). — ✅ WP-91 + WP-100 built; WP-132: an extension point (`SharedCacheMounts`, `OrgReferencesProvider`), none in this build: `backend/python/tests/unit/harness_core/engines/test_cache_mounts.py`
- [x] **P1** Signed custom images + corporate CA injection (G-20, GA-11). — ✅ The trust roots and the broker's image check are extension points (`TrustRootsProvider`, `ImageTrustProvider`; defaults: the release's roots and its engine images), none for custom images in this build: `backend/python/tests/unit/harness/supply_chain_trust/test_wp129_supply_chain_ports.py`, `backend/python/tests/unit/harness/supply_chain_trust/test_wp85_image_trust.py`.
- [x] **P1** Upstream proxy chaining; private CIDRs default-deny; PipesHub internals always denied (G-21, G-38, SEC-EG-13, SEC-ISO-09). — ✅ `backend/python/tests/unit/harness_core/sandbox/egress/test_g21_upstream_egress.py`, docker `backend/python/tests/unit/harness/sandbox_broker/test_egress_upstream_docker.py` (runc); the `[gvisor]` SEC-ISO-09 lane stays with C20.39
- [ ] **P2** Persistent project workspaces per OWD-16 (G-22).
- [x] **P1** Safe web-research tier via control-plane fetch/search tool (G-23, GA-12). — ✅ `backend/python/tests/unit/web/test_ga12_web_research_tier.py`
- [ ] **P0** Built-in harness tool policy (allow/deny/replace) per profile, verified in conformance (G-24, GA-13).
- [x] **P1** Managed read-only policy hook in each harness that supports hooks (G-25, GA-14). — ✅ Claude Code and Codex `PreToolUse` → `ph policy check --hook` → Tool Gateway, fail closed; opencode and ACP held to the same `NativeCallPolicy` without one: `backend/python/tests/unit/harness_core/engines/test_ga14_native_policy.py`, `backend/python/tests/unit/harness_engines/test_ga14_policy_hooks.py`, `backend/python/tests/unit/harness_engines/pipeshub/test_sk05_ph_skill_and_policy.py::test_ga14_*`; D `backend/python/tests/unit/harness/engines/test_real_claude_code_conformance.py::test_ga14_c20_26_the_managed_hook_still_refuses_after_the_agent_tries_to_disable_it`, `backend/python/tests/unit/harness/engines/test_real_codex_conformance.py::test_ga14_c20_26_the_managed_hook_refuses_a_command_the_runs_policy_refuses` (WP-42)
- [ ] **P2** Harness extension assets (commands, subagents, plugins) as signed, versioned agent assets (G-26).
- [x] **P1** Role → model mapping (primary/fast/subagent/compaction) enforced by gateway (G-27, GA-15).
- [ ] **P2** Headless browser under egress policy (G-28).

**Data and knowledge**
- [x] **P1** Policy inputs beyond taint are an extension point (G-29, GA-16). — ✅ The shared parser (`harness_core/security/classification.py`, `classification.schema.json`): `backend/python/tests/unit/harness/policy/test_classification_ports.py` CL-1..CL-4. The extension point has no default in this build (Python `ClassificationPort`, Node `HarnessEdition.classification`, `RetentionPass`, `PolicySectionRule`; defaults: every model call passed through, the approval policy unchanged, nothing recorded or asked about, the section refused `CP_NOT_EXPOSED`): `backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts`, policy-section-rules.test.ts (moved out of this repository), `frontend/app/(main)/agents/components/__tests__/agent-transfer.test.tsx` AT-9
- [x] **P1** Model registry data-handling metadata (ZDR, region, training opt-out) used by policy (G-30). — ✅ A model's data handling is vocabulary of the classification schema (`modelHandling`); reading it is part of the classification extension point with no default in this build (`ClassificationPort`; default: no declared regions): `backend/python/tests/unit/harness/policy/test_classification_ports.py`
- [x] **P1** Shared per-(org, connector) rate budget between Tool Gateway and connector sync (G-31, GA-17). — ✅ WP-37: `app/services/rate_budget` (Redis token bucket per org and vendor, priority reserves and floors, one shared Retry-After pause); Slack and Notion sync and their agent toolsets: `backend/python/tests/unit/services/rate_budget/test_ga17_shared_budget_load.py`, `backend/python/tests/unit/services/rate_budget/test_wp37_rate_budget.py`, `backend/python/tests/unit/harness/test_wp37_rate_budget_wiring.py`; WP-38: Jira and Confluence Cloud, Google Drive and Gmail, Microsoft SharePoint/OneDrive and Outlook sync and toolsets: `backend/python/tests/unit/services/rate_budget/test_ga17_vendor_families_load.py`, `backend/python/tests/unit/harness/test_wp38_rate_budget_families_wiring.py`
- [ ] **P2** Read-after-write overlay for agent-created records (G-32, GA-18).
- [ ] **P2** Index agent artifacts as records with most-restrictive-source ACL (G-33).
- [ ] **P2** Search past sessions (ACL'd) (G-34).
- [x] **P1** Async bulk export with quotas (G-35, GA-19). — ✅ WP-40: `backend/python/tests/unit/harness_core/knowledge/test_ga19_bulk_export.py`, `backend/python/tests/unit/harness_core/gateway/test_ga19_export_through_the_gateway.py`, `backend/python/tests/unit/harness/test_wp40_knowledge_export_wiring.py`

**Security**
- [x] **P1** Knowledge reads and exports pass an extension point an edition can bind (G-36, SEC-AZ-09). — ✅ `backend/python/tests/unit/harness/test_wp40_knowledge_export_wiring.py` (BW-4), `backend/nodejs/apps/tests/modules/agent-harness/edition-journal-seams.test.ts`
- [x] **P1** Secret detection on user input with vault offer and redaction (G-37, SEC-CR-07). — ✅ WP-35: `backend/python/tests/unit/harness_core/security/test_sec_cr_07_pasted_secrets.py`, `backend/nodejs/apps/tests/modules/agent-harness/sec-cr-07-pasted-secrets.test.ts`, `frontend/app/(main)/chat/__tests__/pasted-secrets.test.ts`, `frontend/app/(main)/chat/__tests__/pasted-secret-dialog.test.tsx`
- [ ] **P0** Sandbox cannot reach any PipesHub internal datastore/service (G-38, SEC-ISO-09). — ◐ runc/Compose proven for every store and service port (`backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` `test_sec_iso_09_*`, WP-14); left: the `[gvisor]` variants and the Helm NetworkPolicy with P5.3, ⏸ gVisor/Kata deferred to the end (decided 2026-10-26)
- [ ] **P2** Content-safety guardrails at the Model Gateway (G-39).
- [ ] **P1** Fail-closed external policy webhook (G-40, GA-20). — not in this build: the worker asks an outside policy only through the `ExternalPolicyGate` extension point, whose built-in gate leaves the run's rules unchanged.
- [x] **P1** IdP group-based policy subjects (G-41, GA-21).
- [ ] **P1** Configured write identity + AI disclosure; bot-key commit signing outside sandbox (G-42, GA-22). — ◐ WP-23: the disclosure on every disclosed connector write, in the loop and the Tool Gateway (`backend/python/tests/unit/harness/test_wp23_write_identity.py`); WP-90: wording per action type; WP-132: the identity per action type is an extension point (`WriteIdentityPort`; this build writes as the user, `backend/python/tests/unit/harness/test_wp132_write_identity_port.py`); left: commit signing with a bot key (after the credential rotation, OB-1)
- [x] **P0** Vendor subscription login flows disabled and blocked (G-43, GA-23). — ✅ `backend/python/tests/unit/harness/security/test_ga23_vendor_logins_refused.py`, `backend/nodejs/apps/tests/modules/agent-harness/ga23-vendor-logins.test.ts` (OWD-17 as amended: Claude Code's own login admin-gated, off by default; every other login refused)
- [ ] **P2** Legal hold / eDiscovery overriding retention (G-44).

**Operations and quality**
- [x] **P1** Priority classes and preemption across claims, gateways and provider limits (G-45, GA-24). WP-48: the loop's own model calls and engine calls share one provider limit across processes; the real-stack SLO #20 profile is built, its run is the PERF lane's. WP-49: the tools' helpers' model calls are metered and admitted too. WP-61 (decision 3): a class raised mid-attempt is served at the run's next claim; the attempt keeps its claim's class for its own calls and preemption, and an engine's gateway calls follow at the grant's next read (60 s cache) (`support/execution-store-contract/priority.ts` R11D-PRI-1, `backend/python/tests/unit/harness/test_wp61_live_class.py` R11D-PRI-2..3; documented in `harness_core/priority.py`). WP-61 (decision 4): the provider limit is keyed per upstream (provider + account digest + endpoint/region digest), shared by the gateway and loop paths; two orgs on separate accounts never share a limit, two runs on one account do (`backend/python/tests/unit/harness/test_wp61_provider_gate_scope.py` R11D-PG-1..6).
- [ ] **P2** Warm-pool autoscaling with idle-cost reporting (G-46).
- [ ] **P1** Availability SLO, error budgets, RPO/RTO, DR drill (G-47). — ◐ WP-46: targets, error budget and RPO/RTO in [SLO.md](./SLO.md), drill procedure [DR-DRILL.md](./DR-DRILL.md) (`backend/python/tests/cohesion/test_v2_guides.py`). Left: run and record the drill (ops), availability dashboards. WP-47: gateway request metrics and the Tool Gateway `/healthz` (OB-08, OB-09)
- [x] **P1** Graceful degradation chain with visible notice (G-48, GA-25). — ✅ WP-89 + WP-100: `backend/python/tests/unit/harness/test_wp100_ga25_default.py`, `backend/python/tests/unit/harness_core/gateway/test_wp100_gateway_fallback.py`, `backend/nodejs/apps/tests/modules/agent-harness/wp100-model-fallback.test.ts`
- [ ] **P0** Mock harness adapter, gateway record/replay, seeded fixture tenant, ephemeral PR envs (G-49, GA-26). — ◐ WP-20: VCR gateway (`backend/python/harness_core/testing/cassette.py`), seeded multi-connector tenant (`backend/python/tests/support/seeded_tenant.py`), GA-26 offline P0-journey lane (`backend/python/tests/harness_replay/test_ga26_p0_journeys.py`, CI `harness-ga26.yml`) pass. Left: per-PR ephemeral environments (infra spend)
- [x] **P1** Local dev profile documented in CONTRIBUTING (G-50). — ✅ WP-74: `bash deployment/e2e/run.sh dev-up` (`dev-harness` profile, `deployment/e2e/docker-compose.dev-harness.yml`), CONTRIBUTING.md "Agent harness on a laptop"; Docker lane up and a scripted turn served (`backend/python/tests/cohesion/test_v2_guides.py`)
- [ ] **P2** Product analytics dashboard (G-51).
- [x] **P1** Timezone-aware schedules; date injected at context tail (G-52, GA-27). — ✅ WP-89 + WP-100: `backend/python/tests/unit/harness_core/engines/durable/test_wp100_engine_answer_language.py`, `backend/nodejs/apps/tests/modules/agent-harness/wp100-output-language.test.ts`
- [ ] **P2** Chargeback/showback dimensions in the cost ledger (G-54).

**Capability ideas**
- [ ] **P2** Cross-harness best-of-N / cross-review (G-55).
- [ ] **P2** Graduated autonomy within admin limits (G-56).
- [ ] **P2** Initializer → incremental long-horizon mode (G-57).
- [x] **P1** Quarantined summarizer for bulk untrusted content (G-58, GA-28). — ✅ WP-41: `knowledgegraph__summarize`; `backend/python/tests/unit/harness_core/context/test_ga28_quarantined_summarizer.py`, `backend/python/tests/unit/harness/knowledge/test_ga28_knowledge_summarize.py`, `backend/python/tests/unit/harness/security/test_ev_saf_03_acl_canary.py` (`test_ev_saf_03_through_the_quarantined_summarizer_*`)

