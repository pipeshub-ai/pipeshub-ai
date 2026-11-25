import type { WorkerCapabilities } from '../contracts/generated/core/worker-capabilities';
import {
  KNOWN_WORKER_FEATURES,
  type WorkerFeature,
} from '../contracts/generated/ext/pipeshub/enums';
import { HarnessConflictError } from './errors';
import { UNSET_REGION } from './region';
import type { HarnessRun } from './execution';
import type { RunAudience } from './session-audience';
import { TURN_LABELS_FEATURE } from './turn-labels';
import { ASSISTANT_AGENT_DEFINITION_KEY } from './rollout';
import type { AgentRunDefinition } from './agent-definition';
import { GOVERNED_MEMORY_FEATURE, type MemoryPin } from './memory';
import { RUN_GATES_FEATURE } from './run-gates';
import { ENVIRONMENT_PIN_FEATURE } from './agent-environment';
import { OUTPUT_LOCALE_FEATURE } from './output-locale';
import {
  guardedInputFeatures,
  KEYWORD_SEARCH_BUDGET_FEATURE,
} from './worker-input';
import {
  CODE_TASK_TOOL_FEATURE,
  DELIVERABLE_PIN_FEATURE,
  DESCRIPTOR_PINS_FEATURE,
  ExecutorKind,
  grantsMcpAtFirstUse,
  MCP_CARRIED_PINS_FEATURE,
  MCP_FIRST_USE_FEATURE,
  KNOWLEDGE_SELECTION_FEATURE,
  MODEL_FALLBACK_FEATURE,
  pinnedKnowledge,
  pinsKnowledgeSelection,
  RELEASE_PIN_FEATURE,
  RESOLVED_CONFIG_FEATURE,
  RUN_SPEC_FOLD_VERSION,
  RUN_SURFACE_FEATURE,
  RUN_SPEC_SCHEMA_VERSION,
} from './run-spec';

/**
 * Schema-gated claims (REPORT 6.1.7; P1b RU-2). A worker advertises what its
 * build can run; each run records what it needs; a claim hands a worker only
 * runs it can fold and execute. A mixed fleet (a rolling deploy, a rollback)
 * is then safe by rule instead of by deploy order: an older worker never
 * receives a run a newer build pinned, admitted or wrote to, and such a run
 * waits in the queue for a worker that can run it.
 */

/**
 * Run shapes beyond the spec and fold versions, by the slice that added them:
 * `chat` the built-in assistant's pin (T1a), `caller` the run input's caller
 * (T1b), `steering` queued input (T2), `steering_references` queued input
 * naming attachments and records (T2b), `steering_by` queued input naming its
 * sender (SS1), `child_runs` a stream holding `child.*` events and a child
 * completing with its result (P6.1), `deep_mode` deep mode's loop and its
 * sub-agent children (`x.pipeshub.subAgent`) (K-L), `executor_engine` a run
 * pinned with an engine executor (E1), `engine_inputs` an engine run whose
 * input names artifacts it finds under `in/` (E1b), `session_roles` a run in a shared
 * context: `sessionRole` on `run.admitted` and the history audience it renders
 * by (SS2), `turn_labels` an audience listing other participants' runs whose
 * label passed (P2.5a), `governed_memory` a spec pinning memories and the
 * remember tool (P7.3), `agent_code` a loop run that may be pinned
 * `sandboxProfile: agent-code`: `run_code` with its sandbox hooks and receipts
 * (P5.11), `inherited_labels` a child whose spec pins its parent's label (UX2),
 * `inherited_grants` a child whose spec pins the conversation grants its parent held (FLOWASK),
 * `child_asks` a child whose spec pins whether its parent can ask a person (ANSWER-CONTRACT-FU-3),
 * `descriptor_pins` a spec whose tool and MCP manifest pins carry
 * `digestVersion: 2`, verified at claim (CPS), `resolved_config` a spec
 * carrying the run's pin (`x.pipeshub.resolved`), which the worker reads its
 * policy from instead of the live documents (CPD-6), `child_models` a stream
 * holding a `child.spawned` that names the model a sub-agent role set (MA1), `assistant_agent` a spec
 * pinning the system assistant definition (ASSIST1), `knowledge_selection` a
 * spec pinning a selection below app level or a saved agent's source limits
 * (SYNC4), `run_surface` a spec pinning where the run was started (FU4), `sandbox_command` a person's `/run`
 * (SLASH3), `release_pin` a spec pinning what it was admitted under and its engine image digest (C2.07),
 * `trigger_context` a scheduled run's trigger context in its input (TR-03), `run_gates` a spec pinning run gates
 * and a stream holding what they journal (G-03..G-05). A new one is added to the
 * contract's `WorkerFeature` once workers advertise it, and required only by
 * runs that use it.
 */
