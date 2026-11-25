# Master test plan: scope and strategy

**Status:** plan.
**Covers:** the agent harness (durable and legacy paths, engines, sandbox plane, gateways) and the unified
control plane designed in [`docs/control-plane/`](../control-plane/00-requirements.md).

This plan sits on top of [`docs/agent-harness-v2/TEST-STRATEGY.md`](../agent-harness-v2/TEST-STRATEGY.md) (the
original strategy, which named the tools and lanes) and the cohesion suite C-1..C-28
(`backend/python/tests/cohesion/`). It does not replace them. It adds what they
do not have: one component map, one case list per component and per integration edge, an end-to-end matrix
across the harness options, a trace from requirements to tests, and the work to close the gaps.

| File | What it holds |
|---|---|
| `00-scope-and-strategy.md` | This file: quality goals, risks, levels, entry and exit criteria, data, flakes, environments |
| [`01-component-map.md`](01-component-map.md) | 40 components with boundaries, interfaces, dependencies, files and owners; the component diagram |
| [`02-component-test-plans.md`](02-component-test-plans.md) | Per component: behaviours, levels, numbered cases, fakes, existing tests, gaps |
| [`03-integration-test-plan.md`](03-integration-test-plan.md) | Per edge of the component graph: contract, data flow, errors, idempotency, ordering, skew, degraded dependencies |
| [`04-e2e-test-plan.md`](04-e2e-test-plan.md) | The harness options matrix: must-run scenarios, the pairwise and 3-wise covering arrays, and how a row becomes a test |
| [`05-traceability-and-gaps.md`](05-traceability-and-gaps.md) | Requirements → components → tests, and the ranked gaps |
| [`06-ci-and-execution.md`](06-ci-and-execution.md) | Lanes, durations, gates, commands, flake quarantine, fixtures, live budget |
| `data/` | `components.json` (the component map as data), `e2e-factors.json`, `e2e-must-run.json`, the covering arrays (`e2e-pairwise.*`, `e2e-threeway-core.*`) |
| `generated/` | `inventory.{md,json}` from `scripts/harness_test_inventory.py`; `e2e-matrix.md` from `scripts/e2e_covering_array.py` |

Regenerate the data-driven parts with:

```bash
python3 scripts/harness_test_inventory.py          # tests per component (add --check in review)
python3 scripts/e2e_covering_array.py              # covering arrays (add --check to verify coverage and constraints)
python3 -m unittest discover -s scripts -p 'test_*.py'   # the tools' own tests (CI: main.yml connector-coverage job)
```

---

## 1. Quality goals

Ordered by what a user or an admin loses when the goal is missed.

| # | Goal | Stated as a testable property | Primary evidence |
|---|---|---|---|
| Q1 | **No action the user or admin did not allow** | No external write leaves a run unless the pinned policy allows it, the flow rule holds it after a private read, or a person approved it; a denied or expired request never executes; nothing reaches a destination outside the sandbox's allowlist | Policy vectors, flow-guard unit and e2e cases, egress and confinement (C-13, C-28), red-team lane |
| Q2 | **Exactly-once effects across crashes** | Every write intent has at most one executed side effect; an unknown outcome is reconciled by a read or parked for a person, never retried blindly | Crash-point matrix (`integration-tests/harness/fault`), effect recorder assertions, recovery table |
| Q3 | **A run has the authority it was admitted with, never more** | The pin is the only authority; live config can narrow and never widens; a widening needs a new admission; per-user visibility holds | Pin determinism, `pin ⊓ live ⊑ pin` property, CPD shadow mismatch = 0 |
| Q4 | **No data leaves its tenant or its audience** | Every store and cache is keyed by org; secrets never appear in journal, logs, spans, prompt bytes, sandbox FS or explain output; per-viewer audiences hold in shared sessions | Cross-org cases per port, canary scan, audience projection tests |
| Q5 | **Runs finish or say why** | No run waits silently; a queued run shows a waiting frame and meets its claim deadline; every refusal carries a typed, translated reason and a next action | Claim-deadline tests, refusal vocabulary (C-6), UX3 cases |
| Q6 | **The durable path is invisible when it works** | Model requests and outputs equal today's loop for the same inputs (C-24); SLO #2–#5 latency budgets hold; stop and approval resume are prompt | C-24 wire parity, `bench_harness.py`, SLO gates |
| Q7 | **Erasure is complete and only on purpose** | Deleting a conversation erases every copy (messages, citations, artifacts, workspaces, sandbox folders, journal, keys); nothing live is erased; the backfill is digest-bound and idempotent | C-14, erasure contract suites, backfill itests |
| Q8 | **Upgrades and rollbacks are safe mid-flight** | Either upgrade order (worker first or Node first) is safe; migrations dry-run, apply and revert; the kill switch moves new turns to legacy without breaking runs in flight | Upgrade suite, migration tests, kill-switch mid-flight test |
| Q9 | **Every option combination that ships works** | Each supported combination of mode, path, engine, provider, tools, sandbox, stores and deployment passes its scripted e2e; high-risk combinations are explicit | 04 must-run list, covering arrays |
| Q10 | **The test suite tells the truth** | Every lane that gates runs in CI; a skip in a real-service lane fails the job; no retries hide flakes; every test can fail | CI-1/CI-2 (landed by FX), C-25, `--strict-markers`, mutation checks |

