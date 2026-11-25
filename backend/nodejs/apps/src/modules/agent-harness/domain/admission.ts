import type { AdmissionQuotaCaps } from './admission-quotas';
import { runPolicyFields } from './run-policy-fields';
import type { RunAttribution } from './principal';
import type { RunNotice } from './execution';
import type { Heads, ResolvedRunConfig } from '../../control-plane';
import { HarnessConflictError } from './errors';
import { workerCommand } from './commands';
import type { MemoryAdmission, MemoryPin } from './memory';
import type { PinnedModelAccess } from './model-access';
import { canonicalSha256 } from '../contracts/canonical-json';
import { HarnessRun, RunInputSnapshot, RunProfile } from './execution';
import { HarnessMajor } from './rollout';
import { AgentRunDefinition, bindsDescriptors } from './agent-definition';
import type { PinnedEngineProfile } from './engine-profiles';
import type { ExecutorKind, RunVia } from './run-spec';
import { SESSION_ROLES_FEATURE, type RunAudience } from './session-audience';
import {
  admissionRequirements,
  MODEL_FALLBACK_FEATURE,
  pinsSystemAssistant,
} from './worker-compat';
import type { SessionImportSummary } from './session-import';
import type { LabelledTurn } from './turn-labels';
import type { RunTrigger } from './schedules';
import { admittedRunKind } from './run-kind';
import { priorityClassForRunKind, priorityFields } from './priority-class';
import type { SessionRepository } from './repository';
import { RUN_GATES_FEATURE, type RunGates } from './run-gates';
import { pinsEnvironment } from './agent-environment';
import type { AnsweredRun, AnsweredRunPin } from './answered-run';

/**
 * Where a run was started: the web app, or an external surface that gets
 * what its legacy route rendered (REPORT 8 audiences). A log and audience
 * label; it never grants anything.
 */
export type RunSurface = 'web' | 'slack' | 'mcp';

/**
 * A chat message's length bound, a new turn's and a steering message's alike:
 * the legacy chat routes' (`enterpriseSearchCreateBodySchema.query`).
 */
export const CHAT_QUERY_MAX_CHARS = 100_000;
export type ExternalSurface = Exclude<RunSurface, 'web'>;

