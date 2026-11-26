# PipesHub Agent Harness v2: Test Strategy

> Aligned with the implementation plan v2.0 (see §10.3a for work packages added by the end-to-end review).

**Status:** proposal · **Date:** 2026-09-26 · **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md) ·
**Supersedes for v2:** [../agent-harness-e2e-verification-plan.md](../agent-harness-e2e-verification-plan.md)
(v1's Gates 0–9 are kept and re-homed below; §10.3 maps them).

This document says what we test, at which layer, with which tools, and which suite gates each phase.
It extends the test infrastructure the repo already has. It does not add a parallel system. Every
new directory, fixture and workflow below is named so that implementation plans can cite it.

---

## 0. What already exists, and what we reuse

| Asset | Where | Reuse in v2 |
|---|---|---|
| Python unit suite (pytest, `asyncio_mode=auto`, `--timeout=30`, markers `integration`/`unit`/`slow`/`docker`) | `backend/python/pytest.ini`, `tests/unit/**` | All harness core and adapter unit tests. Add markers `property`, `fault`, `security`, `golden`, `quarantine` |
| Scripted LLM double | `tests/unit/agents/adapter/support/scripted_transport.py` (`ScriptedTransport`, `ScriptedStep`) | Promoted into the core test kit as `ScriptedModel` (§4.1) |
| Liskov contract-suite pattern ("skips live in fixtures, never in bodies") | `tests/unit/agent_loop_lib/sandbox/contract/{conftest,test_backend_contract}.py` | Template for every port contract suite (§3.1) |
| Fakes for Redis/Kafka | `tests/support/{fake_kafka,fake_redis_connection_provider,redis_provider_matrix}.py`; `fakeredis[lua]` dev dep | LiveBus/quota unit tests; the broker adapter is exercised for real in the contract lane |
| v1 harness unit tests | `tests/unit/harness/test_{checkpoint_store,virtual_fs,state_client,worker_runner,skill_bundles,...}.py` | Carried forward; `test_agent_executor.py` is deleted with the synthetic-HTTP executor (P1.5) |
| Backend matrix (one job per real backend, **any skip fails the job**, discovery by env var) | `.github/workflows/backend-matrix.yml`, `deployment/docker-compose/docker-compose.integration.{messaging,vector-db,graph-db,kv}.yml` | Harness contract suites join the matrix using the same discovery convention (§5) |
| Mongo replica-set + real admission fixture | `deployment/docker-compose/tests/agent_harness_replset_test.sh`, `docker-compose.agent-harness{,.test}.yml`, Node `tests/integration/agent-harness/mongo-admission.integration.test.ts` | Base of the harness integration stack |
| Real Docker sandbox contract + leak assertion | `.github/workflows/agent-harness-infrastructure.yml` (`sandbox-contract` job) | Tier 1 provider contract; the leak check is generalized to all tiers |
| Node mocha (parallel, 4 jobs, **global ioredis/kafkajs/MCP mocks**, c8 coverage gate 89/90/80/89) | `backend/nodejs/apps/.mocharc.yaml`, `tests/helpers/*` | Unit and in-memory contract tests. Real-infra Node tests must be `*.itest.ts` run with `mocha --no-config` (the pattern `storage-backends.yml` uses for `tests/stores/*.itest.ts`), because the global mocks would otherwise shadow real clients |
| v1 in-memory control-plane contract | `tests/modules/agent-harness/execution-store.contract.test.ts` | Re-parameterized over in-memory and Mongo (fixes REPORT E9) |
| Frontend vitest (jsdom, `app/**/*.test.{ts,tsx}`) | `frontend/vitest.config.ts`; `app/(main)/chat/__tests__/*`, `app/(main)/agents/__tests__/durable-api.test.ts` | Stream reducer, reconnect, approval card, transcript parity |
| Playwright (projects `setup`/`seed`/`authenticated`/`ai`/`unauthenticated`, `@smoke` tag, CI retries 2) | `frontend/playwright.config.ts`, `frontend/tests/e2e/**`, `tests/e2e/chat/agui-sse-builder.ts` | Harness browser journeys get a new `harness` project (§7.1) |
| Full-stack integration (Neo4j and Arango shards; `resilience`, `storage`, `security`, `permissions` markers; Playwright on the live stack) | `.github/workflows/integration-tests.yml`, `integration-tests/**` | Harness full-stack, fault and security suites live here |
| Fault switches (`/etc/hosts` provider blackhole, unwritable blob) | `integration-tests/helper/fault_switches.py`, `integration-tests/resilience/*` | Extended with failpoints, toxiproxy and process kills (§6) |
| `@pipeshub-ai/mcp` against a real server | `integration-tests/mcp_package/integration_test_mcp_package.py` | Run with the harness flag on and off (§3.3) |
| Perf harness (`bench_*.py`, `compare.py`, committed baselines) | `integration-tests/perf/`, `perf-{query,indexing,scale}.yml` | `bench_harness.py` plus baselines (§8.4) |
| Answer-quality evals (structural on PR, `test_cases_can_fail.py`) | `backend/python/tests/evals/`, `answer-quality-evals.yml` | Trajectory checks over the journal (§9) |
| Laptop / arm64 / Helm smokes | `published-hub-smoke.yml` (`eval` = 8 GB laptop install), `arm64-smoke.yml`, `helm-chart.yml` + `deployment/helm/tests/kind_smoke.sh` | Per-profile harness smoke (§8) |
| Upgrade smoke | `deployment/docker-compose/tests/{upgrade_smoke.sh,upgrade_seed.py}` | Upgrade with in-flight runs (§7.5) |
| OpenAPI lint | `openapi-validator.yml` (Spectral) | New `{base}/runs|events|interactions` routes, plus a route-vs-spec test |

**Missing today; added by this strategy:** `hypothesis` (Python property and stateful testing),
`fast-check` (Node and frontend), `mutmut` and `@stryker-mutator/*` (mutation testing, weekly only),
`toxiproxy` (compose service), `libfaketime` (test image layer), `hypothesis-jsonschema`,
`schemathesis` (nightly API fuzz), a fake LLM HTTP server, and a fake connector/MCP server with effect
recording. Nothing here adds a runtime env var to the product. Failpoints are configured from a file
(§6.1).

---

## 1. Principles and test pyramid

### 1.1 Principles

1. **The journal is the oracle.** Most harness assertions are statements about the event log:
   gap-free `seq`, one `model.response` per turn, `tool.intent` paired with at most one
   `tool.outcome`, no event committed under a stale fence. Tests assert on the journal first and on
   projections second. Projections are then checked against the journal (§3.2).
2. **A fake is a product.** Every in-memory adapter (journal, live bus, checkpoint, mount provider,
   sandbox provider, credential resolver) must pass the same port contract suite as the real adapters
   (§3.1). Unit tests use those adapters rather than `MagicMock` of our own ports. That is what stops
   in-memory and Mongo stores from drifting (REPORT E9, N04 §5.5 #8). `MagicMock` stays acceptable for
   third-party SDK edges such as the Docker SDK, E2B, or provider HTTP.
3. **Determinism by construction.** CI never calls a real model or SaaS. Time, randomness, IDs,
   scheduling interleavings and crash points are injectable: `ManualClock`, seeded ID factory,
   controlled scheduler, failpoints. A flaky harness test is treated as a bug report against the
   harness until proven otherwise.
4. **Skips live in fixtures, never in test bodies.** In a real-service lane any skip fails the job.
   This is the existing `backend-matrix.yml` rule, applied to every new harness lane.
5. **Every test must be able to fail.** This is the evals rule (`test_cases_can_fail.py`),
   generalized. Weekly mutation testing enforces it for the safety kernels (§2.14). Every security
   test ships with a "vulnerable twin" fixture that shows the assertion rejecting the bad behavior.
6. **Adversaries are scripted, not hoped for.** Security E2E scripts the *model* to attempt the
   attack. The test then checks that the harness blocks it, whatever the real model would have done.
7. **Durability claims need real persistence.** Plan ground rule 5 applies: nothing that claims
   crash-safety passes on in-memory adapters alone.
8. **Legacy is the reference.** Until P9, every user-facing contract (REPORT §8, N04 §7) is proven by
   diffing the v2 projection against the legacy path on the same recorded input.

### 1.2 Pyramid, budgets and allowed dependencies

| Layer | Target count at GA | Runtime budget | May use | May not use |
|---|---|---|---|---|
| **L0 static** | n/a | < 3 min | ruff, pyright, ESLint, tsc, `import-linter` (core has zero `app.*` imports, P1.11), schema codegen drift check, descriptor classification check (P2.1), determinism linter fixtures | n/a |
| **L1 unit** (Python core + adapters, Node, frontend) | ~2,500 Py, ~600 Node, ~250 FE | Py harness subset < 4 min on `-n auto`; Node < 3 min; FE < 2 min | Core test kit (§4), in-memory adapters, `fakeredis`, `sinon.useFakeTimers`, jsdom | network, Docker, real Mongo/Redis/broker, wall clock, `asyncio.sleep` > 0 without `ManualClock` |
| **L1p property / model-based** | ~150 properties, ~12 state machines | PR: `max_examples=200`, derandomized, < 4 min total. Nightly: 5,000 examples, random seed | Hypothesis (+ stateful), fast-check (`fc.commands`), in-memory adapters | Same as L1. Nightly also runs selected machines on real Mongo (L2) |
| **L2 contract** | ~25 port suites × every adapter; ~6 cross-language / wire suites | In-memory: part of L1. Real: < 12 min per matrix job | Real single backend per job via compose | More than one backend per job (a red job must name the backend) |
| **L3 integration** | ~300 | PR harness lane < 20 min | Real Mongo RS, Redis, one broker, local blob, fake LLM, fake MCP/connector, real Docker sandbox | Real SaaS, real LLM |
| **L4 fault / chaos** | ~120 crash-point scenarios + ~40 network/dependency scenarios | PR canary (12 scenarios) < 15 min; nightly full < 90 min; release ×100 repetitions | L3 plus failpoints, toxiproxy, `docker kill -s KILL/STOP`, libfaketime | retries that mask failures |
| **L5 E2E** (browser, Slack/MCP clients, security, upgrade) | ~40 browser, ~15 client, ~200 security cases, 6 upgrade | PR browser smoke < 15 min; nightly full < 60 min | Full compose stack (or kind), Playwright, fake LLM | mocks inside the stack (browser-side `route()` mocks belong to vitest/`authenticated` specs, not `harness`) |
| **L6 deployment profile / perf / soak** | 4 profiles; 6 load profiles | Profile smoke < 45 min; soak 6 h weekly, 24 h release | Laptop limits, arm64, kind + Helm | n/a |

---

## 2. Unit-test design per component

Directory convention. **Core** (generic, zero `app.*` imports; evolves from `agent_loop_lib`, D14):
tests in `backend/python/tests/unit/harness_core/<component>/`. The test kit ships *inside* the core as
`<core>/testing/` so that Loom (P10) and adapters reuse it. **PipesHub adapters** (`app/harness/**`):
tests in `backend/python/tests/unit/harness/<component>/`. **Node:**
`backend/nodejs/apps/tests/modules/agent-harness/<component>/`. **Frontend:**
`frontend/app/(main)/chat/__tests__/harness/`.

Property-test profile: `tests/unit/harness_core/conftest.py` registers Hypothesis profiles `ci`
(`derandomize=True, max_examples=200, deadline=None`) and `nightly` (`max_examples=5000`, random seed,
`database=DirectoryBasedExampleDatabase(".hypothesis")`, with failing examples uploaded as a CI
artifact). Selected with `pytest --hypothesis-profile=nightly`, not with an env var.

### 2.1 Kernel, cursor and fold (P1.4)

| Test | Kind | Asserts |
|---|---|---|
| `test_fold_is_pure_and_deterministic` | property | `fold(s0, events)` gives equal states across 3 runs and across `PYTHONHASHSEED` values (a subprocess check that catches `list(set)`-style ordering, REPORT C2) |
| `test_fold_incremental_equals_batch` | property | `fold(fold(s0, e[:k]), e[k:]) == fold(s0, e)` for all k: checkpoint + tail equals a full replay |
| `test_checkpoint_is_cache_only` | property | Deleting or corrupting any checkpoint yields the same folded state as having it; a wrong-hash checkpoint is rejected (v1 test carried forward) |
| `test_cursor_after_model_response_is_dispatch_pending` | example | Journal ending in `model.response{2 calls}` + 1 outcome → `cursor.next_action == dispatch_pending{inv_b}`; `ScriptedModel.calls == 0` on wake (never re-sample) |
| `test_resume_never_repeats_pre_run_steps` | example | Intent routing, goal rewrite and clarification run once; wake after crash does not call them (N00 F6) |
| `test_approval_resume_executes_same_invocation` | example | After `interaction.resolved{approve}`, the tool executes with the original `invocationId` and args digest (fixes hard-coded `approved: True`, REPORT E4) |
| `test_dangling_tool_use_repaired_as_compiler_invariant` | property | Every rendered `tool_use` has exactly one `tool_result` whatever the crash position |
| `test_statelot_persist_roundtrip` | property | Every `StateSlot(persist=True)` in `PhaseDriver`/orchestrator survives fold, and a non-persist slot is reset |
| `test_unknown_event_type_is_rejected_or_upcast` | golden | Journals recorded at `schemaVersion` N−1 fold after upcasting (`tests/fixtures/journals/v{n}/*.jsonl`) |

**Model-based.** `RunStateMachine(hypothesis.stateful.RuleBasedStateMachine)` in
`tests/unit/harness_core/driver/test_run_state_machine.py`. It drives the in-memory
`JournalStore` + `RunDriver` + `ScriptedModel` + `FakeToolSource`.

- **Rules:** `admit`, `claim(worker)`, `model_turn(n_calls)`, `tool_outcome(i)`, `crash(worker)`,
  `lease_expire`, `request_approval`, `resolve(decision)`, `cancel`, `stale_commit(worker)`,
  `duplicate_dispatch`, `sweep(now)`.
- **Invariants:**
  - at most one active root run per session
  - fence strictly increases per run
  - terminal states are absorbing
  - every non-terminal run has a deadline (REPORT E5)
  - no event is accepted with fence < current
  - `seq` is gap-free
  - every `tool.intent` with effect ≥ `idempotent_write` has at most one executed side effect in the `EffectRecorder`
  - fold of the journal equals the machine's shadow model

### 2.2 Context compiler (P4.1–P4.5)

**Cache-stability properties** (`tests/unit/harness_core/context/test_cache_stability.py`). Let
`B(S)` be the compiled request split into bands 1–5 (REPORT §6.3).

| Property | Statement |
|---|---|
| `prefix_stable_under_append` | For any state S and any event e not in {tool disclosed, prompt version change, agent/org edit, `context.cleared`, `context.compacted`}: bands 1–3 of B(S+e) are **byte-identical** to B(S), and band 4 of B(S) is a byte prefix of band 4 of B(S+e) |
| `disclosure_only_appends` | Disclosing tool t changes band 1 only by appending t's schema; bands 2–3 unchanged |
| `tools_sorted_by_tool_id` | Band 1 is sorted by `toolId` for any insertion order and any hash seed |
| `compaction_changes_prefix_once` | After `context.compacted{range}`, each of the next N compilations leaves the prefix byte-stable (no per-call rewrite; REPORT C1) |
| `volatile_only_in_tail` | Mutating goal, todos, time, request context or preloaded skills changes only band 5 |
| `breakpoints_positions` | The transport renders cache breakpoints after bands 1, 3 and at the tail. Checked per `ModelTransport` with golden JSON |
| `budget_counts_system_and_tools` | Ladder decisions use `usage` including system + tools; a synthetic `usage` sequence triggers cap → clear at > 50% → compact at > 80% → handoff, in that order and never out of order (C4) |
| `ladder_is_journaled` | Every reduction emits exactly one `context.cleared`/`context.compacted` event; recompiling from the journal alone reproduces the same request bytes (`compile(fold(journal)) == recorded model.request.contextDigest`) |
| `cleared_results_recoverable` | Every `[cleared: see /tool-results/...]` marker names a path the `ToolResultProvider` can read back byte-for-byte |

**Golden files.** `tests/unit/harness_core/context/goldens/<transport>/<scenario>.json` hold rendered
provider requests for Anthropic, OpenAI Responses, Gemini, Bedrock and the LangChain fallback, with
IDs and timestamps normalized. Update with `pytest --update-goldens` (a pytest option registered in
`conftest.py`, not an env var). A golden diff in review *is* the prompt diff, so reviewers see prompt
changes without running a model.

### 2.3 Tool gateway: policy, taint and resolution (P2.1–P2.5)

**Decision table** `tests/unit/harness_core/tool_gateway/policy_table.yaml`. One row per case, run by
`test_policy_table.py`, around 200 rows at GA.

Input columns:
- `effect` (6 classes + unclassified)
- `principal.kind` (user / agent-service / workflow-owner / service / worker)
- `org_rule` (none / allow / ask / deny × glob)
- `taint` (clean / readsUntrusted-in-run)
- `data_flow` (derived-in-run / literal)
- `preapproval` (none / matching / expired / wrong-resource)
- `in_grant` (yes / no / client-narrowed-out)
- `credential` (present / missing)
- `budget` (ok / exhausted)
- `caller` (model / code / workflow / child)

Output: `allow | ask | deny{rule} | unavailable{reason}`.

The table must include these mandatory rows:
- deny beats ask beats allow
- unclassified → treated as `external_write`, so it asks or denies and never auto-allows (REPORT §6.5)
- `worker` principal → deny for any tool
- `caller=code` gets the identical decision to `caller=model` (the code path cannot bypass policy, T3)
- child outside the parent grant → deny (S2)
- missing credential → `unavailable`, and the run is not failed (T8)

Properties (`test_policy_properties.py`):
- **deny-monotonicity:** adding a deny rule never turns any decision into `allow`
- **grant-narrowing:** `allowed(narrow(G)) ⊆ allowed(G)`
- **taint stickiness:** once tainted, a run stays tainted across fold, resume, child spawn and code-mode calls
- **Rule of Two:** there is no allow for `readsPrivate ∧ readsUntrusted ∧ writesExternal` without a matching pre-approval
- **resolution uniqueness:** `modelName` values are unique per run, and two instances of one toolset never collide (T5); property over random instance sets

**Descriptor CI check** (`tests/unit/harness/tool_gateway/test_descriptor_coverage.py`). It iterates
all 598 `@tool`s and the MCP templates. It fails on any tool without an effect class in the overlay,
and on overlay entries that name no existing tool.

### 2.4 Effect scheduler and recovery by effect class (P2.3, REPORT §6.1)

- `test_schedule_respects_concurrency` (property, with a controlled scheduler from the kit):
  - for random batches of calls with `concurrency` classes and resource keys, `exclusive` runs alone
  - `serialize_per_resource` never overlaps on the same key
  - `parallel_safe` calls overlap when possible
  - results are returned in `tool_call` order
- `test_recovery_table`: parameterized over effect class × crash position (before intent, after
  intent, after execute, after outcome). Expected outcomes:
  - `read`/`workspace`: re-run
  - `idempotent_write`: re-run with key `org:run:invocation`; the fake connector dedupes, so the `EffectRecorder` sees one effect
  - `reconcilable_write`: lookup via `reconcile.lookup` found → outcome from reconcile; not found → execute once
  - `external_write`: `tool.unknown` → `needs_reconciliation`, never executed twice
- `test_timeout_produces_unknown_not_failed_for_writes`: a write that times out is `unknown`, not a
  retryable failure.

### 2.5 VFS router and providers (P3.1–P3.8)

- **Path fuzzing.** `test_path_normalization_fuzz.py` (Hypothesis) generates paths from:
  - Unicode (NFC/NFD pairs, compatibility forms, RTL marks, zero-width)
  - `..`, `.`, `//`, trailing slashes, backslashes, percent-encoding, NUL, very long segments
  - case-fold collisions and mount-root lookalikes (`/workspace2`, `/work space`)

  Properties:
  - normalization is idempotent
  - the result never escapes its mount root
  - longest-prefix routing picks exactly one provider
  - rejected inputs raise a typed `PathError`, never an OS error

  Nightly, the same normalizer runs under `atheris` for 10 minutes.
- **Authorization wrapper.** For every provider and every operation, a spy asserts the provider
  received a `VfsCtx` produced by the authorization wrapper. A provider called without one is a
  test failure (X1/X4 by construction).
- **Provider contract suite** (§3.1): `stat/list/read/write/patch/glob/grep` gated by `MountCaps`.
- **Adversarial `grep`.** A corpus of catastrophic-backtracking patterns (`(a+)+$`, nested
  alternations) against a 50 MB file must finish under the cap with RE2 (`test_grep_redos_bounded`).
- **Edit semantics.** `write_file` is create-only unless `expected_hash` is given. `edit_file`
  rejects non-unique `old_str`. `apply_patch` rejects when hunks do not apply. All three are checked
  against the golden patch dialect fixtures.

### 2.6 CAS and workspace revisions (P3.2)

- `WorkspaceRevisionMachine` (Hypothesis stateful). Rules: `write`, `patch`, `commit`, `fork`,
  `snapshot`, `publish`, `retain_checkpoint`, `release`, `gc`, `concurrent_commit(a, b)`.
  Invariants:
  - reachable objects ⊆ stored objects (GC never deletes reachable data)
  - HEAD moves only by CAS on `revision`, and of two concurrent commits exactly one wins
  - forks share objects (O(1): no object copy is counted)
  - `manifestHash` matches its manifest
- `test_cas_rejects_hash_mismatch`, `test_binary_roundtrip_bytes_identical` (property over random bytes).

### 2.7 Durable code runtime: replay and divergence (P8.10)

- `test_replay_prefix_no_reexecution`. A script makes 5 writes and is killed after 3 (failpoint
  `code.after_call_outcome{n=3}`). On re-run the `EffectRecorder` shows exactly 5 effects in total,
  and calls 1–3 were answered from the journal. This is the P5 exit gate in unit form.
- `test_modified_script_raises_replay_divergence`: changing `toolId` or `argsDigest` at `callSeq`
  k ≤ prefix raises `ReplayDivergence` and fails the run with no side effects.
- `test_replay_equivalence` (property). For randomly generated scripts (sequences of SDK calls with
  random effect classes) and a random crash point, the effect log of crash + replay equals the effect
  log of an uninterrupted run.
- `test_suspension_releases_lease`: `wf.sleep`, `wf.wait_for_event` and `wf.approval` journal
  `run.suspended` and release the lease. `ManualClock.advance` fires the timer and the run is requeued.
- `test_determinism_validator` (golden fixtures in `tests/unit/harness_core/code_runtime/validator_cases/`):
  - rejects `time.time`, `datetime.now`, `random`, `uuid4`, `os.environ`, `socket`, `subprocess`, `open` outside `/workspace`, `requests`/`httpx`
  - accepts the `wf.now`/`wf.uuid` equivalents
  - every rejected fixture has a twin that is accepted, so the rule is shown to be targeted rather than a blanket ban

### 2.8 Interactions (P2.6)

- Consume-once. 50 concurrent `POST .../interactions/:id/responses` → exactly one 200; the others get
  409 or an idempotent replay (Node, in-memory and Mongo via §3.1).
- Expiry and re-authorization. If the approver lost permission between request and response, the
  response is rejected. `argsDigest` or `expectedRevision` mismatch → rejected.
- A web answer and a Slack answer race → one `interaction.resolved{by}`.
- A question answered as free text (legacy compatibility, REPORT §8) binds to the pending
  `interactionId`.

### 2.9 RunSpec (P1.2)

- The JSON-schema round-trip is in §3.4.
- `test_spec_digest_golden`: canonical JSON → `specDigest` is stable (goldens), and a key-order
  permutation leaves the digest unchanged (property).
- `test_client_tools_only_narrow`:
  - `tools` omitted = everything the agent grants
  - `[]` = nothing
  - any list → `grants ⊆` the omitted-case grants (property)
  - `agentKey` the actor cannot use → admission is rejected (Node)
- `test_wake_never_reresolves`: after `run.spec_pinned`, the resolver spy is called 0 times across
  wake, resume, retry and child spawn. Changing the agent definition mid-run does not change the run.

### 2.10 Projections and transcript equality (P1.7–P1.8)

- **Python/Node shared goldens.** `contracts/harness/fixtures/journals/*.jsonl` →
  `golden/*.agui.sse` and `golden/*.parts.json`.
- Node `StreamProjector` unit test: journal + LiveBus frames → AG-UI frames equal the golden
  byte-for-byte after normalization. `id:` lines are allowed extras. Truncation limits apply
  (TOOL_CALL_ARGS 2000, TOOL_CALL_RESULT 500).
- **Live == reloaded** (`transcript-parity.test.ts`, fast-check). For random well-formed journals,
  the frontend's live `parts[]` builder fed the projected frames produces the same `parts[]` as the
  transcript projector (N04 §7 #13/#17). This is a differential test in both directions.
- The attempt-discard rule: frames from an abandoned model attempt never reach the output (REPORT §6.4).

### 2.11 Credential broker (P2.4)

- `RunContext` has no attribute path that reaches a credential (reflection test over the type).
- Canary. The `FakeCredentialResolver` issues tokens `pipeshub-canary-<uuid>`. After a scripted run
  touching every tool source, a scanner (`kit.assert_no_canary(...)`) walks the journal, checkpoints,
  LiveBus frames, captured logs, OTel spans, prompt bytes and sandbox FS snapshot, and finds no
  canary.
- A missing or refresh-failed credential → `unavailable`, with the rest of the run continuing (T8).

### 2.12 Sandbox broker with a fake provider (P5.1–P5.5)

- `FakeSandboxProvider` (kit) declares configurable `capabilities()` and records exec/put/get.
- Tier selection by capability. An `agent-code` profile with no isolation class ≥ container is
  refused with a typed error (REPORT §10.2).
- Reaper: persisted `SandboxRef` past expiry → destroyed. Quotas: `fakeredis` quota exceeded → typed
  backpressure.
- **Incremental materialization** property. For random sequences of workspace edits, the bytes
  transferred on the next `exec` equal the sum of the changed files' sizes, and the in-sandbox tree
  hash equals the workspace revision hash (fixes F4).
- Code-mode channel: JSON-lines framing fuzz (split and merged frames, oversized frames, invalid
  UTF-8) → typed errors, never a hang (`ManualClock` timeouts).

### 2.13 MCP hub (P2.8)

The in-process fake MCP server (kit, built on the `mcp` SDK over memory streams) speaks the 2025-11-25
and 2026-07-28 protocols. It is used to test:
- schema cache hit/miss keyed `(instanceId, serverVersion, toolsDigest)`; a digest change invalidates
- pooled sessions keyed `(instance, principal)`, where principal B never reuses principal A's session (negative)
- two instances of one server keep distinct `toolId`s
- a server down → its tools are `unavailable` and the run continues
- tool search is enabled above the threshold

### 2.14 Node journal, fencing, outbox and sweeper (P0.6, P1.1, P1.10)

- Existing tests stay: `outbox-relay.test.ts`, `session-run-service.test.ts`, `run-admission-service.test.ts`.
- **Contract suite** over `InMemoryExecutionStore` and `MongoExecutionStore`. Details in §3.1.
- **Sweeper** with `sinon.useFakeTimers`. Every non-terminal state (`queued`, `running`,
  `suspended`, `recovering`, `needs_reconciliation`, `cancelling`) past its deadline reaches the
  documented next state. A dead-lettered dispatch releases `activeSessionKey` (E5).
- **fast-check model-based.** `fc.commands([Admit, Claim, Renew, Commit, StaleCommit, Cancel,
  Expire, Sweep, Relay])` runs against the in-memory store with a pure model. Nightly, the same
  command set runs differentially against Mongo (the `*.itest.ts` twin).
- **Linearizability of fencing.** `tests/modules/agent-harness/fencing.linearizability.itest.ts`.
  N ∈ {2, 4, 8} concurrent simulated workers issue claim, renew and commit with random delays against
  Mongo. The history (invoke/response timestamps) is checked by a small Wing–Gong checker
  (`tests/helpers/harness/linearizability.ts`, bounded at 12 ops per history, 500 histories nightly)
  against the sequential spec of the lease register. The pass condition is that no history
  linearizes to "two workers committed under the same fence" or "a commit accepted with fence <
  current".
- **Journal history checks** (Elle-style, cheap, run after every L3/L4 test by a shared `afterEach`):
  - `seq` gap-free per session
  - `commitSeq` monotonic
  - one `run.spec_pinned` per run, and it comes first
  - each `tool.outcome` has a prior `tool.intent`
  - nothing follows a terminal run event except retention markers

**Mutation testing (weekly, `mutation-weekly` job).**

| Tool | Targets | Surviving-mutant threshold |
|---|---|---|
| `mutmut` | fold/cursor, policy evaluator, taint propagation, replay/divergence, path normalizer, CAS HEAD update, recovery table | ≤ 10% per module |
| Stryker | fence/lease CAS, admission txn builder, sweeper state transitions, interaction consume-once, StreamProjector terminal-frame logic | ≤ 10% per module |

Survivors are filed against the owning suite. They do not block PRs.

---

## 3. Contract tests

### 3.1 Port contract suites (every adapter, in-memory + each real backend)

Pattern: copy `tests/unit/agent_loop_lib/sandbox/contract/`.
- The suite body lives in the core as `<core>/testing/contracts/<port>_contract.py`, as a class with
  abstract fixture `make_adapter()`.
- Each adapter has a thin subclass under `tests/unit/...` (in-memory, runs in L1) and under
  `tests/integration/harness/contracts/` (real, runs in the backend matrix).
- Real subclasses read an env var such as `HARNESS_IT_MONGO_URI`, `HARNESS_IT_REDIS_URL`,
  `HARNESS_IT_S3_ENDPOINT`, `HARNESS_IT_AZURITE_URL` or `HARNESS_IT_K8S_CONTEXT`. They skip in the
  fixture only. `backend-matrix.yml` discovers them by those names, so any skip fails the job.

| Port | Adapters under contract | Key contract cases |
|---|---|---|
| `JournalStore` (Py client + Node store) | in-memory; Mongo RS (Node store via `HarnessStateClient`) | append with fence, reject stale fence, gap-free seq under 50 concurrent appends, claim-check > 16 KB, `events_after` pagination without gap/dup, retention cursor-gap response |
| `CheckpointStore` | in-memory; blob local / S3 (MinIO) / Azure (Azurite) | corrupt/oversize/wrong-hash rejected, missing object → "no checkpoint", not error |
| `LiveBus` | in-memory; Redis pub/sub; broker fallback on Kafka and Redis Streams | ordered per run, cancel signal delivered, subscriber loss tolerated (at-most-once is allowed; the journal is authoritative) |
| `MessagingFactory` dispatch | Kafka; Redis Streams (existing `tests/integration/messaging` extended) | ids-only payload allowlist, ack-on-claim, duplicate delivery tolerated |
| `MountProvider` | CAS workspace, session docs, scratch, tool-results (generic); attachments, knowledge, artifacts, skills, memory, workflows (PipesHub) | capability-gated ops, pagination, ranges, expected-hash writes, grep limits |
| Blob gateway (`StorageServiceInterface` + Python `blob_storage.py`) | local, S3, Azure (extends `tests/stores/storage-adapters.itest.ts`) | binary round trip, exact version read, large object, timeout/retry |
| `SandboxProvider` | fake; local; Docker (fake client + real `-m docker`); E2B (mocked); k8s agent-sandbox (kind) | extends `test_backend_contract.py`: exec, put/get delta, limits, destroy leaves nothing, capabilities truthful |
| `CredentialResolver` | fake; `ConfigurationService` over Redis KV and etcd | refresh, revoke, no plaintext in errors |
| `CapacityLedger` / quotas | in-memory; Redis; Redis Cluster (existing `redis_cluster` compose) | reservation/release atomic, cross-slot safe |
| `ModelTransport` | `ScriptedModel`; each native transport against recorded HTTP cassettes (`respx`) | streaming, tool calls, usage, cache fields parsed, retry w/o duplicate deltas |
| `CompactionStrategy` | own summarizer (scripted), native (cassettes) | always emits `context.compacted` |

**Differential rule.** For the stateful ports (journal, execution store, revisions), the nightly lane
also runs the Hypothesis or fast-check state machine against the real adapter and compares it
step by step with the in-memory one.

### 3.2 API and SSE golden-frame contract vs legacy (P0.7, gate for P1+)

Location: `backend/nodejs/apps/tests/contracts/` (planned in P0.7).

1. **Recording (once per legacy release, P0).** Run the legacy path on the full stack with the fake
   LLM for ~40 scenarios. Scenarios cover:
   - plain chat, each `chatMode`, agent quick/react/planExecute/deep
   - tool call, ask_user_question, artifact, sub-agent, error, stop
   - `tools` omitted vs `[]`

   Capture the raw SSE bytes, the persisted conversation (`chatSessions`, messages, `parts[]`,
   `content` markers), and the final `RUN_FINISHED.result`. Store them in
   `tests/contracts/golden/<scenario>/{request.json, stream.sse, conversation.json}`.
2. **Normalization** (`tests/contracts/normalize.ts`): ids, timestamps, durations and `requestId`
   become stable placeholders; `id:` lines are stripped for the legacy comparison.
3. **Assertions** with the flag off (legacy) and on (v2):
   - frame sequence equal
   - each frame is `event: T\ndata: <single-line JSON with type T>\n\n`
   - first frame `CUSTOM conversation_created`
   - terminal `RUN_FINISHED{result:{conversation, meta}}` is Node-authored and carries a result
   - child `RUN_FINISHED` has no result
   - `RUN_ERROR.code` vocabulary
   - persisted shapes deep-equal
4. **Allowed deltas** (explicit allowlist file, reviewed like an API change): `id:` lines; the
   intentional behavior changes in REPORT §8 (tab close does not stop the run; `409 RUN_ACTIVE`;
   approval frames under an `ask` policy).
5. Vitest twin `frontend/app/(main)/chat/__tests__/harness/golden-replay.test.ts`: feeds each golden
   `stream.sse` through `createAGUIEventHandler` and snapshots the rendered slot state. It catches
   frontend reducer drift independently of the backend.

### 3.3 `@pipeshub-ai/mcp` compatibility

- **Fast (L2, PR).** `tests/contracts/mcp-package-parser.test.ts` pins `@pipeshub-ai/mcp@2.4.2` as a
  devDependency and feeds its `pipeshub_chat` stream parser every v2 golden stream. It asserts the
  parser finds the terminal frame (only when `result` is present) and extracts the same answer as
  from the legacy golden.
- **Full (L5, nightly).** The existing `integration-tests/mcp_package/integration_test_mcp_package.py`
  is parameterized `harness_flag ∈ {off, on}` and runs the published package as a subprocess with a
  real minted token, as it does today.
- **Forward.** When a new package version is published, the fast test runs against both pinned
  versions (N and N−1).

### 3.4 JSON-schema contracts, Python ↔ TypeScript (P0.5)

- Source of truth: `contracts/harness/*.schema.json` (`RunSpec`, event envelope + each event type,
  `ToolDescriptor`, `Interaction`, `FileMeta`, `SandboxCapabilities`). Fixtures live in
  `contracts/harness/fixtures/{valid,invalid}/*.json`.
- Codegen: Python models via `datamodel-code-generator`, zod via `json-schema-to-zod`. L0 runs
  codegen and `git diff --exit-code`, so stale generated code fails the build.
- **Cross-language round-trip** (`contracts/harness/tests/roundtrip.sh`, runs in `unit-test.yml`):
  - Python parses each valid fixture, emits canonical JSON, TS parses, emits canonical JSON, Python parses again
  - every hop must be byte-equal
  - every invalid fixture is rejected by both languages with the same error path
- **Generative:** `hypothesis-jsonschema` produces 500 instances per schema, and both languages must
  accept them (a subprocess pipe to a Node validator).
- **Evolution:** `schemaVersion` bump requires an upcaster and a golden old-version fixture.
  `test_schema_compat.py` checks that the new schema accepts every fixture under
  `fixtures/v{n-1}/` after upcasting (backward-read compatibility for rolling deploys, §7.5).

### 3.5 OpenAPI

- The new routes (`{base}/runs|events|interactions`, internal worker routes) go into the existing
  OpenAPI doc. `openapi-validator.yml` (Spectral) keeps linting it.
- `tests/contracts/routes-vs-openapi.test.ts` compares registered Express routes with OpenAPI paths
  (v1 Gate 0 item 2). An unregistered spec path or an undocumented route fails.
- Nightly `schemathesis run` against Node in the L3 stack covers the harness routes with two users in
  two orgs. Its checks include `not_a_server_error` and `response_schema_conformance`, plus a custom
  check that cross-org ids yield only 403 or 404.
- Harness control-plane routes must never appear in the MCP tool list (REPORT §5.2). A test in
  `tests/modules/mcp/` asserts this.

---

## 4. Deterministic harness test kit

Home: `<core>/testing/` (Python, importable by core tests, adapter tests, integration tests and later
Loom), `backend/nodejs/apps/tests/helpers/harness/` (Node) and `integration-tests/helper/harness/`
(full-stack helpers). Every item has its own tests, and every port fake passes its contract suite.

### 4.1 Scripted fake model

- `ScriptedModel` is promoted from `scripted_transport.py`; the old path re-exports it to avoid a big
  bang. It implements the `ModelTransport` port with provider-native shapes: tool calls with provider
  IDs, `usage` including `cache_read`/`cache_write`, `stop_reason`, reasoning blocks, deltas and
  errors (429/500/timeout/malformed).
- **Script DSL** (YAML, `tests/fixtures/model_scripts/*.yaml`):
  ```yaml
  id: approval-jira-create
  turns:
    - expect: { last_user_contains: "create a P1 bug" }      # optional guard → ScriptMismatch
      reply: { tool_calls: [{ name: jira_create_issue, args: { summary: "Login 500" } }] }
    - expect: { tool_result_for: jira_create_issue, status: succeeded }
      reply: { text: "Created PROJ-1.", chunks: 4 }
  ```
  Guards turn a harness regression into a readable `ScriptMismatch` instead of a silent wrong path.
  `reply.from_request` lets a turn compute its reply from the compiled request, for example to echo a
  path the harness returned.
- **HTTP form (`fake-llm` service)** for L3–L5: `integration-tests/helper/harness/fake_llm/` serves
  OpenAI-compatible `/v1/chat/completions` + `/v1/responses` and Anthropic `/v1/messages` (SSE
  streaming). Scripts are selected by a marker in the user message (`[[script:approval-jira-create]]`)
  or by a per-org model name (`fake/approval-jira-create`). It is registered as an ordinary
  OpenAI-compatible model in the seeded org, so no product code knows about it. A control endpoint
  (`/_control/*`) exposes call logs for assertions and supports injected latency or a mid-stream cut
  for fault tests.

### 4.2 Fake connectors and MCP servers with effect recording

- `FakeToolSource` (in-process) builds descriptors for any effect, concurrency and idempotency
  combination. Its handlers write to an `EffectRecorder` (append-only, with assertions
  `assert_exactly_once(key)`, `assert_none()`, `effects_for(tool)`).
- `fake-saas` (HTTP, compose) is a minimal Jira-, Slack- and Gmail-shaped API. It offers idempotency
  keys, a `lookup` endpoint for `reconcilable_write`, configurable "lose the ack after committing"
  (the P2 exit gate), and multi-instance support (two base URLs = two Jira instances). Real PipesHub
  toolsets are configured against it by base URL where the toolset supports that. A thin
  `FakeJiraToolset` registered only in test images covers the rest.
- `fake-mcp` (streamable HTTP + STDIO) runs both protocol versions and has toggles for schema
  change, slow `tools/list`, crash and poisoned results (for security tests).

### 4.3 Fake sandbox provider

`FakeSandboxProvider` has configurable capabilities and isolation class. It keeps an in-memory FS and
evaluates scripts via a tiny interpreter for `pipeshub` SDK call lists (deterministic, no subprocess),
which lets code-mode and durable-runtime tests run in L1. The real providers run the same scripts in
L3.

### 4.4 Clock, IDs and scheduling

- `ManualClock` implements the core `Clock` port (injected into leases, deadlines, timers, TTLs,
  retention). `advance(dt)` fires due timers deterministically.
- `SeededIds` provides deterministic `runId`/`invocationId`/`eventId`.
- `ControlledScheduler` is an asyncio policy that runs ready tasks in an order chosen by a Hypothesis
  strategy. It is used for scheduler and race properties (§2.4) and to replay a failing interleaving
  from its seed.
- Node: `tests/helpers/harness/clock.ts` wraps `sinon.useFakeTimers`, with the same semantics.

### 4.5 Failpoints

`<core>/failpoints.py` and `modules/agent-harness/domain/failpoints.ts`. In production the registry is
a no-op. See §6.1.

### 4.6 Journal-replay fixtures (P0.4, `tests/harness_replay/`)

- **Record.** `python -m tests.harness_replay.record --run <runId>` exports a run's journal (events +
  claim-checked payloads + model responses + tool outcomes).
  - It is scrubbed by `scrub.py`: secret patterns, emails and names → stable pseudonyms, document text
    → length-preserving lorem, record IDs remapped. A secret-canary scan runs afterwards.
  - Output is a `.jsonl.zst` under `backend/python/tests/fixtures/journals/<source>/<id>/` with a
    `MANIFEST.json` (harness version, schemaVersion, model, scrub version).
  - Production recordings need an org opt-in flag and a reviewer sign-off in the PR that adds them.
  - Legacy runs are recorded from the P0 SSE + transcript capture and converted with
    `legacy_to_journal.py`.
- **Replay modes:**
  - `strict`: frozen model responses and tool outcomes. The new build must produce an identical
    journal, modulo the allowlist. This mode is the regression test for refactors.
  - `harness-diff`: model responses are served per turn index. When the compiled `contextDigest`
    differs, the replay reports a context diff (band-level) and continues. This is how P4 changes are
    reviewed.
  - `live`: the model is real and tool outcomes are frozen.
- **Corpus:** at least 60 journals at GA (all modes, sandbox, approvals, children, workflows). Each
  phase adds the journals for its own features. `strict` replay runs on every PR touching the harness
  (it is fast, with no stack).

### 4.7 Reuse across phases

| Phase | Kit pieces it must add |
|---|---|
| P0 | `ScriptedModel` promotion, `fake-llm`, golden recorder, replay tool, schema fixtures |
| P1 | in-memory `JournalStore`/`LiveBus`/`CheckpointStore`, `ManualClock`, failpoints (worker + Node), history checker |
| P2 | `FakeToolSource`, `EffectRecorder`, `fake-saas`, `fake-mcp`, `FakeCredentialResolver`, policy table |
| P3 | in-memory providers, path corpus, adversarial regex corpus |
| P4 | transport cassettes, context goldens, usage-sequence generator |
| P5 | `FakeSandboxProvider`, SDK script generator, determinism-validator fixtures |
| P6–P8 | child-run and workflow script fixtures, trigger clock (`ManualClock` in the trigger service), `fake-saas` Slack channel |
| P10 | Loom runs the core contract suites and the fault suite unchanged |

---

## 5. Integration tests

### 5.1 Stack

A new compose file, `deployment/docker-compose/docker-compose.integration.harness.yml`, reuses the
replica-set bootstrap of `docker-compose.agent-harness.yml`. Services:
- `mongodb` (single-node RS, keyfile, init job)
- `redis`
- `redis-streams` or `kafka` (profile-selected)
- `minio` (S3)
- `azurite` (Azure Blob)
- `etcd` (profile)
- `toxiproxy` (fronts Mongo, Redis, broker, Node internal API, fake-llm and the sandbox broker)
- `fake-llm`, `fake-saas`, `fake-mcp`
- `sandbox-broker` (P5+; the only service with the Docker socket; later also `runsc`)

Suites:
- **Python** `backend/python/tests/integration/harness/` (worker ↔ Node over the real internal API):
  `test_dispatch_claim_commit.py`, `test_resume_on_other_worker.py`, `test_livebus_streaming.py`,
  `test_approval_suspend_resume.py`, `test_vfs_session_persistence.py`, `test_code_mode_rpc.py`, ...
- **Node** `backend/nodejs/apps/tests/integration/agent-harness/*.itest.ts` (`mocha --no-config`):
  admission atomicity with injected pre-commit failures (v1 Gate 3), 50 concurrent identical and
  distinct admissions, index uniqueness, outbox relay against both brokers, retention jobs.
- Full-stack HTTP flows go in `integration-tests/harness/` with marker `harness`, as a new shard in
  `integration-tests.yml`.

We use compose files rather than testcontainers. That matches every existing lane, lets developers run
the identical stack locally (`docker compose -f ... up --wait`), and keeps the backend-matrix
discovery convention.

### 5.2 Backend matrix strategy

The harness's own state touches only Mongo, Redis, the broker, blob and KV. Graph and vector matter
only for the `/knowledge` mount and retrieval tools. So harness correctness does not multiply by
graph × vector.

| Dimension | PR (path-filtered harness lane) | Merge to `main` | Nightly | Release |
|---|---|---|---|---|
| Broker | Redis Streams | + Kafka | both | both |
| LiveBus | Redis pub/sub | same | + broker fallback (Kafka, Redis Streams) | all |
| Blob | local | local | local, S3 (MinIO), Azure (Azurite) | all |
| KV / credentials | Redis | Redis | Redis, etcd | both |
| Mongo | RS 1 node | RS 1 node | RS 3 nodes (step-down tests) | 3 nodes + managed-like (auth, TLS) |
| Graph × vector (knowledge-mount suite only) | Neo4j × Qdrant | same | Neo4j×{Qdrant, OpenSearch, Redis}, Arango×{Qdrant, OpenSearch, Redis} | full 2×3 |
| Sandbox tier | fake + Docker (runc) | same | + Docker `runsc`, kind agent-sandbox | + managed adapter (E2B, secrets lane) |

Nightly combinations of broker × blob × KV use a **pairwise covering array** (6 jobs instead of 12),
committed as `.github/harness-matrix.yml`. The full cartesian product runs at release.

---

## 6. Fault injection and chaos suite

### 6.1 Deterministic crash points (failpoints)

- **API.** `failpoint("worker.model_response.after_commit")`. It is a no-op unless armed. It can be
  armed in two ways:
  - **In-process** (L1/L3): `failpoints.arm(name, action, when=nth(3))`.
  - **Cross-process** (L4): a JSON file at the fixed path `/run/pipeshub-failpoints/failpoints.json`,
    bind-mounted by the test compose overlay and read on change.

  There is no env var, and a production image has no such mount.
- **Actions:**
  - `kill` (`os.kill(getpid(), SIGKILL)` / `process.kill(process.pid,'SIGKILL')`: a true kill -9, with no `finally`)
  - `raise(ExcType)`
  - `delay(ms)`
  - `pause` (blocks until released via the control file; used to create stale-fence windows)
  - `drop` (skip the step; used for lost acks)
- **Triggers:** `once`, `nth(k)`, `prob(p, seed)`.
- **Coverage check.** `test_failpoint_catalog.py` asserts that every name in
  `<core>/failpoints_catalog.py` appears in code, and that every catalog entry has at least one L4
  scenario.

**Catalog and required observations** (L4 suite `integration-tests/harness/fault/test_crash_points.py`,
parameterized per point × {Redis Streams, Kafka}):

| Point | Process | Required observation |
|---|---|---|
| `node.admit.before_txn_commit` | Node | nothing admitted; retry with same key succeeds once |
| `node.admit.after_commit_before_response` | Node | retry returns original run; one user turn |
| `node.relay.after_publish_before_mark` | Node | redelivery; one claim, one answer |
| `node.claim.after_fence_inc_before_response` | Node | worker retries claim; fence gap allowed, no double ownership |
| `node.commit.after_events_before_transcript` | Node | impossible by txn: assert events and `parts[]` both absent or both present |
| `node.interaction.after_consume_before_outbox` | Node | sweeper/outbox re-drives resume; decision not lost, not applied twice |
| `node.sweeper.mid_scan` | Node | next sweep completes; no double transition |
| `node.projector.mid_stream` | Node | client reconnects with `Last-Event-ID`; no gap, no dup |
| `worker.after_claim` | Py | recovered; `run.spec_pinned` written exactly once |
| `worker.after_spec_pinned` | Py | wake uses pinned spec; resolver not called |
| `worker.model.mid_stream` | Py | deltas shown, nothing journaled → turn re-sampled once; projector discards abandoned attempt frames |
| `worker.model_response.after_commit` | Py | **model not re-sampled**; dispatch resumes |
| `worker.tool.after_intent_before_execute` | Py | executed once after recovery |
| `worker.tool.after_execute_before_outcome` | Py | per effect class (§2.4): re-run / keyed / reconcile / `needs_reconciliation`; `EffectRecorder` never shows a duplicate external write |
| `worker.tool.after_outcome` | Py | not re-executed |
| `worker.compaction.before_event` | Py | compaction recomputed once; journal has one `context.compacted` |
| `worker.workspace.after_blob_before_cas` | Py | orphan objects GC-able; HEAD unchanged; tool re-run safe |
| `worker.workspace.after_cas_before_outcome` | Py | revision reused (idempotent by `invocationId`); no second revision |
| `worker.suspend.after_request_before_release` | Py | lease expiry → run `suspended`, not `recovering` loop |
| `worker.child.after_admit_before_parent_event` | Py | parent adopts existing child run; no second child |
| `worker.artifact.after_registry_before_event` | Py | publication key idempotent; one artifact version |
| `code.after_call_outcome{n}` | Py | replay performs only calls > n |
| `broker.provision.after_create_before_ref` | Sandbox broker | reaper destroys the orphan (label scan) |
| `broker.exec.mid_materialize` | Sandbox broker | next exec re-diffs; tree hash equals revision |

**Process-level kills.** Beyond in-code failpoints, `integration-tests/helper/harness/chaos.py` offers
`docker kill -s KILL <svc>` and `docker pause`/`unpause` at event-driven moments. For example, it
waits until the journal shows `tool.intent` for a named tool. This keeps the tests deterministic
without sleeps.

### 6.2 Races and fencing

- **Stale worker.** `docker pause worker-a` at `worker.tool.after_authorize` until the lease expires.
  Worker B claims and continues, then A resumes. Required:
  - A's commits are rejected, 100%
  - A re-validates its lease **before executing** any non-read effect (renew-before-execute), so the
    `EffectRecorder` shows no second write
  - the UI shows no duplicate frames
- **Duplicate delivery.** `helper/harness/broker_inject.py` publishes each dispatch 1–3 times with
  jitter through `MessagingFactory`. Expected: one claim and one answer.
- **Cancel races.** Cancel vs completion, cancel during model stream, cancel during tool, cancel
  during sandbox exec. Expected: exactly one terminal event, and the container is gone within the
  deadline.
- **Two tabs / two workers.** Concurrent sends → one active root run plus `409 RUN_ACTIVE` (Q3).
  Approval answered from web and Slack simultaneously → one resolution.

### 6.3 Network partitions (toxiproxy)

| Link | Toxics | Required |
|---|---|---|
| worker ↔ Node internal API | `timeout`, `reset_peer`, 5 s `latency` | commits retried idempotently (command receipts); lease loss → stop before next effect |
| worker ↔ Redis (LiveBus) | `timeout` / drop | **LiveBus loss:** UI keeps advancing from journal commits at turn/tool boundaries; final transcript identical; cancel falls back to outbox within the renew interval |
| Node ↔ Redis | drop | projector serves from journal; reconnect ≤ 3 s after heal |
| Node ↔ Mongo | `reset_peer` mid-txn; RS `stepDown` | no partial admission; retryable writes; no duplicate events |
| relay ↔ broker | down 2 min | outbox durable, bounded retry, drains after heal |
| worker ↔ fake-llm | cut mid-stream | retry with new attempt id; no duplicate deltas in UI |
| worker ↔ sandbox broker | timeout during exec | `run_code` outcome `unknown` → treated per `execute` effect; no orphan after reaper |
| sandbox ↔ egress proxy | down | package install fails typed; no direct egress fallback |

### 6.4 Dependency outages

These extend `integration-tests/resilience/` and reuse `fault_switches.py`. Each outage is paired with
its required behavior:
- Mongo down → admission 503 and in-flight runs pause without losing events
- Redis down → LiveBus degraded, quotas fail closed for sandbox provisioning
- broker down → outbox holds
- blob unwritable (existing switch) → checkpoint or claim-check failure never advances state
- etcd/KV down → credentials unavailable → tools `unavailable`, run continues
- MCP server down → its tools unavailable
- sandbox broker down → typed error
- provider 429 storm → backoff within budget, no journal pollution

### 6.5 Clock skew

A `libfaketime` layer in the test worker image, with its offset set per container by the compose
overlay, runs the worker at ±10 min and the Node replicas at ±90 s relative to each other. Required:
- lease validity is decided by Node/Mongo server time only
- worker JWT `exp`/`nbf` leeway holds
- the sweeper never expires a lease early due to a skewed replica (the sweeper uses Mongo `$$NOW`)
- trigger schedules (P8) fire once per occasion

### 6.6 Execution and repetition

- **PR canary:** 12 points, one broker.
- **Nightly:** full catalog × both brokers, plus partitions and outages.
- **Release:** each process-kill scenario ×100 with random `nth`/`prob` seeds, which is v1 Gate 6's
  soak requirement. Seeds are logged, and any failure replays from its seed.
- Every scenario ends with the §2.14 journal history checks and a leak check (containers, active
  leases without owners, stuck outbox rows, orphan CAS objects past grace).

---

## 7. End-to-end suites

### 7.1 Browser journeys (Playwright)

- New project `harness` in `frontend/playwright.config.ts` with deps `setup` +
  `harness-provision` (`tests/e2e/setup/harness.provision.ts`). The provision step enables the Labs
  flag and registers the `fake-llm` model and `fake-saas` connectors.
- Specs live under `frontend/tests/e2e/harness/` and use `retries: 0` (trace on first failure). A
  retry would hide exactly the races we are testing for.
- The PR smoke is the `@smoke` subset.

| Spec | Journey | Tag |
|---|---|---|
| `chat-durable-happy-path.spec.ts` | send → stream → answer → reload; live == reloaded DOM (text, citations, parts) | @smoke |
| `chat-tab-close-continues.spec.ts` | close page mid-answer, reopen: answer complete, one user turn, one answer | @smoke |
| `reconnect-last-event-id.spec.ts` | `context.setOffline(true)` 5 s mid-stream → resumes from last seq; no dup text | @smoke |
| `stop-cooperative.spec.ts` | Stop → "Stopped" marker, partial saved, one cancel request | @smoke |
| `approval-approve.spec.ts` / `approval-reject.spec.ts` | scripted write under `ask` policy → card shows exact action → decision → run continues / tool denied result | @smoke |
| `approval-survives-restart.spec.ts` | approval pending; restart worker + Node; approve → executes once (`fake-saas` effect count = 1) | |
| `question-card-restore.spec.ts` | ask_user_question → reload → card restored → answer binds interaction | |
| `artifact-exact-version.spec.ts` | code mode publishes xlsx → live card → reload → same version; later version doesn't replace historical card | |
| `concurrent-send-409.spec.ts` | two tabs send → one run, other shows conflict UX | |
| `background-streams.spec.ts` | 15 concurrent background streams across conversations | |
| `workspace-across-turns.spec.ts` | turn 1 writes `/workspace/notes.md`; turn 3 reads it (worker restarted in between) | |
| `subagent-nesting.spec.ts` | deep mode; child runs render nested (STEP_STARTED before child RUN_STARTED) | |
| `workflow-author-approve-run.spec.ts` (P8) | "every Monday…" → review card → approve → `ManualClock`-driven trigger fires 4× → 4 posts in fake Slack | |
| `flag-off-legacy.spec.ts` | flag off: only legacy endpoint called (network assertions) | @smoke |

**Electron.** `electron-build.yml` gains a step that runs `chat-durable-happy-path` against the
packaged app through Playwright's `_electron.launch` (nightly, macOS arm64 runner).

### 7.2 Slack and MCP client journeys

- `integration-tests/harness/clients/test_slack_internal_routes.py` drives the `/internal/*` twins
  exactly as the Slack bot does, and posts a signed Slack interaction payload to approve. It asserts
  one resolution and one `fake-saas` effect. It also covers expired approvals and approvers without
  permission.
- `@pipeshub-ai/mcp` full journey (§3.3) with the flag on, plus a case where the MCP client
  disconnects mid-run and the run still completes.
- A PAT / OAuth-app client calls the new `{base}/runs|events` routes with scoped tokens. The scope
  matrix mirrors P0.2.

### 7.3 Security E2E (`integration-tests/harness_security/`, marker `security`; never quarantined)

- **Injection → exfiltration** (scripted adversarial model; each case has a vulnerable twin fixture):
  - Poisoned sources: knowledge doc, `fake-mcp` tool result, `/inputs` attachment, memory entry, skill resource.
  - Exfiltration channels the model is scripted to use:
    - `fake-saas` Slack post / Gmail send with tainted data → must `ask` (Rule of Two); pre-approved recipient domain only
    - markdown image URL with query-string data → frontend renders no remote image from untrusted origins (vitest + Playwright assertion)
    - code-mode `httpx.get` to an attacker host → egress proxy denies; SDK call to a non-granted tool → deny
    - workflow writing outside declared targets → suspends for approval
    - memory write from a tainted run → quarantined
- **Sandbox escape and egress probes** (per tier; reuses v1 Gate 7 item 4). Probes include:
  - env enumeration, `/proc`, fd reads
  - DNS/HTTP, loopback, RFC1918, compose service names (`mongodb`, `redis`, `pipeshub-ai`), `169.254.169.254`
  - Docker socket, host paths, `setuid`, fork bomb, disk fill

  Required: denial and no canary visible. The run lives in `sandbox_security/test_probes.py`,
  parameterized by tier.
- **Credential canaries** (§2.11) end to end: the broker message, checkpoint, journal, logs, spans,
  sandbox and artifact are all scanned.
- **Cross-tenant and cross-user negatives.** `test_idor_matrix.py` is generated from the OpenAPI
  route table. For each route with path/body IDs (session, run, event cursor, interaction, artifact
  id/version, workspace revision, skill digest, idempotency key), it replays the request as
  {same-org other user, other-org admin, shared-viewer, PAT with insufficient scope}. Expected: a
  generic 403 or 404 with no data. It includes the P0.1 IDOR regression (`get_record_download_url`),
  VFS `/knowledge` reads for records the principal cannot see, and service-account agent retrieval
  (D11) once decided.
- **Token misuse.** Worker claim/renew/commit tokens are reused across operation, run, org and after
  expiry (v1 Gate 4 items 3–4, Gate 7 item 5).
- **Skill and archive abuse.** Traversal, links, bombs and Unicode collisions (v1 Gate 7 item 6),
  against the `/skills` mount.

### 7.4 Workflow and automation E2E (P8)

- Authoring run → validate → smoke in `authoring-smoke` (no egress; a probe asserts it) → dry run
  (writes blocked; `EffectRecorder` shows zero) → commit → activate.
- An unverified version cannot activate (negative).
- Another org member cannot edit, run or read the workflow.
- A trigger fires once per occasion across a worker kill (idempotency key = task + occasion).
- Crash mid-run → no duplicate post.

### 7.5 Upgrade, rolling deploy, rollback

These extend `deployment/docker-compose/tests/upgrade_smoke.sh` + `upgrade_seed.py` and add
`deployment/helm/tests/kind_upgrade_harness.sh`.

1. **In-flight upgrade (compose).**
   - Seed on release N with runs that are queued, running (long scripted tool), suspended (approval)
     and scheduled (P8).
   - Upgrade to N+1 and resolve the approval.
   - Assert all runs reach a terminal state, no duplicate effects, journals fold on N+1, and old
     events upcast.
2. **Rolling (kind, 2 Node + 2 worker replicas).** `helm upgrade` while 20 scripted runs are active,
   in both orders:
   - Node N+1 + worker N: the reader tolerates unknown optional fields
   - Node N + worker N+1: the worker writes only schema versions N+1 can read, or waits

   Asserts: stale workers are fenced and no run is lost.
3. **Flag rollback.** Turn the Labs flag off with in-flight durable runs. Admission returns 503 while
   reads and cancel keep working, and the runs drain (v1 Gate 1 "disabled admission").
4. **Worker drain.** SIGTERM → stop claiming, finish or release at the next journal boundary, and the
   broker message is not acked before disposition.

### 7.6 Backup and restore (P9.4)

- `mongodump` + blob copy taken mid-run, then restored into a fresh stack. Runs recover or land in
  `needs_reconciliation`. No run silently re-executes a journaled external write.
- Skewed restore, where blob is newer than Mongo: extra CAS objects are GC'd after grace.
- Skewed restore, where Mongo is newer than blob: a missing object → typed `payload_unavailable`
  failure, never a crash loop.
- Restore twice → idempotent.

---

## 8. Deployment-profile testing

### 8.1 Profiles

| Profile | Where tested | Harness specifics |
|---|---|---|
| **Laptop** (Compose `eval` install, 8 GB, arm64/macOS Docker Desktop, maybe Electron) | `published-hub-smoke.yml` `eval` job + `arm64-smoke.yml`; compose `deploy.resources.limits` cpus=4 mem=8g; nightly Electron on a macOS arm64 runner | no gVisor: capability probe reports `isolation=container`; `desktop-eval`: no sandbox (file tools only, `run_code` unavailable with reason); `desktop`: tier 1 runc via the broker container; `agent-code` allowed only with container isolation (refused otherwise, test both); inline profile default; Mongo RS single node via overlay |
| **Compose server** (slim/full, x86) | `e2e-smoke.yml`, `integration-tests.yml` | full harness stack; Kafka and Redis Streams; tier 1 + optional `runsc` |
| **Helm (kind/k3d)** | `helm-chart.yml` + `kind_smoke.sh SMOKE_VARIANT=harness` | separate Deployments (relay/sweeper, workers, sandbox broker); agent-sandbox CRDs + warm pool; runc in kind (gVisor nightly on a larger runner with the `runsc` containerd shim installed in the kind node image) |
| **Helm managed-like** (EKS values, `values-eks-kind.yaml`) | release | 2+ replicas each; external Mongo with TLS/auth; S3/Azure blob |

Harness smoke per profile (`integration-tests/harness/smoke/test_profile_smoke.py`, marker `smoke`),
about 5 minutes:
1. durable chat turn with a tool
2. kill worker → resume
3. approval round trip
4. `run_code` in the profile's default tier with an egress probe
5. file persists across turns
6. no leaked sandboxes or leases

Laptop-only extra checks:
- idle harness worker RSS ≤ 350 MB
- sandbox broker idle ≤ 100 MB
- cold `run_code` ≤ 5 s on arm64
- the stack starts within the `eval` install's memory budget with the harness enabled

macOS Docker Desktop has no CI runner with nested Docker. It is covered by the arm64 Linux runner as a
proxy, plus a **release checklist item** executed on Apple silicon and recorded in the release issue.

### 8.2 Load and performance (`integration-tests/perf/bench_harness.py`)

The existing perf framework stays as it is: `stack.py`, `compare.py`, committed
`baselines/<label>.json`, and summaries into `GITHUB_STEP_SUMMARY`. The load generator is Python
asyncio (`httpx` + SSE), consistent with `bench_query.py`. **k6** (with `xk6-sse`) is used only for the
reconnect-storm profile, where tens of thousands of SSE connections exceed a Python client. The fake
LLM drives these tests with fixed token rates, so the numbers measure the harness and not a provider.

Load profiles:
- one tenant, many sessions
- many tenants, one session each
- long-disconnected client with a large event history
- max-size checkpoint, workspace, bundle and artifact
- slow model and slow tool with many lease renewals
- worker autoscale, including scale-to-zero, with broker and blob latency injected

### 8.3 SLO thresholds (gates)

| Metric | Threshold | Gate |
|---|---|---|
| Non-model latency overhead, short turn (inline), p50 vs P0 baseline | < +15% | P1 exit |
| Admission p95 | < 150 ms | release |
| `conversation_created` first frame p95 | < 300 ms | release |
| LiveBus delta → browser p95 | < 100 ms | release |
| Reconnect to first new frame | ≤ 3 s | GA |
| Recovery to runnable after lease expiry | ≤ 60 s | GA |
| Mongo writes per tool-using turn | ≤ 2 + 2 × tools (coarse commits) | P1 exit |
| First token with 5 MCP servers attached | better than P0 baseline | P2 exit |
| Cache-read share of input tokens | better than P0 baseline per model | P4 exit |
| Tier 1 warm / tier 1 cold / tier 2 warm pool | < 1 s / < 5 s / < 500 ms | P5 exit |
| Code-mode 5k-issue aggregation: context tokens | bounded (< 10k) | P5 exit |
| 24 h soak | 0 orphan containers, 0 ownerless leases, 0 stuck outbox rows, RSS growth < 10% | GA |

`compare.py` gains a `--fail-on-regression` flag for harness benchmarks only. Existing benches stay
report-only.

---

## 9. Trajectory checks

The existing answer-quality evals in `backend/python/tests/evals/` are unchanged. The harness is held by deterministic checks over the journal of
recorded and scripted runs (`tests/harness_replay/`), with zero tolerance:

- no external write without `allow` or a resolved approval
- no tool outside the grant
- no canary or secret in the output
- citations resolve to records the principal can read
- no re-sampled journaled turn
- no `ReplayDivergence` on scripted workflows

---

## 10. CI mapping

### 10.1 Lanes

| Lane | Trigger | Workflow (new or extended) | Contents | Budget |
|---|---|---|---|---|
| **PR fast** | every PR | `unit-test.yml`, `main.yml` (extended) | L0 static (import-linter, codegen drift, descriptor coverage), L1/L1p (`ci` profile), in-memory contracts, schema round-trip, SSE goldens, MCP-parser contract, `strict` replay, frontend vitest | < 12 min |
| **PR harness** | path filter: harness core, `app/harness/**`, `modules/agent-harness/**`, `enterprise_search` stream code, `contracts/harness/**`, chat streaming FE | new `harness-integration.yml` + extended `agent-harness-infrastructure.yml` | L3 on Redis Streams + local blob, Docker sandbox contract + leak check, L4 canary (12), Playwright `harness @smoke` | < 25 min (jobs parallel) |
| **PR backend** | existing path filters | `backend-matrix.yml` (extended with `suite: harness-*`) | real-adapter port contracts per backend | < 12 min/job |
| **Merge to `main`** | push to `main` | `harness-integration.yml` | + Kafka, full L3, full Playwright `harness` | < 45 min |
| **Nightly** | 18:30 UTC (with `integration-tests.yml`) | `harness-fault.yml`, `integration-tests.yml` harness shard, `backend-matrix.yml` schedule | pairwise matrix, full fault catalog × brokers, partitions/outages/skew, security E2E, IDOR matrix, schemathesis, Hypothesis/fast-check `nightly` profile + differential on Mongo, knowledge-mount 2×3, gVisor kind job, Electron smoke | < 3 h wall (parallel) |
| **Weekly** | Sun | `mutation-weekly` job in `harness-fault.yml`; `perf-harness.yml` | mutmut/Stryker, 6 h soak, perf baselines compare | scheduled |
| **Release candidate** | release branch / tag | `release-publish.yml` gate job → reusable workflows | full cartesian matrix, fault ×100, upgrade/rolling/rollback, backup/restore, 24 h soak, Helm managed-like, per-profile smokes incl. arm64 and laptop limits, macOS checklist | blocking |

### 10.2 Flake policy and ownership

- **Retries:**
  - `retries: 0` for the `harness` Playwright project and for `fault` and `security` markers
  - other existing Playwright projects keep their current 2 retries
  - security and fault failures are never quarantined; they block, as in v1 Gate 7
- **Quarantine:** `@pytest.mark.quarantine(owner="@handle", issue="#1234", until="2026-11-01")` and a
  mocha `describe.skip` wrapper with the same metadata. A conftest or root-hook plugin **fails the run
  when `until` has passed**. A quarantined test still runs in nightly and reports.
- **Flake hunting:** the nightly `flake-hunt` job reruns the L3 harness suite ×20 with
  `pytest-repeat` and random `ControlledScheduler`/failpoint seeds. Any failure files an issue with
  its seed so it can be replayed exactly.
- **Artifacts per failed test** (v1 §6 rule): runId, sessionId, attempt/fence, last seq, checkpoint
  version, workspace revision, artifact id/version, container ids, failpoint/seed, plus the scrubbed
  journal. Never credentials, prompts with user data, or file bytes.
- **Ownership** (`CODEOWNERS`, one line per suite directory):

  | Area | Owner | Suite directories |
  |---|---|---|
  | Kernel, driver, replay | Execution track | `tests/unit/harness_core/{driver,code_runtime}`, `tests/harness_replay/` |
  | Tool gateway, policy, MCP, security | Execution track + security reviewer | `tests/unit/harness_core/tool_gateway/`, `integration-tests/harness_security/` |
  | VFS, CAS, skills, artifacts, memory | Data track | VFS, CAS, skills, artifacts and memory suites |
  | Context, model gateway | Intelligence track | `tests/unit/harness_core/context/` |
  | Journal, projector, contracts | Node / control-plane owners | `backend/nodejs/apps/tests/{modules/agent-harness,contracts}/` |
  | Browser journeys | Frontend owners | `frontend/tests/e2e/harness/` |
  | Profile, upgrade, perf | Platform / deploy owners | profile, upgrade and perf suites |

### 10.3 Phase exit gates → named suites

| Phase | Exit gate (the implementation plan) | Suites that must be green |
|---|---|---|
| **P0** | P0.1–P0.2 shipped; contracts versioned; v1 merged dark | `integration-tests/harness_security/test_idor_matrix.py::p0_*` + `tests/unit/.../test_record_download_url_acl.py` (P0.1); Node `toolsets-routes.scopes.test.ts`, `pat-scope-bounds.test.ts`, `internal-agent-routes.scopes.test.ts` (P0.2); `tests/harness_replay/test_replay_legacy_run.py` (P0.4); `contracts/harness/tests/roundtrip.sh` (P0.5); `execution-store.contract` on in-memory **and Mongo**, `sweeper.test.ts` (P0.6); `tests/contracts/sse-golden.test.ts` flag-off green + `mcp-package-parser.test.ts` (P0.7) |
| **P1** | Resume on another worker w/o re-sampling or repeating a tool; UI identical; overhead < 15% p50 | `test_run_state_machine.py`; `JournalStore`/`LiveBus`/`CheckpointStore` contracts (Mongo, Redis, both brokers); `fault/test_crash_points.py -k "node. or worker.(after_claim|after_spec|model|tool)"` × both brokers; `fencing.linearizability.itest.ts`; SSE goldens + `transcript-parity.test.ts` flag on/off; Playwright `chat-durable-happy-path`, `chat-tab-close-continues`, `reconnect-last-event-id`, `stop-cooperative`, `flag-off-legacy`; `mcp_package` flag on; `bench_harness.py --profile short-turn` vs P0 baseline; import-linter core boundary (P1.11) |
| **P2** | Lost ack never blind-retries; approval survives restart, answerable from web/Slack; two Jira instances; MCP first-token better | `policy_table.yaml`, `test_policy_properties.py`, `test_recovery_table`, `test_descriptor_coverage.py`; `worker.tool.after_execute_before_outcome` fault × effect classes with `fake-saas` lost-ack; Playwright `approval-*` incl. `approval-survives-restart`; `test_slack_internal_routes.py`; `test_two_jira_instances.py`; MCP hub unit + contract; `bench_harness.py --profile mcp-5-servers`; credential canary suite |
| **P3** | File from turn 1 readable in turn 5 on another worker; attachment readable by tools and sandbox w/o prompt; knowledge ACL = retrieval (negatives); grep bounded | `MountProvider` contracts (all providers × blob backends); `test_path_normalization_fuzz.py`; `WorkspaceRevisionMachine`; `test_grep_redos_bounded`; `test_vfs_session_persistence.py` (worker restart); `workspace-across-turns.spec.ts`; `test_idor_matrix.py::knowledge_*` against Neo4j and Arango; `test_attachment_not_in_prompt.py` (asserts compiled request bytes lack attachment text) |
| **P4** | Cache-read share and p50 TTFT improve vs P0 baseline | `test_cache_stability.py` properties; transport goldens; `ladder_is_journaled`; `harness-diff` replay over the journal corpus (reviewed diff); production cache-read share (SLO #11) and TTFT vs baseline |
| **P5** | Escape/egress/credential probes per tier; 5k-issue script with bounded tokens; no Docker socket in worker; 3-of-5 writes resume; modified script → divergence | `sandbox_security/test_probes.py` per tier (runc, runsc, kind agent-sandbox); `SandboxProvider` contract all tiers; `test_code_mode_rpc.py::test_5k_issue_aggregation_tokens_bounded`; `test_worker_has_no_docker_socket` (compose + Helm render assertion); `test_replay_prefix_no_reexecution` (L1) **and** `fault ... code.after_call_outcome` (L4); `test_modified_script_raises_replay_divergence`; `test_determinism_validator`; incremental materialization property |
| **P6** | deep/planExecute recovery reuses completed children; child cannot exceed grant; budgets across parent + children | `worker.child.after_admit_before_parent_event` fault; policy rows `caller=child`; `test_budget_ledger_reservations.py` (+ Redis `CapacityLedger` contract); `subagent-nesting.spec.ts`; replay journals `deep-*`, `plan-execute-*` in `strict` mode |
| **P7** | Nested binary skill bundle survives import → mount → restart byte-for-byte; artifacts at project/user scope; memory reviewable/erasable | `test_skill_bundle_roundtrip_restart.py` (v1 Gate 8 fixture set) on local/S3/Azure; `artifact-exact-version.spec.ts`; `test_artifact_owner_scope.py`; memory quarantine + erasure tests; archive-abuse security set |
| **P8** | Monday-summary workflow authored, verified, dry-run, approved once, 4 unattended runs; crash mid-run no duplicate post; other member cannot edit/run | `workflow-author-approve-run.spec.ts` (ManualClock trigger ×4); `test_workflow_authz.py`; `fault ... code.after_call_outcome` on workflow bodies; `test_trigger_once_per_occasion.py` incl. clock skew; `authoring-smoke` no-egress probe |
| **P9** | Zero silent loss; zero blind retries of unknown effects; stale fence 100% rejected; recovery ≤ 60 s; reconnect ≤ 3 s; legacy deleted | full fault catalog ×100 on cartesian matrix; linearizability nightly 500 histories clean for 14 consecutive nights; partitions/outages/skew; upgrade/rolling/rollback/backup-restore; 24 h soak SLOs; per-profile smokes; the deterministic injection cases; SSE goldens re-baselined with the legacy path deleted (goldens become the frozen contract) |
| **P10** | One fence/journal/sandbox across chat, agents, workflows, Loom; Loom suite green on core adapters | core port contract suites run from Loom's CI against its adapters; Loom's own suite on core adapters; harness fault catalog run with Loom workflows; `strict` replay of Loom-recorded journals |

### 10.3a Suites for work packages added in the implementation plan v2.0

The rows above use the v1.0 phase numbers. P0 now spans P0a/P0b/P0c, P1 spans P1a/P1b, and P8 spans
P8a/P8b; the durable code runtime moved from P5.8 to P8.10 (its P5 gate items now gate P8b). New WPs map
to suites as follows. Every one of them also adds a row to the security negative set (§7.3).

| WP | Suites |
|---|---|
| P0.11 exfil rendering | Playwright `answer-remote-image-no-request.spec.ts` (network interception asserts zero requests to non-allowlisted hosts), `artifact-preview-isolated-origin.spec.ts` (no same-origin access, `form-action` blocked), CSP header test |
| P0.12 STDIO lockdown | `test_mcp_custom_stdio_forbidden_managed.py`, `test_mcp_template_pinned_no_npx.py` (no network at server start) |
| P0.10 replica set by default | `deployment/docker-compose/tests/standalone_to_rs_migration_test.sh` on real standalone data; `install.sh` fresh-install RS assertion |
| P0.13 flags | flag matrix unit tests; kill-switch routes new runs to legacy only |
| P0.14–P0.18 legacy quick wins | `test_cache_stability.py` against the legacy builder; `bench_harness.py --profile mcp-5-servers`; no-`aiPayload` log assertion |
| P1.12 journal authority | `journal-writer-allowlist.test.ts` (every disallowed event type × writer rejected); attempt-token binding tests; forged worker token from another service's secret rejected |
| P1.13 data protection | envelope-encryption round trip per blob backend; crypto-shred erasure makes payload unreadable; audit chain verifier detects a tampered record |
| P1.14 admission quotas | per-user/org rate + concurrency caps; cost ceiling stops admission; `Last-Event-ID` window cap |
| P1.15 capabilities | probe unit tests per backend; `capabilitiesDigest` pinned; missing capability removes the tool; `capability_lost` suspension on wake |
| P1.16 worker slots | 20 concurrent runs on 1 worker; 2 workers on Kafka with a 1-partition topic both make progress |
| P1.17 degraded modes | §6.4 outage suite rows for Redis, broker and blob |
| P1.18 steering | `input.queued` consumed at the next tool boundary; Playwright `steer-running-task.spec.ts` |
| P1.19 legacy import | import + flag-off rendering in both directions |
| P1.20 rolling upgrades | §7.5 upgrade suite + replay-compat job (journals from N replayed on N+1) |
| P2.4 credential service | issuance audited; refresh-lock race test with rotating refresh tokens (fake-saas); worker process cannot read etcd credential paths or decrypt them (OS-user + KV role test); workspace/admin credentials never issued for `action`; stale attempt token rejected; revocation stops issuance immediately; worker token cache hit rate in `bench_harness.py` |
| P2.13 knowledge publications | caller never sees records outside actor ∩ grant unless published to an audience containing them; `answer_only` denies `fetch_full_record`/download/raw read; published-label content cannot be re-shared outside the audience (flow policy); non-owner cannot create a publication; compatibility-flag runs audited; filter latency vs baseline |
| P2.5a/b labels and flow | policy decision table rows for every (integrity, sink, audience, runKind); declassification only via typed output or interaction; injection suite exfil success = 0 |
| P2.6 interactions | PAT/OAuth app cannot approve; Slack signature required; re-delivered Slack action idempotent; card renders canonical args as text (homoglyph fixture) ; a11y axe scan of the card |
| P2.8 MCP manifest | changed description disables the tool until re-approved; invalid tool name rejected; codegen escaping fixture; SSRF to private range blocked |
| P2.10 loop guards | doom-loop fixture triggers "step back" then suspension; 3/20 denial breaker |
| P3.9 cross-principal | user B reading user A's shared session content re-checks record ACL; dedupe oracle test (existence not observable) |
| P4.7 model policy | 5xx / content-filter fallback journaled; resume after provider switch is provider-valid |
| P5.1 broker topology | Compose and Helm render assertions: no Docker socket in app or worker; broker rejects calls without capability; ref from another org rejected |
| P5.9 pools and snapshots | warm-pool member never reused across orgs; snapshot unreadable by another session |
| P5.10 MCP-runner | STDIO server cannot reach internal addresses, undeclared hosts or other tenants' credentials; unpinned artifact refused; runner never reused across principals; `desktop-eval` reports STDIO unavailable; warm runner reuse latency |
| P6.6 child labels | child result label propagates; child without inherited context starts clean |
| P7.1 archive links | internal symlink/hardlink materialized byte-identical; link escaping root, absolute, dangling, cycle, >8 hops → preview lists them and import fails without explicit exclusion; exclusion recorded in manifest; link-amplification bomb hits the logical-size budget; device/FIFO rejected |
| P7.1 / P7.5 governance | agent-authored skill never auto-preloads for another user; external-labelled memory write quarantined; artifact scope widening requires declassification |
| P8.9 workflow security | non-owner edit disables triggers; disabled owner pauses workflow; self-trigger loop stopped at max chain depth; admin cannot run as owner |
| P9.8 journal auditor | seeded-violation journals are all detected |
| P9.10 pen tests | external reports tracked as release blockers |

### 10.4 v1 plan re-homing

| v1 gate | Where it lives now |
|---|---|
| Gate 0 | L0 + §3.5 |
| Gate 1 | L1 + §3.1 |
| Gate 2 | `SandboxProvider` contract (tier 1) |
| Gate 3 | Node `*.itest.ts` in §5.1 |
| Gate 4 | §5 + §6.2 |
| Gate 5 | §7.1 |
| Gate 6 | §6 |
| Gate 7 | §7.3 |
| Gate 8 | §3.1 + §5.2 |
| Gate 9 | §8.2–8.3 |

The v1 release checklist items stay open until their suites above are green.

---

## 11. First slices (P0, in order)

1. Add `hypothesis`, `hypothesis-jsonschema` and `mutmut` to `[dev]` in `backend/python/pyproject.toml`,
   and `fast-check` + `@stryker-mutator/core` to Node and frontend devDependencies. Register pytest
   markers and Hypothesis profiles.
2. Promote `ScriptedTransport` → core `testing/ScriptedModel`. Build `fake-llm` and the script DSL.
3. Record the legacy SSE/transcript goldens and land `tests/contracts/sse-golden.test.ts` and
   `mcp-package-parser.test.ts` green on `main` (P0.7).
4. `contracts/harness/` schemas + codegen + round-trip script (P0.5).
5. Re-parameterize `execution-store.contract.test.ts` over Mongo (`*.itest.ts`) and add the sweeper
   tests (P0.6).
6. `tests/harness_replay/` record/scrub/replay with one legacy run (P0.4).