## 2. Risks, ranked

Impact and likelihood are 1–5. Likelihood is judged from the current code and the R1–R3 reviews, not from
the design's intent. Score = impact × likelihood. The ranked **gaps** that follow from these risks are in
[05](05-traceability-and-gaps.md) §3.

| Rank | Risk | Impact | Likelihood | Score | Why this likelihood | Components |
|---|---|---|---|---|---|---|
| 1 | **Authority wider than admitted.** Live reads of approvals, flow rule, LLM config, chat-mode policy and skills change a run in flight (R2 F2); before CPS also skills leaking across users (F3), pins not bound to approved descriptors (F4), stricter-only gaps (F5) and stale next-run grants (F6) | 5 | 4 | 20 | Verified in code by R2. CPS (merged at `a168c7d5c`) fixed F3–F7 with tests; F2 waits for CPD-6, so the likelihood stays 4 until then | PIN, POL, APR, SKL, MCP, TSC, ADM |
| 2 | **External write without consent** after a private read, through any path (loop, engine, legacy, regenerate, child, schedule) | 5 | 3 | 15 | Flow guard on by default since UX1 and applied to legacy since UX3; regenerate durable since UX3; the e2e lanes that prove it across paths are dev-only | APR, TGW, LEG, RTE, CHD, SCH |
| 3 | **Duplicate or lost side effects** on crash, retry, duplicate commit or lost acknowledgement | 5 | 3 | 15 | Crash matrix exists and runs nightly; FX fixed a replayed-commit bug (M1); reconcile hooks cover two tools only | JRN, WRK, TGW, APR |
| 4 | **The suite is inert or lies**: lanes not run in CI, Docker skips, browser harness specs only on a dev script, wall-clock and sleep-based tests | 4 | 4 | 16 | FX landed CI-1/CI-2; the durable Playwright specs still run only through `deployment/e2e/run.sh` (dev-only); 74 sleeps and ~8 wall-clock asserts (R3) | KIT, all |
| 5 | **Version skew in a rolling upgrade** (worker vs Node, RunSpec and fold versions, control-plane schema versions) | 4 | 3 | 12 | UX2 added `nodeCapabilities` both orders; CPD adds new schemas and a worker feature `resolved_config` | CON, JRN, PIN, DEP |
| 6 | **Control-plane migration changes authority unnoticed** (resolver switch, store migration, credential migration) | 5 | 3 | 15 | CPD-4/5/7/8 are high-risk by their own plan; shadow mode is the mitigation and must be gated by tests | PIN, CST, TSC, CPA |
| 7 | **Silent or stuck runs** (no claimer, lost hint, unknown effect, sub-agent approval not lifted) | 4 | 2 | 8 | UX3 fixed F1, F3, F4; the 90 s claim deadline and waiting frame exist; fault coverage of the notify paths (LAT1) is new | WRK, ADM, APR, CHD, PRJ |
| 8 | **Cross-tenant leakage** through caches, KV documents or audit | 5 | 2 | 10 | Deployment-wide `{orgs:{…}}` documents today (R2 F8); CPD caches keyed by org and revision are new code | CST, PIN, NTF, AUD |
| 9 | **Sandbox escape or egress bypass** | 5 | 2 | 10 | C-13, C-28 and SEC1–SEC2 cover runc; gVisor, Kata and K8s need runners a plain Docker host lacks; O-5/O-6 deferred to a human pen test | SBX, EGR, ENG |
| 10 | **Data loss**: incomplete erasure, erasing a live conversation, workspace copy-out loss, memory proposals discarded | 5 | 2 | 10 | E7B fixed live erasure; FX fixed M1, M2, M4; backfill on real backends runs only in path-filtered lanes | ERA, WSP, MEM |
| 11 | **Latency regressions** users feel (stop, approval resume, deep waves, engine start, TTFT) | 3 | 4 | 12 | R1 measured seconds-scale waits; UX3 and LAT1 address them; `bench_harness.py` has one profile (`short-turn`) | RTE, ADM, WRK, PRJ, PIN |
| 12 | **Durable and legacy drift** in what users see or what the model receives | 3 | 3 | 9 | C-24 compares 148 cases offline; the browser-level transcript parity (live == reload) is unit-level only | WRK, PRJ, LEG, FEC |
| 13 | **Backend-specific failure** (ArangoDB, etcd, Kafka, Redis Streams, Helm) | 3 | 3 | 9 | Harness lanes run Neo4j + Redis + Redis Streams + Compose by default; the others run nightly or by hand | KNW, LAB, JRN, DEP |
| 14 | **Cost runaway** (output, retries, children, schedules) | 3 | 2 | 6 | P4.17 bounds output; ledger reserves at the limit; org and agent budgets are CP7 (not built) | BUD, MGW, SCH |
| 15 | **Unreadable or inaccessible UI** (English-only errors, raw codes, missing ARIA) | 2 | 4 | 8 | R1 F22–F34; UX3 fixed several; no a11y or i18n lane gates harness screens | FEC, FEA |