export {
  CODE_TASK_TOOL_FEATURE,
  DELIVERABLE_PIN_FEATURE,
  MCP_CARRIED_PINS_FEATURE,
  MCP_FIRST_USE_FEATURE,
  MODEL_FALLBACK_FEATURE,
  KNOWN_WORKER_FEATURES,
  RELEASE_PIN_FEATURE,
  RESOLVED_CONFIG_FEATURE,
  RUN_SURFACE_FEATURE,
};
export type { WorkerFeature };

/** Every loop worker advertises it, so its sightings say whether any worker is alive (UX3). */
export const LIVENESS_FEATURE: WorkerFeature = 'chat';

/** Advertised only by a worker composed with the engine kernel: engine runs can run (UX3 SE-4). */
export const ENGINE_FEATURE: WorkerFeature = 'executor_engine';

/** UX2: a child whose spec pins its parent's label (`x.pipeshub.inheritedLabel`). */
export const INHERITED_LABELS_FEATURE: WorkerFeature = 'inherited_labels';

/** FLOWASK: a child whose spec pins the conversation grants its parent held (`x.pipeshub.inheritedGrants`). */
export const INHERITED_GRANTS_FEATURE: WorkerFeature = 'inherited_grants';

/**
 * ANSWER-CONTRACT-FU-3: a child whose spec pins whether its parent can ask a person (`x.pipeshub.parentAsks`); an
 * older worker's strict spec model refuses the field.
 */
export const CHILD_ASKS_FEATURE: WorkerFeature = 'child_asks';

/** MA1: `child.spawned.model` on a stream; an older worker's strict event model refuses it. */
export const CHILD_MODELS_FEATURE: WorkerFeature = 'child_models';

/** ASSIST1: an older worker reads `agentIdPlaceholder` as a saved agent and refuses the run. */
export const ASSISTANT_AGENT_FEATURE: WorkerFeature = 'assistant_agent';

/** Whether a run's definition is the system assistant's (the assistant's Agent mode, ASSIST1). */
export const pinsSystemAssistant = (
  definition: Pick<AgentRunDefinition, 'agentKey'> | null,
): boolean => definition?.agentKey === ASSISTANT_AGENT_DEFINITION_KEY;

/** SLASH3: a person's `/run`, one `run_code` call and no model (`input.sandboxCommand`, run kind `command`). */
export const SANDBOX_COMMAND_FEATURE: WorkerFeature = 'sandbox_command';

/** TR-03: a scheduled run's input carries its trigger context, staged at `/pipeshub/inputs/trigger.json`. */
export const TRIGGER_CONTEXT_FEATURE: WorkerFeature = 'trigger_context';

/**
 * AP-06: an approval journaling the arguments a person edited. A worker without it reads the approval and runs
 * the model's arguments, so a run that holds one requires it from then on.
 */
export const APPROVAL_EDITS_WORKER_FEATURE: WorkerFeature = 'approval_edits';

/** P5.11: a loop run admitted while the deployment proved a code sandbox. */
export const AGENT_CODE_FEATURE: WorkerFeature = 'agent_code';