export interface AdmitRunCommand {
  orgId: string;
  actorId: string;
  agentKey?: string;
  sessionId: string | null;
  idempotencyKey: string;
  payloadHash: string;
  runId: string;
  dispatchCommandId: string;
  absoluteDeadlineAt: number;
  input: RunInputSnapshot;
  /** Decided once at admission; the run keeps it for its whole life. */
  harnessMajor: HarnessMajor;
  /**
   * What the agent granted this actor at admission: the upper bound the claim
   * resolves the RunSpec from. Kept off the idempotency hash, so a replay
   * after the agent changed still replays.
   */
  agentDefinition?: AgentRunDefinition;
  /**
   * The engine profile resolved at admission (E4a): release defaults under the
   * org ceiling and the agent's narrowing. The claim pins it; a later change
   * to the ceiling reaches the next run, never this one.
   */
  engineProfile?: PinnedEngineProfile;
  /**
   * The model access resolved at admission (A1): the concrete model and the
   * account route an engine run pins. Absent for a loop run.
   */
  modelAccess?: PinnedModelAccess;
  /** How the actor reached the run (x.pipeshub `via`). */
  via: RunVia;
  /** Who the run acts as, who started it and on whose authority (OWD-2); absent on commands queued before it. */
  attribution?: RunAttribution;
  /** Where the run was started; the claim pins it as `x.pipeshub.surface` only when `runSurface`. */
  surface?: RunSurface;
  /**
   * A live worker reads `x.pipeshub.surface` (FU4): the run requires `run_surface`. Absent: the spec carries no
   * surface and the run shows no MCP sign-in card, as before the feature.
   */
  runSurface?: boolean;
  /**
   * A live worker reads `x.pipeshub.release` (C2.07): the run requires `release_pin` and its claim pins the
   * contract, catalog and policy it was admitted under, and an engine run its image digest. Absent: none pinned.
   */
  releasePin?: boolean;
  /**
   * A live worker reads `x.pipeshub.deliverable` (ADR-RUN-01 D1): the run requires `deliverable_pin` and its claim
   * pins what the run owes, resolved once from its request. Absent: the worker resolves it when it loads.
   */
  deliverablePin?: boolean;
  /**
   * G-48: a live worker moves a loop run whose model provider is down to an alternate route (`model_fallback`): the
   * run requires it and its claim pins the agent's other models as alternates. Absent: one route, as before.
   */
  modelFallback?: boolean;
  /**
   * Only join the session's active run (T2): with none, RUN_NOT_ACTIVE and
   * nothing written, so a client that meant to steer never starts a run.
   */
  steerOnly?: boolean;
  /**
   * Regenerate (UX3): the conversation's last answer, `messageId`, and its
   * question leave the conversation in the admission's transaction, and the
   * question is this run's input as stored. Refused `REGENERATE_NOT_LAST`
   * when it is not the last answer, `RUN_ACTIVE` while a run is active.
   */
  regenerate?: {
    messageId: string;
    /** A stored `/review`'s fresh expansion, made by the route (LC2FIX): asked in place of what its message keeps. */
    query?: string;
  };
  /**
   * The caller may read every attachment and record the input names (C-21):
   * checked by the admission service before a steering message is queued.
   */
  referencesAuthorized?: boolean;
  /**
   * `inline`: the admitting process claims it; no dispatch until `T_CLAIM_MS`
   * passes unclaimed. Absent: dispatched at once.
   */
  profile?: RunProfile;
  /**
   * The actor is a collaborator of the session (SS2), as admission read it:
   * the store binds the session through the actor's share, checked again in
   * its own transaction. Absent: the actor must own the session.
   */
  asCollaborator?: boolean;
  /**
   * The next run this admission drains (SS2): with an active run in the
   * session it is refused `RUN_ACTIVE`, never queued again or joined.
   */
  nextRunId?: string;
  /**
   * Other participants' runs of the session whose label passed for the actor
   * (P2.5a), decided by the admission service with the actor's own credential
   * just before the store; pinned with the audience. Absent: none passed.
   */
  visibleRuns?: string[];
  /** Notices journaled with run.admitted (CTL-5b, `x.pipeshub.run.notice`). */
  notices?: RunNotice[];
  /**
   * The schedule firing this run is (P8a): kept on the run, store-only; the
   * claim pins `runKind: scheduled` and the firing's budget from it.
   */
  trigger?: RunTrigger;
  /**
   * The memories the actor may be given and the scopes the run may propose
   * to (P7.3), decided with the actor's own credential just before the
   * store; the store narrows it to the run's context and pins it. Absent:
   * memory off.
   */
  memory?: MemoryAdmission;
  /**
   * An agent loop run admitted while the deployment proved a code sandbox
   * (P5.11): it requires `agent_code`, and its claim pins `agent-code` if
   * the capabilities it pins still prove one.
   */
  agentCode?: boolean;
  /**
   * CTL-6: an engine agent's turn runs on the loop and keeps `engineProfile` as its engine authority, which the
   * loop's `code_task` child narrows. The run requires `code_task_tool`; the claim pins it as `x.pipeshub.codeTask`.
   */
  codeTask?: boolean;
  /**
   * DEEPROUTE: the org turned off handing long work to the engine on its own; kept on the run and pinned as
   * `x.pipeshub.codeTask.autoRoute: false`. Absent: on. Only with `codeTask`.
   */
  codeTaskAutoRoute?: false;
  /**
   * GIT-FLOW (WP-26): the chat's GitHub repository, on a run that hands work to an engine; kept on the run and
   * pinned as `x.pipeshub.repository` (an engine run's and a code_task parent's, which its child inherits).
   */
  repository?: SessionRepository;
  /**
   * G-03..G-05: the run gates the turn asked for; kept on the run and pinned as `RunSpec.gates`. The run requires
   * `run_gates`, and so does every later run of its session (they fold what the gates journal).
   */
  gates?: RunGates;
  /**
   * WP-64: the run whose question this turn answers; its gates are already in `gates`. Kept on the run, store-only:
   * the claim narrows the run's budget to the cap it carries on, and a later answer to this run carries it again.
   */
  answered?: AnsweredRunPin;
  /**
   * The run's pin (CPD-5, `CONTROL_PLANE_PIN=on`): written on the run in this
   * admission's transaction, which first checks the pin is still current
   * (`PinStale` otherwise). The legacy pinned fields are derived from it at
   * claim. Absent: today's resolution.
   */
  resolved?: ResolvedRunConfig;
  /**
   * C4.10 `quotas.*`: the org's caps on its active runs, read before the
   * transaction (from the pin when there is one); absent: the release's.
   */
  quotaCaps?: AdmissionQuotaCaps;
  /**
   * The org head and the deployment's revision the pin was resolved at
   * (CPD-10). Beside the pin, not in it: a worker of the release before
   * refuses a pin member it does not know.
   */
  resolvedAt?: Heads;
  /**
   * A live worker reads the pin (CPD-6): the run requires `resolved_config`
   * and its claim carries `x.pipeshub.resolved`. Absent: the pin stays on the
   * run and the worker reads the spec's legacy fields, as before the feature.
   */
  resolvedConfig?: boolean;
}