## 3. Test levels

This plan's levels, their definition and how they map to TEST-STRATEGY's layers and to the `level` column
that `scripts/harness_test_inventory.py` derives from a test file's path and markers.

| Code | Level | Definition | May use | May not use | TEST-STRATEGY | Inventory level |
|---|---|---|---|---|---|---|
| **S** | Static | Types, lint, import boundaries, codegen drift, flag matrix, route inventory | ruff, pyright, tsc, ESLint, import-linter, dependency-cruiser, C-5, C-16, C-26 | — | L0 | `cohesion` (guards) |
| **U** | Unit | One module or class, in process, deterministic | Test kit (`ManualClock`, `ScriptedModel`, seeded ids, failpoints), in-memory adapters that pass their port contract, `fakeredis`, `sinon` fake timers, jsdom | Network, Docker, real stores, wall clock, `sleep` > 0 | L1 | `unit` |
| **P** | Property / model-based | Invariants over generated inputs or command sequences (a kind of U) | Hypothesis (+ stateful), fast-check | Same as U | L1p | `unit` |
| **K** | Contract | **Every port against every adapter**: one suite body, a thin subclass per adapter (in-memory in U; real backends in I). Also cross-language vectors and wire goldens | The suite body in `harness_core/testing/contracts` or `tests/.../support/*-contract.ts`; vectors under `contracts/harness/vectors` | Adapter-specific assertions in the shared body | L2 | `contract` |
| **C** | Component | One deployable process or Node module behind its real HTTP surface, dependencies faked at the port | supertest/in-process Express, FastAPI test client, in-memory adapters | Other services, real stores | L2/L3 | `component` |
| **I** | Integration | One component against real infrastructure: Mongo replica set, Redis, a graph DB, a broker, the Docker sandbox | Compose services, `*.itest.ts` with `mocha --no-config`, `-m docker`, `-m integration` | More than one real backend per job when the job tests a backend (a red job names one backend) | L3 | `integration`, `integration-docker` |
| **X** | Cross-service integration | Two or more processes over their real wire: Node ↔ Python worker ↔ broker ↔ sandbox broker ↔ egress proxy ↔ gateways ↔ engines | The harness integration stack, fake LLM and fake SaaS, failpoints, `docker kill` | Real model, real SaaS | L3/L4 | `fault`, `replay` |
| **E** | End to end | Browser (or Slack/MCP client) → Node → services → worker → sandbox → model, through the shipped deployment. **Es** scripted (fake LLM, CI) and **El** live (real models, budgeted) | Compose or Helm stack, Playwright, `deployment/e2e/run.sh`, the published `@pipeshub-ai/mcp` client | Mocks inside the stack (browser `route()` mocks belong to U) | L5, L7 | `e2e-browser`, `e2e-client`, `live` |
| **N** | Non-functional | **N-lat** latency per mode against SLO.md; **N-load** load and **N-soak** 6 h/24 h soak; **N-chaos** crash points, partitions, dependency outages, crash-resume; **N-sec** security and red-team; **N-upg** upgrade, migration and rollback; **N-ux** i18n and accessibility | `bench_harness.py`, k6, toxiproxy, failpoints, libfaketime, red-team cases, axe-core, i18n parity scripts | — | L4, L6, L7 | `perf`, `fault`, `security`, `resilience`, `gate-slow`, `deployment` |