/**
 * SIGNGAP: the worker can carry a person's own vendor sign-in in an engine
 * sandbox (the egress proxy's tunnels and the release's signed host list). A
 * deployment fact, not a run shape: Node pins a run on the actor's own plan
 * only while a live worker advertises it, and such a run requires it.
 */
export const VENDOR_SIGNIN_FEATURE: WorkerFeature = 'vendor_signin';

/**
 * A worker that knows regions (OWD-12): it names its region whenever one is
 * set, so one that names none has none (WP88-FU-2). A fact about the worker,
 * not a run shape: no run requires it.
 */
export const REGION_AWARE_FEATURE: WorkerFeature = 'region_aware';

/**
 * C411-FU-1: the deployment's traces hold model content (prompts, answers, tool arguments): tracing and the GenAI
 * content opt-in are both on where the worker and the Model Gateway run. A deployment fact the admins' AI Access page
 * shows, read from live workers because a split install's Node need not share their settings; no run requires it.
 */
export const TRACE_CONTENT_FEATURE: WorkerFeature = 'trace_content';

/**
 * What a run admitted before requirements were recorded needs: every run
 * shape this Node knows. Not `region_aware` or `trace_content` (worker facts). Not `vendor_signin`: no such run was pinned on a
 * person's own plan (SIGNIN came long after), and requiring it would strand
 * the run on every server without the sign-in add-on. Not `mcp_first_use`: no such run granted a server whole. Not `mcp_carried_pins`: no such child carried a pin.
 */
export const LEGACY_RUN_FEATURES: readonly WorkerFeature[] =
  KNOWN_WORKER_FEATURES.filter(
    (feature) =>
      feature !== VENDOR_SIGNIN_FEATURE &&
      feature !== MODEL_FALLBACK_FEATURE &&
      feature !== MCP_FIRST_USE_FEATURE &&
      feature !== MCP_CARRIED_PINS_FEATURE &&
      feature !== REGION_AWARE_FEATURE &&
      feature !== TRACE_CONTENT_FEATURE,
  );

/**
 * Features admission requires only while a live worker advertises them, and
 * whose stranded runs the sweeper releases (DUR2): requiring one is an
 * upgrade, never a correctness need, so a run that cannot get it runs as
 * before the feature existed. A feature a run cannot do without (a fold, a
 * schema, an event older workers cannot read) is never listed here.
 */
export const GUARDED_WORKER_FEATURES: readonly WorkerFeature[] = [
  AGENT_CODE_FEATURE,
  // Without it the run reads its policy as before the pin reached workers (the spec's legacy fields).
  RESOLVED_CONFIG_FEATURE,
  // Without it the run shows no MCP sign-in card, as before FU4.
  RUN_SURFACE_FEATURE,
  // Without it the run pins no contract, catalog or image digest, as before C2.07.
  RELEASE_PIN_FEATURE,
  // Without it the worker resolves the run's deliverable from its request once, as before ADR-RUN-01 D1.
  DELIVERABLE_PIN_FEATURE,
  // Without it the model answers in the language the person writes in, as before G-52.
  OUTPUT_LOCALE_FEATURE,
  // Without it each knowledge search waits for all its keyword matches, as before R33JGAPS-FU-1.
  KEYWORD_SEARCH_BUDGET_FEATURE,
];

/**
 * The guarded features of a deployment. With `pinOnly` (the policy store is
 * the record) `resolved_config` is a correctness need: a run relaxed off it
 * would have no policy document left to read, so it waits for a worker that
 * reads its pin, as a run does for any feature it cannot do without.
 */
export const guardedWorkerFeatures = (
  pinOnly: boolean,
): readonly WorkerFeature[] =>
  pinOnly
    ? GUARDED_WORKER_FEATURES.filter(
        (feature) => feature !== RESOLVED_CONFIG_FEATURE,
      )
    : GUARDED_WORKER_FEATURES;

/** A run pinned to a guarded feature that no live worker serves any more (DUR2). */
export const NO_COMPATIBLE_WORKER = 'no_compatible_worker';