/**
 * The org head or the deployment's revision moved between the pin and the
 * admission's transaction (02-lld §6.3, `CP_STALE`): nothing was written;
 * admission re-pins once on a refreshed snapshot.
 */
export class PinStale extends Error {
  readonly code = 'CP_STALE';

  constructor() {
    super('The control plane changed while the run was admitted');
    this.name = 'PinStale';
  }
}

/**
 * A pin is admissible only at the revisions it was resolved at: the org's
 * head, which every control-plane write of the org moves, and the
 * deployment's, which a write to a document all orgs read moves (an AI-model
 * save). One that does not say where it was resolved is not admissible.
 */
export function assertPinCurrent(
  command: Pick<AdmitRunCommand, 'resolved' | 'resolvedAt'>,
  heads: Heads,
): void {
  if (command.resolved === undefined) return;
  const at = command.resolvedAt;
  if (
    at?.org !== heads.org ||
    at.deployment !== heads.deployment ||
    command.resolved.headRevision !== heads.org
  ) {
    throw new PinStale();
  }
}

/** A message queued behind someone else's running task (SS2 `next_run`). */
export interface QueuedNextRun {
  id: string;
  /** The run it becomes, admitted as its sender when the active run ends. */
  runId: string;
  /** 1 for the first in line. */
  position: number;
}

export type AdmissionIdentity = Pick<
  AdmitRunCommand,
  'orgId' | 'actorId' | 'agentKey' | 'sessionId' | 'idempotencyKey'
>;

/** The receipt key an idempotent retry of the same submission maps to. */
export const admissionScopeKey = (command: AdmissionIdentity): string =>
  canonicalSha256([
    command.orgId,
    command.actorId,
    command.agentKey ?? 'plain-chat',
    command.sessionId ?? 'new-session',
    command.idempotencyKey,
  ]);

/** The title a conversation a turn starts is stored with: its first message, cut to 100 characters. */
export const durableSessionTitle = (
  command: Pick<AdmitRunCommand, 'input'>,
): string => command.input.query.slice(0, 100);

export interface RunAdmissionResult {
  sessionId: string;
  userMessageKey: string;
  run: HarnessRun;
  firstEventSeq: number;
  lastEventSeq: number;
  created: boolean;
  /**
   * The message joined the session's active run (steering, T2) instead of
   * starting one: `run` is that run, `messageSeq` names the message in it.
   */
  queued?: { messageSeq: number };
  /** History the admission imported before the run (`session.imported`, T3). */
  imported?: SessionImportSummary;
  /**
   * The conversation this admission created, as stored: its creator's turn
   * streams without reading it back (WP88-FU-4). Never replayed: a replay
   * reads what is stored now.
   */
  createdSession?: { title: string };
  /**
   * The message waits for the session's active run, whose actor is someone
   * else, to end (SS2): `run` is that run, with no input.
   */
  nextRun?: QueuedNextRun;
}

