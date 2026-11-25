# ADR-OPS-01: Operator controls: one model of stop switches, one audit trail

- Extends: ADR-OWD-09 (policy and approvals: layers narrow, never widen), AD-06 (kill switches at session, org, harness version and global egress, effective within 60 s, audited, reversible), J-19 (a security officer stops a harness version after a CVE).
- Related: ADR-OWD-05 (durable run and session: a cancel is a control command), ADR-OWD-03 (isolation: the egress proxy is the only way out of a sandbox), ADR-OWD-14 (versions: a session keeps its engine version, C14.01).
- Checklist rows: C6.35, C11.06, C14.02, C14.03, C13.02. Gaps found by WP-87: an operator cannot cancel another person's run; nothing in the product calls the egress revocation; `AGENT_HARNESS_KILL_SWITCH` is not in any shipped Compose file or Helm value; no approval-backlog alert; engine images have no registry prefix; Helm `global.imageRegistry` reaches only some images.

## Context

Before this ADR, stopping work had five unrelated mechanisms:

| Mechanism | Scope | How it acts | Gap |
|---|---|---|---|
| `AGENT_HARNESS_KILL_SWITCH` (env) | instance | New turns take today's chat path (`kill_switch` routing); the egress proxy refuses run bindings when it **starts** with it on | Needs a restart; no shipped file passes it; runs in flight continue |
| The person's Stop | one run | `run.cancel_requested` control command | Only the run's owner may call it |
| User deactivation (SEC-AZ-08) | one person | Cancels their open runs by control command | Not an operator tool |
| `POST /v1/revocations` on the egress proxy | org or session | Revokes live run bindings once | No caller in the product; needs the worker credential |
| Engine profile ceilings (CPD-6) | org | Narrows engine versions for new runs | Does not stop attempts in flight |

Each was built for one incident shape. An operator facing a CVE in one coding-engine version, or a runaway agent, had to combine an env edit, a restart and a hand-made HTTP call, and nothing recorded who did it.

## Decision

### D1. One record: an operator control

A control is **a stop on one scope**. It is the only kind of control: everything an operator switches off is a scope being stopped, and turning it back on is clearing the control. A control is

```text
OperatorControl {
  id        derived from the scope, so one scope holds at most one control
  scope     { kind: session, sessionId } | { kind: agent, agentKey } | { kind: org }
          | { kind: engine_version, engine: { kind, version } } | { kind: instance } | { kind: egress }
  orgId     the org that set it; null for a control that covers every org (instance)
  reason    security | cost | incident | maintenance | other
  note      optional, at most 500 characters, shown to admins only
  setBy     principal ref of the admin who set it
  setAt     milliseconds since the epoch
}
```