const KNOWN = new Set<string>(KNOWN_WORKER_FEATURES);

export interface RunRequirements {
  /** The pinned spec's schemaVersion; absent until pinned. */
  schemaVersion?: number;
  /** The highest fold version of any build that admitted, pinned or claimed the run. */
  foldVersion: number;
  features: WorkerFeature[];
}

/** An advertisement as this Node reads it: names it does not know are dropped. */
export interface ClaimingWorker {
  supportedSchemaVersions: number[];
  foldVersion: number;
  features: WorkerFeature[];
  /**
   * Placement (OWD-12): the worker's region, and whether it also takes runs
   * recorded before regions (it is in this Node's region). Absent: no
   * placement rule (a caller that does not place by region).
   */
  placement?: { region: string; servesUnregioned: boolean };
}

/** A claim with no advertisement is from a build older than the rule: it gets no v2 run. */
export const UNADVERTISED_WORKER: Readonly<ClaimingWorker> = Object.freeze({
  supportedSchemaVersions: [],
  foldVersion: 0,
  features: [],
});

const known = (names: readonly string[]): WorkerFeature[] =>
  [
    ...new Set(names.filter((name) => KNOWN.has(name))),
  ].sort() as WorkerFeature[];

export function claimingWorker(
  advertised: WorkerCapabilities | undefined,
  region?: { worker: string | undefined; here: string },
): ClaimingWorker {
  const base =
    advertised === undefined
      ? UNADVERTISED_WORKER
      : {
          supportedSchemaVersions: [...advertised.supportedSchemaVersions],
          foldVersion: advertised.foldVersion,
          features: known(advertised.features),
        };
  if (region === undefined) return base;
  // A worker older than regions does not say: it is the deployment's, in this Node's region (UX2). A worker that knows
  // regions and names none has no region set: it is no region's (`default`), so a misconfigured worker in a regioned
  // deployment runs nothing there (OWD-12 fails closed; WP88-FU-2).
  const workerRegion =
    region.worker ??
    (base.features.includes(REGION_AWARE_FEATURE) ? UNSET_REGION : region.here);
  return {
    ...base,
    placement: {
      region: workerRegion,
      servesUnregioned: workerRegion === region.here,
    },
  };
}

/** Whether `worker` may hold a run recorded in `region` (OWD-12); a run from before regions is this Node's. */
export const placesRun = (
  region: string | undefined,
  worker: ClaimingWorker,
): boolean =>
  worker.placement === undefined ||
  (region === undefined
    ? worker.placement.servesUnregioned
    : region === worker.placement.region);

/**
 * What a run needs from its admission: this build's fold, the shapes its
 * input has, and what its session's root stream already holds (the queued
 * messages of earlier runs, which this run folds too).
 */