**Placement rule.** A case goes to the lowest level that can make it fail for the right reason. A crash
property goes to U with failpoints if the fold and the commit rules decide it; it goes to X only when the
claim is about two processes (for example "the run resumes on another worker after a SIGKILL"). Durability
claims always have at least one I or X case on real persistence (TEST-STRATEGY principle 7).

**Shape today vs target.** From the generated inventory (1,400+ files mapped): unit-heavy (about 80% of
mapped files), a healthy contract layer for the journal, execution store, workspace and sandbox ports, a
Docker lane, and a nightly crash matrix. Thin: component tests of the Python services over HTTP, browser
e2e in CI for durable chat, cross-store matrices for the harness, latency profiles beyond `short-turn`,
a11y and i18n. Empty: the control-plane core (target design; CPD-0..2 are in flight on their branches).

### 3.1 Entry and exit criteria per level

| Level | Entry (before tests at this level are written or run) | Exit (the level passes for a change) |
|---|---|---|
| S | Code compiles; generated code regenerated | Zero new lint findings vs base; codegen, flag-matrix and route-inventory drift zero; import contracts kept |
| U/P | Port and fakes exist; the fake passes the port contract; clock and ids injectable | 100% pass, no skips outside documented platform skips; property profile `ci` (200 examples, derandomized); new code reaches the module's coverage floor (Python 89.8% overall gate; Node c8 95.3/95.52/86.5/95.3 over unit tests and store itests together, [06](06-ci-and-execution.md) §1a) |
| K | The port is declared in `ports.py`/`ports/*.ts` and registered with C-2 | Every adapter (in-memory and each production adapter) runs the same suite; a real-backend subclass skips only in its fixture, and the backend lane fails on any skip |
| C | The component starts in-process with its router guards | Every route's auth, validation and error shapes covered; OpenAPI route coverage (C-10/`openapi-harness-routes`) green |
| I | Compose service or Docker image available in the lane; env discovery var set | Zero skips in the lane (`PXH_REQUIRE_DOCKER=1`, backend-matrix rule); journal history checks pass after each test |
| X | The harness stack comes up healthy (`deployment/e2e/run.sh up` or the fault stack) | Every crash point and outage scenario in scope ends in the documented state; no orphan containers, leases or outbox rows afterwards |
| Es | Seeded org, admin, scripted model, agents; stack healthy; the row's factor values applied | Every must-run scenario and every covering-array row assigned to the lane passes; assertions on journal, effects and audit hold, not only the UI |
| El | Budget approved for the release; keys present; registered run plan committed before running | Pass rules of the lane's registered plan (for example k = 3, 2/3 per cell for the live matrix); spend within budget; a failure is a finding, not a retry |
| N | Baseline recorded for the environment label; thresholds in SLO.md | Gates in SLO.md hold; soak leaves no orphans; red-team injection success 0 on scripted attacks; a11y has no serious or critical axe violations on harness screens |

## 4. Test data strategy

1. **Synthetic by default.** Seeds come from `deployment/e2e/seed.py` and `seed_config.py` (org, admin, scripted
   model, agents), the fake worlds under `backend/python/tests/evals/` and `tests/harness_replay/world.py`, and
   the fake SaaS (`deployment/e2e/fake_saas/server.py`) and fake LLM (`deployment/e2e/fake_llm/server.py`). No
   production data in any lane.
2. **Recorded journals are fixtures, scrubbed.** Replay corpora live in `contracts/harness/vectors/journals/`
   and `vectors/replay-corpus/`; a new recording goes through the scrubber and canary scan
   (TEST-STRATEGY §4.6) and is reviewed like an API change.
3. **Vectors are the cross-language truth.** Approvals, profiles, sealing, token patterns, worker credentials
   and (target) policy resolution, effective sets, digests and authz are JSON vectors read by both languages.
   A rule that exists in two languages without a vector is a gap.
4. **Two orgs, two users, always.** Every integration and e2e seed creates org A and org B, each with an
   admin and a member, so cross-org and cross-user cases are cheap to write.
5. **Canary secrets.** Credentials in tests are `pipeshub-canary-<uuid>`; scanners assert they appear nowhere
   but the vault (journal, logs, spans, prompts, sandbox FS, explain output, audit).