export interface RunAdmissionStore {
  admit(command: AdmitRunCommand, now: number): Promise<RunAdmissionResult>;
  /**
   * The stored admission for this submission, or null. Lets a retry replay an
   * admitted run even after the rollout stopped admitting new ones; a
   * different payload under the same key is an idempotency conflict.
   */
  findAdmission(
    command: AdmissionIdentity & Pick<AdmitRunCommand, 'payloadHash'>,
  ): Promise<RunAdmissionResult | null>;
  /**
   * The session's finished root runs someone other than `actorId` started
   * that carry a complete label (P2.5a), newest first, at most
   * `MAX_VISIBLE_RUNS`: what admission checks before the actor's run may see
   * them. Empty for a session the store does not hold. A store without it
   * lists none: every foreign turn stays a stub.
   */
  labelledTurns?(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<LabelledTurn[]>;
  /**
   * WP-64: the root run of the session a turn answers, or null. With `runId` (the inbox names it): that run, if it is a
   * root run of this org's session. Without: the session's newest root run, if it ended asking the person
   * (`needs_input`). Its gates and its own spending cap, as the answer is held to them.
   */
  answeredRun(
    orgId: string,
    sessionId: string,
    runId?: string,
  ): Promise<AnsweredRun | null>;
}

/** Regenerate (UX3) named something other than the conversation's last answer. */
export const notLastAnswer = (): HarnessConflictError =>
  new HarnessConflictError(
    'REGENERATE_NOT_LAST',
    'Only the conversation’s last answer can be regenerated',
  );

/**
 * How an admitted run executes (R3 WP-10): on an engine exactly when admission
 * resolved an engine profile for it. The claim pins the same answer through
 * `profileExecutor`; both stores ask here for the run's requirements.
 */
export const executorKindOf = (
  command: Pick<AdmitRunCommand, 'engineProfile' | 'codeTask'>,
): ExecutorKind =>
  command.engineProfile === undefined || command.codeTask === true
    ? 'loop'
    : 'engine';

/** What only a store knows when it writes a root run. */
export interface RootRunPlacement {
  sessionId: string;
  userMessageKey: string;
  /** The idempotency key as the store keys it (`RunKeyDigest`). */
  idempotencyKey: string;
  absoluteDeadlineAt: number;
  /** The admitted input; it carries the history the store read only when no store keeps it by reference. */
  input: RunInputSnapshot;
  now: number;
}

/**
 * The root run an admission writes, queued and unclaimed (R3 WP-10, D13): one
 * construction for both stores, so the contract suite holds them to one shape.
 */
export function rootRunDocument(
  command: AdmitRunCommand,
  placement: RootRunPlacement,
): HarnessRun {
  return {
    runId: command.runId,
    orgId: command.orgId,
    sessionId: placement.sessionId,
    actorId: command.actorId,
    ...(command.agentKey !== undefined ? { agentKey: command.agentKey } : {}),
    ...(command.agentKey !== undefined &&
    command.agentDefinition?.agentKey === command.agentKey
      ? { agentVersion: command.agentDefinition.definitionVersion }
      : {}),
    ...(command.attribution !== undefined
      ? { attribution: { ...command.attribution } }
      : {}),
    userMessageKey: placement.userMessageKey,
    idempotencyKey: placement.idempotencyKey,
    payloadHash: command.payloadHash,
    harnessMajor: command.harnessMajor,
    profile: command.profile ?? 'dispatched',
    status: 'queued',
    outcome: null,
    ...priorityFields(
      priorityClassForRunKind(
        admittedRunKind(
          { input: placement.input },
          command.trigger !== undefined,
        ),
      ),
    ),
    revision: 0,
    fence: 0,
    attemptId: null,
    workerId: null,
    leaseExpiresAt: null,
    absoluteDeadlineAt: placement.absoluteDeadlineAt,
    checkpoint: null,
    attempts: 0,
    notBefore: placement.now,
    ...(command.gates !== undefined
      ? { gates: structuredClone(command.gates) }
      : {}),
    ...runPolicyFields(command.resolved),
    checkpointRef: null,
    input:
      placement.input.command !== undefined
        ? {
            ...placement.input,
            command: workerCommand(placement.input.command),
          }
        : placement.input,
    createdAt: placement.now,
    updatedAt: placement.now,
  };
}

/**
 * What every later run of the session must fold because of this one: its sessionRole (SS1), and what its gates
 * journal (G-03..G-05: `tool.outcome.shadow`, a plan's `tool.denied.toolId`, `verification.recorded`).
 */
export const sessionJournalFeatures = (
  run: Pick<HarnessRun, 'sessionRole'>,
  command: Pick<AdmitRunCommand, 'gates'>,
): string[] => [
  ...(run.sessionRole !== undefined ? [SESSION_ROLES_FEATURE] : []),
  ...(command.gates !== undefined ? [RUN_GATES_FEATURE] : []),
];

/**
 * What a worker must support to take a root run (RU-2), from the run, its
 * session's journal features and what admission decided; harness major 1 runs
 * record none.
 */
export function rootRunRequirements(
  run: HarnessRun,
  command: AdmitRunCommand,
  sessionFeatures: readonly string[],
  audience: Pick<RunAudience, 'visibleRuns'> | null,
  memory: MemoryPin | null,
): HarnessRun['requires'] {
  if (run.harnessMajor !== 2) return undefined;
  const requires = admissionRequirements(
    run,
    sessionFeatures,
    executorKindOf(command),
    audience,
    memory,
    command.agentCode === true,
    bindsDescriptors(command.agentDefinition ?? null),
    command.resolved !== undefined && command.resolvedConfig === true,
    pinsSystemAssistant(command.agentDefinition ?? null),
    command.codeTask === true,
    command.agentDefinition ?? null,
    command.runSurface === true,
    command.releasePin === true,
    command.gates !== undefined,
    command.deliverablePin === true,
    pinsEnvironment(
      command.agentDefinition?.environment,
      executorKindOf(command) === 'engine' || command.codeTask === true,
    ),
  );
  // G-48: only this run's spec pins alternates: not a session feature.
  return command.modelFallback === true
    ? {
        ...requires,
        features: [...requires.features, MODEL_FALLBACK_FEATURE].sort(),
      }
    : requires;
}