export function admissionRequirements(
  run: Pick<HarnessRun, 'agentKey' | 'input' | 'sessionRole'>,
  sessionFeatures: readonly string[] = [],
  executor: ExecutorKind = 'loop',
  audience: Pick<RunAudience, 'visibleRuns'> | null = null,
  memory: MemoryPin | null = null,
  agentCode = false,
  descriptorPins = false,
  resolvedConfig = false,
  systemAssistant = false,
  codeTask = false,
  /** The agent definition a root run pins its knowledge from (null: the built-in assistant); omitted for a child. */
  definition?: AgentRunDefinition | null,
  runSurface = false,
  releasePin = false,
  gates = false,
  deliverablePin = false,
  environmentPin = false,
): RunRequirements {
  const features: WorkerFeature[] = [...known(sessionFeatures)];
  // Not a session feature: an engine run leaves only events older folds know or may ignore.
  if (executor === 'engine') features.push('executor_engine');
  // An older engine worker would run without the artifacts the message names; a loop run that hands off lists
  // them (`code_task_tool`) and leaves them to its engine child (CTL-6).
  if ((run.input?.inputArtifacts?.length ?? 0) > 0 && !codeTask) {
    features.push('engine_inputs');
  }
  if (run.input?.caller !== undefined) features.push('caller');
  if (run.input?.command !== undefined) features.push('command_input');
  // An older worker would refuse the input; a newer one runs it without a model (SLASH3 `/run`).
  if (run.input?.sandboxCommand !== undefined)
    features.push(SANDBOX_COMMAND_FEATURE);
  // An older worker would drop the request and run on the full context.
  if (run.input?.compact !== undefined) features.push('compact_request');
  // An older worker would refuse the input (its model forbids unknown fields).
  if (run.input?.trigger !== undefined) features.push(TRIGGER_CONTEXT_FEATURE);
  // Guarded (G-52's locale, R33JGAPS-FU-1's search budget): relaxed off one, the claim sends the input without its
  // field (`workerInput`).
  features.push(...guardedInputFeatures(run.input));
  // Also a session feature: every later run of the session folds its sessionRole.
  if (run.sessionRole !== undefined) features.push('session_roles');
  // Only this run's spec lists them: not a session feature.
  if ((audience?.visibleRuns?.length ?? 0) > 0) {
    features.push(TURN_LABELS_FEATURE);
  }
  // Only this run's spec pins it: not a session feature.
  if (memory !== null) features.push(GOVERNED_MEMORY_FEATURE);
  // An older worker's durable path registers run_code without its hooks; not a session feature (no new event).
  if (agentCode) features.push(AGENT_CODE_FEATURE);
  // Only this run's spec pins its engine authority: not a session feature.
  if (codeTask) features.push(CODE_TASK_TOOL_FEATURE);
  // Only this run's spec carries the version-2 pins: not a session feature.
  if (descriptorPins) features.push(DESCRIPTOR_PINS_FEATURE);
  // Only this run's spec carries its pin: not a session feature.
  if (resolvedConfig) features.push(RESOLVED_CONFIG_FEATURE);
  // Only this run's spec names the system definition: not a session feature; never guarded, the run waits.
  if (systemAssistant) features.push(ASSISTANT_AGENT_FEATURE);
  // Only this run's spec pins it: not a session feature.
  if (runSurface) features.push(RUN_SURFACE_FEATURE);
  // Only this run's spec pins it: not a session feature.
  if (releasePin) features.push(RELEASE_PIN_FEATURE);
  // Only this run's spec pins it: not a session feature.
  if (deliverablePin) features.push(DELIVERABLE_PIN_FEATURE);
  // Only this run's spec grants a server whole: not a session feature; an older worker would refuse the field.
  if (grantsMcpAtFirstUse(definition ?? null)) {
    features.push(MCP_FIRST_USE_FEATURE);
  }
  // G-03..G-05: an older worker would refuse the spec; never guarded: run without its gates the run would act for real.
  // Also a session feature (the admission store records it): later runs fold what the gates journal.
  if (gates) features.push(RUN_GATES_FEATURE);
  // C20.18: only this run's spec pins it; never guarded: run without its environment the engine lacks its toolchain.
  if (environmentPin) features.push(ENVIRONMENT_PIN_FEATURE);
  // SYNC4: only this run's spec pins a selection or source limits; never guarded: run without them it would widen.
  if (
    definition !== undefined &&
    run.input !== undefined &&
    run.input !== null &&
    pinsKnowledgeSelection(pinnedKnowledge(definition, run.input))
  ) {
    features.push(KNOWLEDGE_SELECTION_FEATURE);
  }
  if (run.agentKey === undefined || run.agentKey.length === 0) {
    features.push('chat');
  } else if (runsDeepMode(run.input?.model.chatMode)) {
    features.push('deep_mode');
  }
  return { foldVersion: RUN_SPEC_FOLD_VERSION, features: known(features) };
}

/**
 * An agent run in deep mode (K-L), and every child it admits (they share its
 * input's mode): an older worker would run it as react.
 */
export const runsDeepMode = (chatMode: string | undefined): boolean =>
  (chatMode ?? '').trim().toLowerCase() === 'deep';

