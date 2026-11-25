import type { AccessGroupsPolicy } from '../../../control-plane';
import type { Heads, ResolvedRunConfig } from '../../../control-plane';
import type { RunSurface } from '../../domain/admission';
import type { AgentRunDefinition } from '../../domain/agent-definition';
import type { PinnedEngineProfile } from '../../domain/engine-profiles';
import type { RunInputSnapshot } from '../../domain/execution';
import type { MemoryAdmission } from '../../domain/memory';
import type { PinnedModelAccess } from '../../domain/model-access';
import type { ExecutorKind, RunVia } from '../../domain/run-spec';
import type { RunNotice } from '../../domain/execution';
import type { PrincipalKind } from '../../domain/principal';
import type { SessionRepository } from '../../domain/repository';
import type { RunGates } from '../../domain/run-gates';
import type { AnsweredRun } from '../../domain/answered-run';
import type { RunTrigger } from '../../domain/schedules';
import type { ResolvedRoute } from '../../ports/routing-decider';

export interface SubmitDurableRun {
  orgId: string;
  actorId: string;
  agentKey?: string;
  sessionId?: string;
  idempotencyKey: string;
  deadlineMs: number;
  input: RunInputSnapshot;
  /** Caller's Authorization header; required to authorize an agentKey. */
  authorization?: string;
  /** How the caller authenticated; `session` unless the transport says otherwise. */
  via?: RunVia;
  /** Who the run acts as, from the verified credential (OWD-2); a person unless the transport says otherwise. */
  principalKind?: PrincipalKind;
  /** Where the run was started; `web` unless an external surface says so. */
  surface?: RunSurface;
  /**
   * The idempotency key was minted for this request (an external surface
   * sends none), so no earlier admission can hold it: a legacy decision
   * reads nothing.
   */
  firstSubmission?: boolean;
  /** The client sends to the running task only (T2): never a new run. */
  steerOnly?: boolean;
  /**
   * G-52: the person's locale, from the request's `Accept-Language` (`requestLocale`); the run's input carries it
   * only while a live worker reads it (`OutputLocaleStep`).
   */
  locale?: string;
  /** The project a request names for the conversation it creates; routing reads an existing one's from its record. */
  projectId?: string;
  /** Regenerate the conversation's last answer (UX3); only on an existing session. */
  regenerate?: {
    messageId: string;
    /** A stored `/review`'s fresh expansion, made by the route (LC2FIX): asked in place of what its message keeps. */
    query?: string;
  };
  /** A schedule's firing (P8a): admitted into a new session as `runKind: scheduled`. */
  trigger?: RunTrigger;
  /** The agent version the schedule pinned (OWD-14); absent: the agent's latest published version. */
  agentVersion?: number;
  /** The assistant's turn asked for the durable harness (ASSIST1): routing reads it, the run never carries it. */
  durableRequested?: boolean;
  /** The person's chat switch asked for the classic path (CTL-7): routing reads it, the run never carries it. */
  legacyRequested?: boolean;
  /**
   * The coding engine the person chose before this chat's first message (CMDUI2): an engine kind or `none`.
   * Read only by a turn that creates its chat; checked as `/engine` checks a choice, and saved on the chat
   * the turn creates. The run never carries it.
   */
  codingEngine?: string;
  /**
   * The GitHub repository the person chose before this chat's first message (GIT-FLOW, the composer chip). Read
   * only by a turn that creates its chat; checked as `PUT …/repository` checks it and refused with its code, then
   * saved on the chat the turn creates. An existing chat's own is read from the chat.
   */
  repository?: SessionRepository;
  /**
   * G-03..G-05: the run gates the turn asks for (a dry run, plan approval, a verify gate). Kept on the run and pinned
   * as `RunSpec.gates`; a gate only narrows what the run may do, so any sender may ask for one.
   */
  gates?: RunGates;
  /**
   * WP-64 (G-13): the run whose question this turn answers, as the inbox names it. Admission reads it, in this org's
   * session only, and holds the turn to its gates and budget; one it cannot find refuses the turn. A turn that names
   * none is checked against the session's newest run all the same, so the chat's question card answers alike.
   */
  answers?: string;
}

