# Master test plan: CI and execution

**Status:** plan, 2026-10-09. Workflows are under `.github/workflows/`; durations are the workflows'
`timeout-minutes` (a ceiling) and, where a gate run recorded one, the measured time on this project's host.
"New" rows are proposed and do not exist yet.

## 1. Lanes

| Lane | Trigger | Workflow / command | Levels | Time (ceiling) | Gate |
|---|---|---|---|---|---|
| **Lint and types** | every PR | `main.yml`: ruff on changed Python files (warning only), F811 shadowed tests (fails), pyright on changed files, ESLint on changed frontend files (warning only), actionlint | S | 10 min | required (F811, pyright, actionlint) |
| **Scripts and coverage tools** | every PR | `main.yml` job `connector-coverage`: `python3 -m unittest discover -s scripts -p 'test_*.py'` (runs this plan's `test_harness_test_inventory.py` and `test_e2e_covering_array.py`) | U | 2 min | required |
| **Plan data check** (new, QA-35) | PRs touching `docs/test-plan/data/**`, `scripts/*inventory*`, `scripts/e2e_covering_array.py`, or any test tree | `python3 scripts/e2e_covering_array.py --check`; `python3 scripts/harness_test_inventory.py --check` | S | 5 min | required |
| **Node unit and coverage gate** | PRs touching Node | `unit-test.yml` `nodejs-unit-tests`, with a MongoDB replica set and Redis: `npm run test:coverage-gate` = `npm test` (unit; holds the `domain/` and `application/` floor) + `npm run test:itest` (`tests/modules/**/*.mongo.itest.ts`, `*.redis.itest.ts`) + `npm run coverage:check` (one threshold check, `.c8rc.json`, on the V8 coverage of both runs) | U, K, C, cohesion, I (store adapters) | ~25 min | required; coverage gate (§1a) |
| **Python unit** | PRs touching Python | `unit-test.yml` `python-unit-tests`: `coverage run -m pytest tests/unit` (fail under 89.8); `pytest tests/evals tests/harness_replay`; `pytest tests/cohesion tests/contracts -m "not docker and not slow"` (CI-1); `pytest tests/integration` (no infra) | U, P, K, replay, cohesion | ~40 min | required; coverage gate |
| **Frontend unit** | every PR (`main.yml`) | `npm run i18n:check && npm run i18n:keys && npm run test:i18n`; `npm run test:unit:coverage` (vitest over `app/**` and `lib/**`, thresholds in `frontend/vitest.config.ts`) | U, N-ux (keys only) | ~5 min | required; coverage thresholds |
| **Harness infrastructure** | PRs touching the harness paths (path filter widened by FX CI-2) | `agent-harness-infrastructure.yml`: `mongodb-replica-set` (`agent_harness_replset_test.sh`: all `*.mongo.itest.ts`, with Redis), `journal-failover` (3-member RS), `mongodb-replica-set-default`, `sandbox-contract`, `harness-docker-lane` (`pytest -n 2 -m "docker and not slow" tests/unit tests/cohesion`, `PXH_REQUIRE_DOCKER=1`) | I | 30 + 15 + 15 + 25 + 75 min | required for those paths |
| **Replay compatibility** | PRs touching harness or contracts; pushes | `harness-replay-compat.yml`: `tests/contracts/test_replay_compat.py` and Node `replay-compat.test.ts` against this and earlier refs' goldens | K, replay | 30 min | required for those paths |
| **Fault (crash points)** | nightly 18:30 UTC; PRs; manual with broker choice | `harness-fault.yml`: `integration-tests/harness/fault` per broker (Redis Streams, Kafka) | X, N-chaos | 60 min per broker | PR: required for harness paths; nightly red blocks the release candidate |
| **Engine crash nightly** | nightly 20:30 UTC | scheduled job, maintained outside this repository (G4 matrix, opencode image built in the job) | X, N-chaos | 240 min | nightly; blocks release candidate |
| **Backend matrix** | PRs touching store adapters; nightly 03:00 | `backend-matrix.yml`: one real backend per job, discovered by env var, any skip fails | I, K | 30 min per job | required for those paths |
| **Storage backends** | PRs touching storage; nightly | `storage-backends.yml`: `tests/stores/*.itest.ts` | I | 20 min | required for those paths |
| **Full-stack integration** | PRs; nightly 18:30 | `integration-tests.yml`: connector, resilience, storage, security, permissions shards on Neo4j and ArangoDB, plus the full Playwright suite | I, E | 300 min | nightly |
| **Browser smoke** | PRs to `main` | `e2e-smoke.yml`: `npm run test:e2e:smoke` (`@smoke`) | E | 60 min | required |
| **Harness e2e PR** (new, QA-5) | PRs touching harness, chat or contracts paths | `harness-e2e.yml` job `es-pr`: `deployment/e2e/run.sh` stack (ST-1) + the Es-PR must-runs (EM-01, 02, 03, 13, 32) in a `harness` Playwright project, `retries: 0` | Es | ≤ 20 min | required for those paths |
| **Harness e2e nightly** (QA-6) | nightly | `harness-e2e.yml` job `es-matrix`: the pairwise and 3-wise rows of the night's stacks (ST-1 every night, one Compose and one Helm stack in rotation, `integration-tests/harness/e2e/rotation.py`); the Es-nightly must-runs stay the browser jobs' | Es | ≤ 120 min | blocks release candidate |
| **Harness e2e weekly** (QA-6) | weekly (Saturday) | the same job, every array row on all seven stacks (the Helm ones once the kind lane has the app image: QA6-F3) | Es | ≤ 6 h | release candidate |
| **Perf** | weekly (Wed 21:00); manual | `perf-harness.yml`: `bench_harness.py --profile short-turn` + `compare.py --fail-on-regression` (SLO #2); new profiles from QA-22 | N-lat | 150 min | weekly; release |
| **Footprint** | PRs touching compose; weekly | `published-hub-smoke.yml` (`eval` with `PIPESHUB_SMOKE_FOOTPRINT=1`) | N | 40 min | required for those paths |
| **Helm** | PRs touching `deployment/helm/**`; weekly | `helm-chart.yml`: `check_chart.sh`, `kind_smoke.sh` (+ EM-34 turn, QA-30) | E, deployment | 15 + 45 min | required for those paths |
| **Answer-quality evals** (existing) | as the workflow schedules them | `answer-quality-evals.yml` | — | 30 min | not a harness gate |
| **Isolation** (new, QA-25) | release; manual | `harness-isolation.yml` on a runner with `runsc` and KVM: C-13, C-28, broker contract, EM-42, EM-43 | I, Es, N-sec | 60 min | release; **needs a runner with `runsc` and KVM** |
| **Live release** (new, maintainer-triggered) | release candidate | `workflow_dispatch` with a registered plan id and a budget input: EM-18, EM-39, EM-44, live subset of EM-01/16/17 | El | budgeted | release |
| **Soak** (new, QA-34) | weekly 6 h; release 24 h | `perf-harness.yml` soak job (SLO #19) | N-soak | 6 h / 24 h | release |
| **Full QA** (QA-AUTO) | before a release candidate; on request | `scripts/qa_full.sh --out <dir>` (`scripts/qa_full.py`): every lane above that runs on one host: unit and cohesion, contracts, replay, integration, frontend, the Mongo/Redis/PostgreSQL itests, the UX screenshots against baselines, browser latency budgets (SLO.md "Browser latency"), the `harness`, `authenticated` and `qa-indexed` Playwright projects on one stack with indexing, Docker (sandbox and `run.sh durable`), the perf rig (PF-05, SLO rows, a 6-minute soak) and the human-test stack's smoke. One `index.html` and `report.json`; a failed test is rerun once alone and labelled flaky or real, never retried green | all but El | ~4 h | release (a real failure blocks; a flaky one is root-caused, §4) |
| **Final QA** (QA-FINAL) | the final pass, once features freeze; on request | `scripts/qa_final.sh --out <dir>` (`qa_full.py --final`): every Full QA lane plus the reported cases' regressions first and the lanes with stacks of their own (MCP-E2E hub-on modules, options matrix ST-1, PostgreSQL / no route out / key rotation stacks, fault suite), then `final.md` marking each row of [QA-FINAL](QA-FINAL.md); `--sample` proves the lanes in minutes each | all | ~8 h (sample ~3 h) | release |

### 1a. The Node coverage gate (COV1)

Store adapters are tested on real stores (level I), so the gate counts those tests: unit tests and the Mongo and
Redis itests write V8 coverage into one directory (`.c8_output`), and `c8 report --check-coverage` checks the two
together. A unit test over a fake collection, written only to move the figure, is no longer needed and should not
be written.

Each store is measured in the lane that runs it (WP-121, after main's merge): the gate counts the Mongo and Redis
adapters and leaves out every PostgreSQL store (`src/libs/repositories/**/pg.ts`, main's own exclusion one level
deeper); the harness's PostgreSQL stores carry their own floor in `test:pg-harness`, which runs them. The gate has two
floors: all of `src` at main's own level, since main's code is not ours to raise here, and the harness's own code at
what it measured, so it cannot slip.

| Check | Where | Lines | Statements | Functions | Branches | Measured at `21d028e3cc` (WP-121) |
|---|---|---|---|---|---|---|
| Unit + itests, all of `src` but the PostgreSQL stores | `.c8rc.json`, `npm run coverage:check` | 89 | 89 | 90 | 80 | 93.79 / 93.79 / 92.38 (10414 of 11272) / 88.23 |
| Unit + itests, `src/modules/agent-harness`, `src/modules/control-plane`, `src/libs/repositories/agent-harness` (no `pg.ts`) | `coverage:harness` in `package.json`, run by `coverage:check` | 96.7 | 96.7 | 94.4 | 90.4 | 96.73 / 96.73 / 94.46 (6028 of 6381) / 90.42 |
| PostgreSQL itests, `src/libs/repositories/{agent-harness,control-plane}/**/pg.ts` | `.c8rc.pg-harness.json`, `npm run test:pg-harness` (needs the PostgreSQL stack; without it the suites skip and the floor fails) | 96.4 | 96.4 | 95.8 | 87.8 | 96.47 / 96.47 / 96.02 (701 of 730) / 88.06 |
| Unit only, `src/modules/*/domain/**` and `*/application/**` | `coverage:logic` in `package.json`, run by `npm test` | 97.2 | 97.2 | 97.2 | 90 | 97.25 / 97.25 / 97.23 / 90.33 |

- `npm test` is unit-only, reports coverage, and fails only on the logic floor: pure logic cannot be covered by an
  itest alone. Unit tests alone cover 92.01 / 92.01 / 90.02 / 86.16 of all of `src`.
- `npm run test:coverage-gate` needs `MONGO_IT_URI` (a replica set) and `REDIS_IT_URL`, and says so when one is
  missing. `npm run test:itest` after `npm test` adds to the same coverage, so nothing runs twice.
- Function and line counts were the same in three runs of the same tree; branch counts moved by up to 43, so the
  branch thresholds carry more room. The function threshold has room for one function at `bc20a507d`: an adapter
  function that loses its only test fails the gate (shown on `MongoOrgHeadLeases.advance`). Only raise these numbers; when a change adds tested code, raise them to the new figures.

## 2. Test levels → commands

| Level | Python | Node | Frontend / stack |
|---|---|---|---|
| S | `ruff check`, `pyright`, `lint-imports`, `pytest tests/cohesion -m "not docker and not slow"`, `bash contracts/harness/generate.sh && git diff --exit-code` | `npx tsc --noEmit`, `npx eslint`, `tests/cohesion/*.test.ts` (in the mocha run) | `npm run lint` |
| U, P | `pytest tests/unit/harness_core tests/unit/harness tests/unit/harness_engines -m "not docker and not slow" -n 4`; nightly property profile `--hypothesis-profile=nightly` (QA-13) | `npm test` (mocha, global ioredis/kafkajs/MCP mocks; coverage floor for `domain/` and `application/`) | `npm run test:unit` |
| K | in-memory subclasses run with U; real subclasses with I | `tests/modules/agent-harness/*.contract.test.ts` and `support/*-contract.ts` via the test files | `tests/contracts` (Node) |
| C | FastAPI test client cases inside `tests/unit` | `*.http.test.ts` | — |
| I | `pytest -m docker` with `PXH_REQUIRE_DOCKER=1` and `SANDBOX_TEST_IMAGE`; `pytest -m integration` in `backend-matrix.yml` | `npm run test:itest` (Mongo and Redis itests under `tests/modules`, counted by the coverage gate); the same Mongo files via `deployment/docker-compose/tests/agent_harness_replset_test.sh` | — |
| X | `cd integration-tests/harness/fault && python -m pytest --broker redis` (and `kafka`) | — | — |
| Es | `integration-tests/harness/e2e` (QA-6) | — | `bash deployment/e2e/run.sh` (all), `run.sh durable`; `npx playwright test --project=harness` (QA-5); `cd integration-tests/harness/e2e && python -m pytest --stacks=ST-1` (QA-6) |
| El | `pytest -m slow tests/unit/harness/engines/test_e2e4_live_matrix.py` with keys exported from `integration-tests/.env.local` | — | `run.sh durable` pointed at a live model |
| N-lat | `python integration-tests/perf/bench_harness.py --profile short-turn` then `compare.py` | `claim-admission.mongo.bench.ts` | — |
| N-chaos | fault suite; engine crash nightly; failpoints armed from a file | `journal-failover.rs3.itest.ts` | — |
| N-upg | `integration-tests/harness/fault/test_upgrades.py`; `deployment/docker-compose/tests/upgrade_smoke.sh` | `harness-migrations.mongo.itest.ts` | — |
| N-sec | `tests/unit/harness/security/redteam`, C-13, C-28, SEC cases in `tests/unit/harness*` | `c26-internal-route-guards.test.ts` | — |
| N-ux | — | — | axe in the `harness` Playwright project; `npm run test:i18n` (QA-32) |

Local: `scripts/verify.sh` runs shell, Python (unit, cohesion and contracts, evals, integration without infra),
frontend vitest and Node mocha; `scripts/verify.sh --list` shows what would run.

## 3. Gating rules

1. **A PR is mergeable** when every required check for its paths is green: lint and types, scripts, unit (Python,
   Node, frontend) with coverage gates, and for harness paths the infrastructure, replay-compatibility, fault (both
   brokers) and harness e2e PR lanes.
2. **Path filters are a risk, not a saving.** A filter that misses a dependency lets a break through (R3 found
   `agent-harness-infrastructure.yml` skipped for `contracts/harness/**` changes; FX widened it). Rule: every
   harness lane also runs on every push to the integration branch and nightly, so a filtered miss is caught within
   a day; QA-35 adds a check that each harness lane's filter covers the files its tests import.
3. **Nightly red blocks the next release candidate**, not open PRs; the owner of the failing component has the
   flake SLA of 00 §5 to fix or quarantine with a ticket.
4. **Release gate:** all nightly and weekly lanes green on the candidate commit; the Es-weekly run; El lanes within
   budget and passing their registered rules; SLO gates in [SLO.md](../agent-harness-v2/SLO.md) for the release
   rows; the upgrade suite with runs in flight; isolation lane once QA-25's runner exists (until then the release
   notes say runc only was verified).
5. **Counts only grow** (the execution plan §5 regression rule): a test removed must be named in the PR with the test
   that replaces it; `scripts/harness_test_inventory.py --check` makes a removal visible in review.
6. **Skips:** a real-service lane fails on any skip (backend matrix rule; `PXH_REQUIRE_DOCKER=1` for Docker). A
   platform skip (no `runsc`, no KVM) is allowed only with a reason string naming the
   lane that runs the case.

## 4. Flake quarantine

| Step | Mechanism |
|---|---|
| Detect | the nightly reruns nothing; a test that fails once on a lane and passes on the next run is logged as a flake from the junit history (`--junitxml` artifacts already uploaded by the fault and replay lanes) |
| Own | the component's owner role from [`data/components.json`](data/components.json) |
| Quarantine | Python: `@pytest.mark.quarantine(reason="<ticket>")`, registered in `pytest.ini` and in C-25's lane registry, deselected from gating jobs with `-m "not quarantine"` and run in a non-gating job; Node: `it.skip` is not used, the test moves to `tests/quarantine/` with the ticket in its title; Playwright: `test.fixme(true, '<ticket>')` |
| Root-cause SLA | 2 working days for gating lanes, 5 for nightly; a quarantine older than 10 working days blocks the owner's next merge |
| Never | retries, rerun plugins, raised timeouts without a cause, quarantining a security test |

Known flakes to root-cause first (QA-20): PL-3 (`tests/unit/agent_loop_lib/sandbox/test_executor.py:154`), the
xdist order dependence of `test_model_egress` (the execution plan status), sleep-then-assert tests
(`tests/unit/harness_core/gateway/test_endpoint_contract.py:66,105-116`, `tests/unit/harness/test_run_claimer.py:287`,
`tests/unit/harness_core/sandbox/egress/test_server.py:219`), real DNS in `tests/unit/harness/test_egress_state.py:274`.

## 5. Test data and fixture hygiene

1. Seeds and fakes as in [00](00-scope-and-strategy.md) §4. Every container a lane starts carries its run label
   (`io.pipeshub.e2e.run=<run>`, `pipeshub.harness.owner=<owner>`) and teardown removes only what it labelled; the
   development host's Docker daemon is shared.
2. One conftest per directory holds its fixtures; `tests/unit/harness/assembly/` gets one for `_deployment`
   (copied in 17 files today); `clock`, `_pinned` and `_spec` move to shared fixtures (QA-33).
3. In-memory adapters live in test support, not production packages (R3 M14; QA-33), and each passes its port's
   contract suite (C-2, extended to production adapters by QA-19).
4. Goldens and vectors change only with a reviewed diff; `--update-goldens` is an option, never an env var.
5. Shared worlds use spec'd mocks (`AsyncMock(spec=IGraphDBProvider)`) or `tests/support/fake_record_graph.py`, never
   bare `MagicMock` graphs (R3 risk 6).

## 6. Time budget per change

| Change touches | Lanes that must pass before merge | Wall time (parallel) |
|---|---|---|
| Docs only | lint | < 5 min |
| Frontend chat or admin | frontend unit, browser smoke, harness e2e PR | ~ 60 min |
| Node harness module | Node unit, infrastructure, replay compat, fault, harness e2e PR | ~ 75 min |
| Python harness or engines | Python unit, infrastructure (incl. Docker lane), replay compat, fault, harness e2e PR | ~ 75 min |
| Contracts | all of the above | ~ 75 min |
| Helm or compose | helm or footprint lanes, harness e2e PR | ~ 60 min |

## 7. Live lanes

Lanes that call a real model run only where an operator supplies a key and opts in; no default lane needs one.

## 8. Reporting

- Every lane uploads junit XML; the nightly summary lists failures by component (path → component via
  `data/components.json`).
- `generated/inventory.md` is regenerated in the PR that adds or moves tests; `--check` fails a stale one (QA-35).
- The e2e lanes write one line per row: row id, stack, oracle expectation, observed route, card, outcome, effects,
  pass or fail, so a red row shows which factor combination broke.