/**
 * A run admitted before requirements were recorded needs every run shape this
 * Node knows of (`LEGACY_RUN_FEATURES`), at this Node's fold: only a current
 * worker gets it.
 */
export function requirementsOf(
  run: Pick<HarnessRun, 'requires'>,
): RunRequirements {
  const requires = run.requires;
  return {
    ...(requires?.schemaVersion !== undefined
      ? { schemaVersion: requires.schemaVersion }
      : {}),
    foldVersion: requires?.foldVersion ?? RUN_SPEC_FOLD_VERSION,
    features: requires?.features ?? [...LEGACY_RUN_FEATURES],
  };
}

/**
 * The spec schema version a claim hands the worker: the pin as it was stored
 * (Node serves it unconverted), or, for a run not pinned yet, the version
 * this Node pins at. A spec schema bump therefore needs no new gate: workers
 * advertise the versions they read, and each run is matched on its own.
 */
export const servedSchemaVersion = (needs: RunRequirements): number =>
  needs.schemaVersion ?? RUN_SPEC_SCHEMA_VERSION;

/**
 * Why `worker` cannot run `run` through this Node; empty when it can.
 * `schemaVersion`: this Node cannot read the pin (a newer build pinned it);
 * `workerSchemaVersion`: the worker cannot read the spec it would be handed;
 * `foldVersion`: the run needs a newer fold; `feature:<name>`: a shape the
 * worker's models refuse; `region`: the run's data lives in another region
 * than the worker's (OWD-12).
 */
export function missingFor(
  run: Pick<HarnessRun, 'harnessMajor' | 'requires'> &
    Partial<Pick<HarnessRun, 'region'>>,
  worker: ClaimingWorker,
): string[] {
  const placed = placesRun(run.region, worker);
  if (run.harnessMajor !== 2) return placed ? [] : ['region'];
  const needs = requirementsOf(run);
  const served = servedSchemaVersion(needs);
  const missing: string[] = placed ? [] : ['region'];
  if (served > RUN_SPEC_SCHEMA_VERSION) missing.push('schemaVersion');
  if (!worker.supportedSchemaVersions.includes(served)) {
    missing.push('workerSchemaVersion');
  }
  if (needs.foldVersion > worker.foldVersion) missing.push('foldVersion');
  for (const feature of needs.features) {
    if (!worker.features.includes(feature)) missing.push(`feature:${feature}`);
  }
  return missing;
}

/**
 * After a claim: the claiming worker may write at its own fold, so from then
 * on only a worker at least as new may take the run.
 */
export function raisedByClaim(
  needs: RunRequirements,
  worker: ClaimingWorker,
): RunRequirements {
  return {
    ...needs,
    foldVersion: Math.max(needs.foldVersion, worker.foldVersion),
  };
}

export const withFeature = (
  needs: RunRequirements,
  ...added: WorkerFeature[]
): RunRequirements => ({
  ...needs,
  features: known([...needs.features, ...added]),
});

/** The worker holding the run's current attempt accepts `feature`. */
export const attemptAccepts = (
  run: Pick<HarnessRun, 'attemptFeatures'>,
  feature: WorkerFeature,
): boolean =>
  run.attemptFeatures === undefined ||
  run.attemptFeatures === null ||
  run.attemptFeatures.includes(feature);

/** A queued run no claimer that asked could run: marked once, until a claim takes it. */
export interface WaitingForWorker {
  since: number;
  requires: RunRequirements;
}

export const WORKER_INCOMPATIBLE = 'WORKER_INCOMPATIBLE' as const;

export const workerIncompatible = (): HarnessConflictError =>
  new HarnessConflictError(
    WORKER_INCOMPATIBLE,
    'This worker cannot run the run; it waits for one that can',
  );

/**
 * A claim released before its runtime was built (the worker began draining)
 * did not attempt the run: it does not count towards the crash-loop cap.
 */
export const unattemptedRelease = (reason: string): boolean =>
  reason === 'drain';