The controls of a deployment are one configuration document, `/services/agentHarness/operatorControls` (`{ schemaVersion: 1, controls: { [id]: OperatorControl } }`), encrypted like the other harness documents and written only through `auditedConfigUpdate`. Setting and clearing are each one control-plane write with its `config.changed` audit entry (object `operator-control`, id the control's id), so **the audit trail is the existing harness audit chain**; nothing else records control changes. The deployment's `AGENT_HARNESS_KILL_SWITCH=true` is read as an `instance` control set by `deployment` that the UI shows and cannot clear.

### D2. One model of scope

Scopes nest: **session ⊂ agent ⊂ org ⊂ engine version ⊂ instance**, plus **egress**, which is orthogonal (it stops sandboxes' network, not runs' reasoning). A run is covered by a control when:

| Scope | Covers a run when |
|---|---|
| session | `run.orgId` and `run.sessionId` match |
| agent | `run.orgId` and `run.agentKey` match |
| org | `run.orgId` matches |
| engine_version | the run's pinned engine profile is that `kind@version`, in the control's org (every org when it has none) |
| instance | always |
| egress | the run holds an engine profile (its sandbox needs egress to reach the model gateway), in the control's org (every org when it has none) |

`coveringControls(run, controls)` in `domain/operator-controls.ts` is the one function admission, enforcement and the views use.

### D3. Who may set which scope

Which scopes and effects an org admin may set is an extension point, `OperatorScopePolicy` (`authorize(orgId, scope, effect)`, `instanceOperator(orgId)`). Every control an org admin sets is bound to their own org (the org is always the caller's; a session of another org is never found).

- **The built-in policy:** a conversation (`session`), an engine version (`engine_version`) and sandbox egress (`egress`), each stopped (effect `stop`) for the admin's org. Any other scope or effect is refused with `OPERATOR_SCOPE_FORBIDDEN`. For a deployment with one org, the org's engine-version and egress stops are the whole instance's: stopping a vulnerable engine version is never withheld from an admin.
- **A control with no org** covers every org (the env switch's instance control, D1). Only a caller the policy names an instance operator may clear one; the built-in policy names none, and the env switch is cleared only by unsetting it.

### D4. What a stop does, at once and within 60 s

On a set, Node:

1. **Running work:** cancels every open run the control covers with a control command (`run.cancel_requested` with `by` = the admin and `stop` = `{ scope, reason }`, an additive field). The command id is `operator-stop:{controlId}:{runId}`, so a repeat cancels nothing twice. Roots go first so children follow their parent. This reuses the cancel the person's Stop uses; the worker hears it at once through the run wakeup channel, or at its next journal boundary, and the sweeper ends a cancelled run whose lease lapses.
2. **New work:** admission reads the controls (cached at most `OPERATOR_CONTROLS_CACHE_MS` = 5 s):
   - session and agent: the turn is refused with `OPERATOR_STOPPED`, which the person reads as a translated "Your admin stopped agent work here".
   - org and instance: the org's kill switch reads on, so turns take today's chat path (`kill_switch` routing, unchanged) and the live layer every worker holds says so.
   - engine_version: the version is unavailable to admission. A chat pinned to it moves to a version the release declares compatible (D6); with none, the engine is withheld and the turn runs on the native loop.
   - egress: engine hand-offs are withheld (`egress_stopped`); the chat answers on the native loop.
3. **Sandbox egress:** the egress proxy reads the egress stops from Node's worker-only route `GET /api/v1/agent-harness/internal/operator-controls/egress` (state API, `harness-state:3002`) every `EGRESS_STOPS_POLL_S` = 10 s (one stop per org stopped, `orgId: null` for every org), revokes every run binding a stop covers and refuses new ones while it holds. It keeps its last answer while Node is unreachable, and its start-up env switch still holds. The poll is the built-in `EgressStopsFeed` (`NodeEgressStopsFeed`), read through `harness_edition()`. An egress stop is a safety control, so it reaches the proxy in every build.

**The 60 s bound.** "Takes effect" means the run is `cancelling` or `cancelled`: from then the model and tool gateways refuse its tokens (`RUN_NOT_LEASED`), so the attempt can do nothing more whatever its process does, and the worker ends it at its wakeup or next journal boundary. The set cancels at once. The sweeper (one leader, every 5 s) re-applies every control to the open runs it covers, so a run admitted in the race with a set, or by a replica whose cached controls predate it, is cancelled at the next tick. The worst case is `OPERATOR_CONTROLS_CACHE_MS + SWEEP_INTERVAL_MS + LIVENESS_GAP_MS` (5 + 5 + 25 s, the last a sweeper leader handover); egress is `EGRESS_STOPS_POLL_S` (10 s). A gated unit test (OPS-02) holds the sum to `OPERATOR_STOP_BOUND_MS` = 60 s and drives the race on the real execution service: the raced run is cancelled at the next tick and its gateway token refused; the proxy's poll period is held to 10 s by OPS-04.

### D5. The people affected are told, plainly

A run stopped by a control ends `cancelled`; Node's projection turns `stop` into the run error `OPERATOR_STOPPED` with the scope and reason, and the chat shows one translated sentence per scope (all 10 locales): "Your admin stopped this conversation's agent work", "…this agent", "…agent work for your organization", "This coding engine version was stopped for a security fix; send your message again to continue on {version}", and so on. No ids, no note (the note is for admins).

### D6. Retire and stop now: two explicit actions on one engine-version control (UP-02, C14.02)

An `engine_version` control carries an `effect` (review 2026-11-05). Whether an admin may set `retire` is the policy's (D3; the built-in policy refuses it); the built-in service applies `stop` and leaves the runs of a stored control with another effect as they are, while admission still refuses its version:

- **`stop` (stop now; a CVE being exploited):** cancels the version's open runs as every other stop does (D4.1).
- **`retire` (planned):** **suspends** them instead, reusing the run's own suspension, never a parallel state: Node's control command `suspend` moves a queued or running run to `paused` with `suspendedFor: retired`, journals `run.suspended {reason: retired}` and the notice `engine_retired`, and takes a running attempt back as a lapsed lease is taken back (attempt and lease cleared, its gateway tokens refused, the worker woken). The run's journal, checkpoint and workspace stay; its working time is held for the person-wait window, as a run waiting for a person holds it. The person reads a translated notice saying the task is paused, will continue on its own, and that stopping it starts fresh.
- **Resume:** every sweeper tick resumes a run suspended for a retired version once that version is allowed again (the control cleared, or switched to nothing) or the release declares a successor that is not stopped: control `resume` with `cause: version_available`, journaling `run.resumed` and the notice `engine_continued`; the run is claimed again as after a lapsed lease and continues where it was. Without either it stays suspended, and its person may stop it and send their message again.
- **Declared compatibility:** an allowlist entry's optional `resumes` names earlier versions of the same engine kind whose sessions it continues (additive; generated for Node as `RELEASE_ENGINE_SUCCESSORS`, read by the worker's image table). A declaration counts only for a version the release no longer lists, so a listed version always runs itself; another kind is never compatible.

New turns on a retired or stopped version are unavailable to admission either way: a chat pinned to it moves to the release's version of the same engine (C14.01). Events, `/pipeshub` and tool schemas evolve additively (UP-03..05): `stop` on `run.cancel_requested`, the suspend reason `retired`, the resume cause `version_available` and the two notice codes are such additions.

### D7. The upgrade pipeline (C14.03) and the pairwise matrix (C13.02) are jobs, not switches

- **Upgrade pipeline:** a scheduled candidate job resolves each engine's `latest` upstream version and runs the CC1 candidate gate (`PXH_ENGINE_CANDIDATE`) against it; a pass writes a candidate catalog entry (an artifact and a draft allowlist change), never the allowlist itself. An org opts in by pinning the candidate version in its engine profile ceiling once a release ships it; the default flips when the allowlist's first entry for that kind changes in a reviewed release. An engine-version stop (D6) is the fast-track's other half: stop the vulnerable version now, ship the fixed one through the same gate.
- **Pairwise matrix:** a scheduled job runs the deterministic harness suites over an all-pairs cover of engine × model family × store (graph, KV, broker); nightly runs the pairwise cover, the release job runs the full product. The matrix and both scheduled jobs are maintained outside this repository; the candidate catalog they call is here.

### D8. One surface for admins

Workspace settings > **Run settings** > **Running now** lists the agent work running in the admin's org in plain words (conversation, agent, person, engine, when it started) with **Cancel run**, which asks for a reason and an optional admins-only note (`GET /api/v1/agent-harness/admin/runs`, `POST /api/v1/agent-harness/admin/runs/{runId}/cancel`). Below it, **Safety stops** lists the release's engine versions and sandbox internet access with their state (on, or stopped by whom, when and why) and **Stop** (a reason and an optional note) or **Resume** (a confirmation); a stop that covers every org is shown without Resume. The stops are `GET/PUT /api/v1/agent-harness/admin/controls` and `DELETE /api/v1/agent-harness/admin/controls/{controlId}`. A fuller page replaces both through the front end's extension points (`RunSettingsRunningNow`, `RunSettingsSafetyStops`, `workspaceNavExtras`).

### D9. Deployment settings, not separate switches

- `AGENT_HARNESS_KILL_SWITCH` is passed by the shipped Compose file to `pipeshub-ai` and `egress-proxy` (`${AGENT_HARNESS_KILL_SWITCH:-false}`) and by Helm as `agentHarness.killSwitch`. It is the instance stop and the restart-time floor; the controls are the live switches.
- `HARNESS_ENGINE_IMAGE_REGISTRY` prefixes every engine image the worker asks for and the broker allows (`{registry}/{bare name}`); the allowlist, digest pins and binary pins are keyed by the prefixed name, so the bare name is not allowed once a prefix is set. Compose passes it to `pipeshub-ai` and `sandbox-broker`; Helm as `agentHarness.engineImageRegistry`, defaulting to `global.imageRegistry`.
- Helm's `global.imageRegistry` reaches every image the chart renders through one helper (stores, sandbox, dind, docker proxy); a value naming its own registry keeps it.

### D10. Approval backlog

Node exports `pipeshub_agent_harness_approvals_waiting` and `pipeshub_agent_harness_approval_oldest_wait_seconds`; the runbook's "Approval backlog" step reads them. An alert rule on them is the deployment's monitoring.

### Consequences

- One mental model for operators and one audit trail; WP-87's runbooks use the controls and the admin cancel route rather than env edits and hand-made revocations.
- A stop never widens anything and is reversible by clearing it; clearing does not restart cancelled runs (the person sends their message again).
- Out of scope: `SECRET_KEY` rotation, which stays the owner's rotation step (recorded in the runbooks).

### Alternatives rejected

- **A switch per mechanism (env, Labs flag, proxy route):** what existed; five models and no audit.
- **Only cancelling for a version (first build):** loses the person's place; the review replaced it with retire (suspend) beside stop now (D6).
- **A new held state for retired runs:** a parallel state machine; `paused` with a suspend reason is the run's own (D6).
- **Pushing stops from Node to the proxy:** Node is not on the sandbox plane; the proxy pulling from the source of truth needs no new path and keeps working from its last answer.

## As built

Built by WP-86 (`harness/v2-wp86`) and narrowed to extension points by WP-126; evidence in the project tracker, "WP-86 evidence".

- D1–D4 (Node): `domain/operator-controls.ts` (the record, `covers`, `coveringControls`, the bound constants), `application/operator-controls.ts` (`OperatorStopService`: the stops in force and their enforcement, the env switch its only built-in source; `ports/operator-stops.ts`, `HarnessEdition.rolloutStops`), `application/run-stopper.ts` (the cancel loop the deactivation shares), the `OperatorStopStep` and the engine-version and egress checks in `application/admission/policy-steps.ts` and `application/engine-eligibility.ts`, the kill switch in `adapters/configuration/platform-rollout-settings.ts`, the sweeper hook in `application/run-sweeper.ts`, all under `backend/nodejs/apps/src/modules/agent-harness/`.
- D4.3 (egress proxy): held stops in `backend/python/harness_core/sandbox/egress/bindings.py` (refusal `egress_stopped`); the `EgressStopsFeed` port in `backend/python/app/harness/sandbox/egress_config.py` run by `backend/python/app/egress_proxy_main.py`; its built-in feed holds no stops.
- D5: `run.cancel_requested` carries `stop` (`contracts/harness/core/events/run.cancel_requested.schema.json`); the projection ends such a run with the run error `operator_stopped` and its scope; the web app words it per scope in all ten locales (`frontend/app/(main)/chat/harness-errors.ts`, one typed `OperatorStopDetails`; Node's one typed `OperatorStopOutcome`). The saved failure carries the same scope (`errorStop`), so a reload reads the same sentence.
- D6: control commands `suspend` and `resume` with `cause: version_available` in `domain/execution.ts`, held to `domain/operator-suspension.ts` by both execution stores (shared contract case OPS-14 on the in-memory store and MongoDB); `OperatorStopService.enforce` (stop; `applyEffect` and `afterEnforce` for other effects); the `RELEASE_ENGINE_SUCCESSORS` constant from the allowlist's `resumes` (`contracts/harness/codegen/generate.py`) and the worker's image table (`backend/python/harness_engines/release.py`); the waiting status `waiting_engine_version` and the notices `engine_retired` / `engine_continued` in the chat.
- D7: `scripts/engine_candidate_catalog.py` (the candidate entry, the skip rule and CC1's gate lanes); the scheduled jobs that run it and the pairwise matrix are maintained outside this repository.
- D8: the admins' open-runs list and cancel, and the pages that set stops, are not part of this repository; `RunSettingsRunningNow` and `RunSettingsSafetyStops` (`frontend/config.ts`) render nothing here.
- D9: `HARNESS_ENGINE_IMAGE_REGISTRY` in `backend/python/harness_engines/release.py` (`run_image`), the broker and the worker; Compose and Helm pass it and `AGENT_HARNESS_KILL_SWITCH`; the chart's `pipeshub-ai.imageRef` helper routes every image through `global.imageRegistry`.
- D10: the two gauges in `application/harness-local-metrics.ts`.
- D6, successor (WP-99, `harness/v2-wp99`; evidence in the project tracker, "WP-99 evidence"): the engine registry takes the release's declared successors (`AllowlistEngineRegistry(successors=…)`, `resumed_on`, built by `harness_engines/release.py` `release_registry`); the durable engine kernel's `_profile()` moves a run pinned to a version the release no longer lists to the successor that declares it (`EngineKernel.successors`, logged `engine.kernel.resumed_on_successor`), and `_turn` drops the run's image and contract pins once moved; a listed version always runs itself. The parents joined on a retired child hold their working time with it (both execution stores, shared contract case PW-12).

## Deviations

- D7: each pairwise cell runs the suites of each of its dimensions on that cell's stores, not one engine-on-family-on-stores run; KV `redis` runs on fake transports, Vertex AI has no scripted wire, and Claude Code candidates are reported, not built (CI cannot download the binary). Both scheduled jobs run only when their repository variables are set. The fake KV transport and the missing Vertex wire are follow-up OPS01-FU-1 in STATUS; the first nightly runs wait on CI (C13.02, C14.03).
- D10: the oldest approval wait is read from the waiting run's last update, which a waiting run does not move.
- D6 (closed by WP-99): resuming on a declared successor and a parent's working time during a child's retire are built; see "As built". The live notice handler shows each notice code once per run, so a second retire of the same run after it continued does not show "paused" again.
- Review 2026-11-05 (recorded, not deviations any more): the first build cancelled on an engine-version stop (now retire beside stop now), showed the generic sentence after a reload (the saved failure now carries `errorStop`), and left next-run readmission and schedule firings to the sweeper (they refuse at admission through the same `OperatorStopStep`; children through `coveringControls`, `CHILD_OPERATOR_STOPPED`).

## Owner amendments

None. Review 2026-11-05: retire and stop now are separate actions (D6). Decision 2026-11-07 (WP-126): an org admin keeps the run cancel, a Running now list, and engine-version and egress stops for their org (safety controls are never withheld); which further scopes may be set is an extension point (D3).

**WP-111 ENGINEOFF, 2026-11-07 (decided from first principles, safety first).** An org admin turning a coding engine off (the engine profile ceiling's `engines`, E4a) is a control like the ones above, not a picker hint. One policy object, `EngineEligibility` (`application/engine-eligibility.ts`), answers "may this engine start a session for this person now" from the org's switch, the person's groups (G-41) and an operator's version or egress stop, and every place an engine is chosen asks it: a turn's admission (`EngineProfileStep`), the default chat's engine choice (`CodingEngineService.forTurn`), a code_task child's spec pin (`ChildRunService`) and an engine run's claim (`ExecutionService`). Whether this server can run the engine at all (image, sandbox, runtime, live worker) stays admission's own check.
- **A chosen engine that is off.** The default chat's choice (the chat's, the person's or the org's default) moves to an engine that is on, the org's default first, then release order, and the answer says so (`engine_switched`: "Claude Code is turned off for your organization, so this task's coding work used Codex."). With none on, the turn answers without code tools and says the engine is off and what to do (`ran_without_code_tools`, `engine_off`). An agent's own profile is its owner's narrowing, so an agent never moves outside it: an engine it lists that is on is used, else `engine_off`.
- **Runs pinned before the switch.** The safety control wins: no new engine session starts on an engine that is off. A code_task child is refused at its spec pin (`ENGINE_OFF`; the loop answers itself, and the chat says the engine is off), and an engine run pinned before the switch is not claimed, neither its first claim nor a claim after a lost attempt: it fails `engine_off`. An attempt already running is not cut short by the switch (its session exists; the budget bounds it, at most the engine's wall time): stopping work in flight is an operator's "stop now" (D4, D6), which the admin uses when they need it now. The next turn of the same chat reads the switch at admission.
- **The chat only names the engine that will run.** "Delegating to …" names the parent's pinned authority, and a child is pinned only on that engine or not at all, so the name is the engine that runs.

**G11, UI-B1, 2026-11-09 (the stop stays on the run).** D5's saved failure carried an operator's stop for the chat message, but the run itself did not: a job's card, the person's runs list (ADR-UI-01 D1) and the run read after a reload had only `cancelled`. The first cancel that carries a `stop` now keeps it on the run as `operatorStop {scope, reason, at}` (DB time; never the note), in the same write that moves the run, on all three stores (`operatorStopAfterCancel` in `domain/execution.ts`; shared contract UIB1-06); a later stop never rewrites it, and the person's own Stop leaves none. The run read (`GET …/runs/{runId}`), the timeline and the runs list return it, so every surface can say "An admin stopped this: <reason>". An admin's single-run cancel (D8) keeps the scope `session` it already journals.

## Status

**Accepted** 2026-11-05: decided from first principles, long term, UX-first; built in WP-86.

