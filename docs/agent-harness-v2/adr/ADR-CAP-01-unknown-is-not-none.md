# ADR-CAP-01: A capability nobody could ask about is unknown, not absent

- Amends: P5.11 (an agent loop run admitted while a code sandbox is proved requires `agent_code`), DUR2 (a guarded worker feature is required only while a live worker advertises it), REPORT 11 (capabilities come from probes and keep their least value when nothing proved them).
- Related: [D-3] (a missing capability removes tools; it never weakens isolation), O-3 (a claim fails closed before the first probe).
- Found by: R31-UIFU (R31UIFU-FU-1, the job panel's Brief that had "Its own sandbox" in one run and not in the next).

## Context

What a run may do is decided once and pinned. A deep-mode agent turn gets `run_code` and its own sandbox only if admission required `agent_code` (`AgentCodeStep`) and its first claim pinned `agent-code` (`sandboxProfileFor`). Both read inputs that are not facts yet on a stack that has just started:

| Input | What it said when it did not know |
|---|---|
| The capability snapshot (`HarnessCapabilityService`) | The sandbox broker's health did not answer within `ISOLATION_PROBE_TIMEOUT_MS` (2 s), so the round kept the least value, `sandbox.isolation: none`, and the next round came 60 s later. |
| The worker sightings (`WorkerFeatureAvailability`) | No worker had polled yet, so `live('agent_code')` was false (and the false answer was cached 5 s). A store that could not be read answered false too. |

Each answer read as "this deployment has no sandbox" or "no worker can run code", and admission withheld `agent_code` for good: the run had no sandbox, no code tools and a prompt about 3.8k tokens smaller for its whole life, while the same turn a minute later had all three. It happened after every test-stack refresh and would after any production restart. The fault is shared: anything that turns "could not ask" into "asked, and the answer is no" pins a loss nobody chose. The other guarded worker features (`run_surface`, `release_pin`, `deliverable_pin`, `output_locale`, `resolved_config`) were decided the same way.

## Decision

**D1. A probe round records what it could not ask.** A `CapabilityProbe` names the capabilities it `provides`. When it throws (a timeout, a refused connection, a malformed answer) those capabilities keep their least value in the snapshot and are listed in its `unsettled`; a probe that answers proves its value either way. A round with anything unsettled is retried on a short backoff, 2 s doubling up to the 60 s period, and a settled round resets it. Nothing is pinned differently: `unsettled` is not part of the digest a run pins.

**D2. A guarded worker feature is withheld only when a live worker shows it lacks it.** Every loop worker advertises `chat` with its other features, so "`chat` is live and the feature is not" proves absence. Before any worker is seen, or while the sightings cannot be read, the feature is unknown and admission requires it as if served (`guardedFeatureState`, `servedOrUnknown`). This costs at most a wait: a queued run that requires a guarded feature no live worker serves is relaxed off it by the sweeper after the window (`unstrand`, DUR2), so a rolling deploy that upgrades Node first still runs it, a minute later. Withholding it would cost the run the feature for good. Unguarded features (an engine, `code_task_tool`, `model_fallback`, `mcp_first_use`) keep the old rule: they cannot be relaxed, so requiring them on a guess could strand a run.

**D3. Admission waits once, then leaves the sandbox to the claim.** When the snapshot's `sandbox.isolation` is unknown, `AgentCodeStep` waits for one more probe round, at most `CAPABILITY_SETTLE_WAIT_MS` (3 s). A proved sandbox requires `agent_code`; a proved `none` withholds it, as before; still unknown, the run is admitted requiring `agent_code` and logged `run.agent_code_pending`. The claim decides: its first claim of an `agent_code` run waits the same bounded round, and while the sandbox is still unknown it refuses with `RUN_NOT_CLAIMABLE` (the worker moves on, the run stays queued, the next poll asks again) until `CAPABILITY_SETTLE_WINDOW_MS` (60 s) after the run's admission. Past that window the broker has been silent for a minute, which is an outage and not a stack starting: the claim pins what is known (`none`, never a weaker sandbox, [D-3]) and logs `run.sandbox_unsettled`.

Refusing the turn with a retryable reason was the alternative. It was rejected on UX: the person would be told to send again for something that resolves on its own within seconds, and the turn would be lost to every caller that does not retry (a schedule, a webhook, a Slack message). Admitting with the sandbox pending costs the person a few seconds of "starting" at worst, and loses nothing silently.

**D4. Readiness says whether capabilities are settled.** `GET /api/v1/health` reports `agentHarness.capabilities: settled | settling` beside `status: available`. It never makes the deployment unhealthy, because a broker outage must not take chat down, but it is what a lane waits on before it drives a stack: `run.sh`, `smoke_human.py`, `journeys.py`, the UX captures and the human-test-stack refresh wait for `settled` instead of racing the first probe. The chat's `/sandbox` answer reports isolation `null` (not probed) while the broker's answer is unknown.

**Consequences.**

- A turn admitted while the broker is starting gets its sandbox once the broker answers, within the window; the Brief, the tools and the prompt are the same as a turn admitted a minute later.
- A broker that stays silent delays such a turn's start by up to a minute before it runs without a sandbox, and the delay is logged. A deployment whose broker is down for good shows `settling` on its health answer until it is fixed.
- During a rolling deploy that upgrades Node first, a turn admitted before any worker polled waits up to the sweeper's window for an older worker. A turn admitted once any worker has polled is decided at once, as before.

## As built

R31-CAPFIX on `harness/v2-r31capfix`:

- D1: `CapabilitySnapshot.unsettled`, `capabilityKnown`, `capabilitiesSettled`, `codeSandboxProof` (`domain/capabilities.ts`); `CapabilityProbe.provides` (`ports/capability-probe.ts`) on the five probes; the backoff in `HarnessCapabilityService` and `settledCapabilities` (`application/capability-service.ts`).
- D2: `guardedFeatureState` and `servedOrUnknown` (`application/worker-feature-availability.ts`), read by `AgentCodeStep`, `RunSurfaceStep`, `ReleasePinStep`, `DeliverablePinStep`, `OutputLocaleStep` and `pinReadByWorker`.
- D3: `AgentCodeStep` (`application/admission/policy-steps.ts`); `ExecutionService.claimCapabilities` (`application/execution-service.ts`).
- D4: `agentHarnessHealth` and `recordCapabilitySettlement` (`config.ts`), recorded when the background services start; the `/sandbox` isolation in `binders/workspaces.ts`; `pipeshub-openapi.yaml`.
- Tests: `tests/modules/agent-harness/r31-capfix-capability-settling.test.ts` (CAPFIX-1..9); the journey runner's `MR-7` (`deployment/e2e/journeys.py`).

## Deviations

None.

## Owner amendments

None.

## Status

**Accepted** (R31-CAPFIX, 2026-11-12).