6. **Determinism.** Time from `ManualClock` (Python) or `sinon` fake timers (Node); ids from `SeededIds`;
   model output from `ScriptedModel`/fake LLM scripts; hash seeds varied in property tests to catch ordering bugs.
7. **Fixture hygiene.** One fixture per concept in the nearest `conftest.py` (R3 found `_deployment` copied
   in 17 files, `clock` in 11); fixtures that start containers are session-scoped and labelled
   (`io.pipeshub.e2e.run=<run>`) so teardown removes only what the run created.

## 5. Flake policy

1. **Root-cause, never retry.** No rerun plugin in Python; Node mocha has no retries. Playwright's `retries: 2`
   on CI (`frontend/playwright.config.ts:37`) applies to the existing projects; the harness specs run with
   `retries: 0`.
2. **A flake is a bug** in the product or the test, owned by the component's owner. Quarantine is allowed
   only with a ticket, an owner and an expiry: mark `@pytest.mark.quarantine` (to be registered in
   `pytest.ini` with C-25's lane registry) or `test.fixme` with the ticket id. Quarantined tests still run in
   a non-gating job so the failure rate is visible.
3. **SLA:** root cause within 2 working days for a gating lane, 5 for nightly; a quarantine older than
   10 working days blocks the owner's next merge.
4. **Known patterns to remove** (R3 §4): sleep-then-assert-nothing (replace with waiting on an event), wall-clock
   bounds (inject `ManualClock` or fake timers), order-dependent fixtures under xdist (for example
   `test_model_egress` recording an outbound dial only under xdist, noted in the execution plan), and real DNS in
   unit tests (`test_egress_state.py:274`). The load flake PL-3 was a product bug
   (the local executor's process budget shared with the whole account), fixed by H10.
5. **Security tests are never quarantined** (TEST-STRATEGY §7.3).

## 6. Environments

| Environment | What it can run | What it cannot | Lanes |
|---|---|---|---|
| **Local dev** (laptop, Docker Desktop) | S, U, P, K (in-memory), C; I with the shared Mongo RS and Redis containers; Es via `deployment/e2e/run.sh` | gVisor, Kata, K8s; live models without keys | `scripts/verify.sh` |
| **CI, GitHub-hosted** (ubuntu, 4 CPU runner for heavy jobs) | S, U, K, C; I for Mongo RS, Redis, brokers, graph DBs, Docker runc; X (fault suite); Es (compose); Helm on kind | gVisor (no `runsc` installed), Kata (no KVM), live models from forks | `unit-test.yml`, `agent-harness-infrastructure.yml`, `harness-fault.yml`, `harness-replay-compat.yml`, `backend-matrix.yml`, `integration-tests.yml`, `e2e-smoke.yml`, `helm-chart.yml`, `published-hub-smoke.yml` |
| **Single-host Docker lane** (runc) | Real broker, egress proxy, engines (opencode, Codex), MCP runners, snapshots, warm pool, C-13, C-28 | gVisor, Kata, KVM (about 190 cases skip by platform, E2E3); Claude Code until its pin is admitted | `-m "docker and not slow"`; engine crash nightly |
| **Compose e2e stack** (`deployment/e2e/run.sh`) | Browser → Node → worker → broker → opencode → egress → gateways, scripted model and SaaS | Live models (unless pointed at them) | Es, El (with keys) |
| **gVisor runner** (needs `runsc`) | Isolation-class cases (`runsc` tier), Landlock interplay (SEC3 findings) | — | Planned: `harness-isolation.yml`. **Needs a runner with `runsc`** |
| **Kata / KVM runner** | Kata isolation class, microVM cold start (SLO #17 tier 2) | — | Planned, same workflow. **Needs a runner with KVM** |
| **Kubernetes** (kind for smoke; a real cluster for the agent-sandbox controller) | Helm install, agent-sandbox provider contract, network policies | Kind cannot prove kernel isolation | `helm-chart.yml` (kind smoke today); agent-sandbox contract planned (QA-25) |
| **Live-model lane** | El scenarios, live engine matrix | Forks (no secrets) | maintainer-triggered live lanes with a registered plan and a budget ([06](06-ci-and-execution.md) §7) |

Where a requirement can only be proven in an environment a plain Docker host lacks (gVisor, Kata, a real K8s cluster,
Claude Code with an admitted pin), the case is written now, marked with the lane that will run it, and listed
as a gap with its lane in [05](05-traceability-and-gaps.md).