/** Where an admission's time went, for its log line. */
export interface AdmissionTiming {
  rolloutMs?: number;
  authorizeMs?: number;
  admitMs?: number;
}

/**
 * What the policy steps decide for one admission, in the order they decide
 * it. The store gets the decisions on its command; nothing here is written
 * before every step passed.
 */
export interface PolicyContext {
  readonly request: SubmitDurableRun;
  readonly timing: AdmissionTiming;
  /** What the agent grants the sender; null for the built-in assistant. */
  agentDefinition: AgentRunDefinition | null;
  engineProfile: PinnedEngineProfile | null;
  /** CTL-6: `engineProfile` is the loop's engine authority for `code_task`, not the run's executor. */
  codeTask?: boolean;
  /** DEEPROUTE: false when the org turned off handing long work to the engine on its own (with `codeTask` only). */
  codeTaskAutoRoute?: boolean;
  modelAccess: PinnedModelAccess | null;
  executor: ExecutorKind;
  agentCode: boolean | undefined;
  memory: MemoryAdmission | undefined;
  /** A queued next run decided again as it drains (SS2), not a turn just sent. */
  readonly draining: boolean;
  /** What the control plane resolved the turn's route on (CPD-10): the pin is made on the same facts and snapshot. */
  routed?: ResolvedRoute;
  /** `shadow`: the same, resolving beside today's decision; no step of the turn's own waits for it. */
  shadowRoute?: Promise<ResolvedRoute | undefined>;
  /** The run's pin, under `CONTROL_PLANE_PIN=on` (CPD-5). */
  resolved?: ResolvedRunConfig;
  /** The org head and the deployment's revision the pin was resolved at: admission holds it to both. */
  resolvedAt?: Heads;
  /** A live worker reads the pin, so the run requires `resolved_config` (CPD-6). */
  resolvedConfig?: boolean;
  /** A live worker reads the run's surface, so the run requires `run_surface` (FU4). */
  runSurface?: boolean;
  /** A live worker reads the run's release pin, so the run requires `release_pin` (C2.07). */
  releasePin?: boolean;
  /** G-48: a live worker moves a run whose provider is down to an alternate, so the run pins the agent's others. */
  modelFallback?: boolean;
  /** A live worker reads the run's deliverable pin, so the run requires `deliverable_pin` (ADR-RUN-01 D1). */
  deliverablePin?: boolean;
  /** G-52: the locale the run's input carries, as a live worker reads it (`output_locale`); absent: none. */
  outputLocale?: string;
  /** R33JGAPS-FU-1: a live worker reads the input's `keywordSearchBudget`; otherwise the run is admitted without it. */
  keywordSearchBudget?: boolean;
  /**
   * G-41: the org's group access section as this admission read it (null: none), so its engine and model checks
   * read it once; undefined until the harness access step ran, or where no group access is composed.
   */
  accessGroups?: AccessGroupsPolicy | null;
  /** What the person is told about the run beside its steps (CTL-5b), journaled with run.admitted. */
  notices?: RunNotice[];
  /** Routing found the classic path asked for a conversation with no durable turns (CTL-7). */
  classicRequested?: boolean;
  /** The request's `codingEngine` passed the `/engine` checks (CMDUI2): the new chat's engine, saved once it exists. */
  newChatEngine?: string;
  /** C14.01: the engine version (`kind@version`) this turn runs the chat's engine on, when the chat holds another. */
  enginePin?: string;
  /** GIT-FLOW: the repository the run pins (`x.pipeshub.repository`): the chat's, on a turn that hands work to an engine. */
  repository?: SessionRepository;
  /** The request's `repository` passed the checks: the new chat's, saved once it exists. */
  newChatRepository?: SessionRepository;
  /** WP-64: the run this turn answers; the run is held to its gates and its spending cap. */
  answered?: AnsweredRun;
}

export const policyContext = (
  request: SubmitDurableRun,
  timing: AdmissionTiming,
  draining = false,
): PolicyContext => ({
  request,
  timing,
  draining,
  agentDefinition: null,
  engineProfile: null,
  modelAccess: null,
  executor: 'loop',
  agentCode: undefined,
  memory: undefined,
});
