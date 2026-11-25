import type { AdmissionQuotaCapsSource } from '../ports/admission-quota-caps';
import { quotaCapsOfPin } from '../domain/admission-quotas';
import { admittedGates, answerGates } from '../domain/run-gates';
import type { AnsweredRun } from '../domain/answered-run';
import type { RunPlacement } from '../ports/run-placement';
import { randomUUID } from 'node:crypto';
import { performance } from 'perf_hooks';
import type { AdmissionCodingEngine } from './coding-engine-service';
import type { PinnedEngineProfile } from '../domain/engine-profiles';
import { canonicalSha256 } from '../contracts/canonical-json';
import { runAttribution } from '../domain/principal';
import {
  AdmitRunCommand,
  RunAdmissionResult,
  PinStale,
  RunAdmissionStore,
} from '../domain/admission';
import {
  gatesNeedRuntime,
  HarnessForbiddenError,
  HarnessNotFoundError,
  HarnessRunConfigUnavailableError,
  HarnessUnavailableError,
} from '../domain/errors';
import {
  ExecutorKind,
  ExecutorResolver,
  profileExecutor,
} from '../domain/run-spec';
import { SessionRoleLookup } from '../ports/session-roles';
import { CollaboratorTurnsSetting } from '../ports/collaborator-turns';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { HarnessTracer, noopHarnessTracer } from './harness-tracing';
import { HarnessRolloutDecision, legacyFor } from '../domain/rollout';
import { AgentAccessPolicy } from '../ports/agent-access';
import { EngineAvailability } from '../ports/engine-availability';
import { EngineProfileCeilings } from '../ports/engine-profiles';
import { ModelAccessService } from './model-access-service';
import { HarnessRolloutPolicy } from '../ports/harness-rollout';
import { InlineClaimHint } from '../ports/inline-claim';
import {
  SteeringReferencesUnchecked,
  steeringReferences,
} from '../domain/steering';
import {
  RecordAccessUnavailableError,
  RecordReadAccess,
  SourceStatus,
} from '../ports/record-access';
import { sourceKey } from '../projection/citation-source';
import type { LabelledTurn } from '../domain/turn-labels';
import type { SharedCitationService } from './shared-citations';
import type { MemoryService } from './memory-service';
import type { CapabilitySource } from '../ports/capability-probe';
import {
  ChatSessionRepository,
  SessionExecutionScope,
} from '../ports/chat-session-repository';
import type { WorkerFeatureAvailability } from './worker-feature-availability';
import { ResolvedRoute, RoutingDecider } from '../ports/routing-decider';
import { RolloutRoutingDecider } from './rollout-routing';
import {
  AdmissionTiming,
  PolicyContext,
  policyContext,
  SubmitDurableRun,
} from './admission/context';
import { AdmissionLog, admissionLogFields } from './admission/admission-log';
import { AdmissionPipeline, AdmissionStep } from './admission/pipeline';
import {
  type AdmissionRepositories,
  type OperatorAdmission,
  policyPipeline,
  roleIn,
  RouteToLegacy,
} from './admission/policy-steps';
import type { SessionRepositoryService } from './session-repository-service';
import {
  AdmissionRefusal,
  asRefusal,
  refusal,
  refusedRole,
  settled,
} from './admission/refusals';
import { RolloutGate } from './admission/rollout-gate';
import type { GroupAccess } from '../ports/group-access';
import { GroupAccessRouting } from './group-access';
import { withOutputLocale } from '../domain/output-locale';
import {
  KEYWORD_SEARCH_BUDGET_FEATURE,
  withoutGuardedFields,
} from '../domain/worker-input';
import { redactPastedSecrets } from '../domain/pasted-secrets';

export type { SubmitDurableRun } from './admission/context';

export interface RunAdmissionClock {
  now(): number;
}

export type SubmitOutcome =
  | { route: 'durable'; admission: RunAdmissionResult }
  | {
      route: 'legacy';
      rollout: HarnessRolloutDecision;
      /** What the control plane resolved the turn on, when it decided (CPD-10): the legacy route's header is made of it. */
      resolved?: ResolvedRoute;
    };

const systemClock: RunAdmissionClock = { now: () => Date.now() };

/** A decided policy field on the command, or none when the decision is none. */
function setOrClear<K extends keyof AdmitRunCommand>(
  command: AdmitRunCommand,
  key: K,
  value: AdmitRunCommand[K] | null | undefined,
): void {
  if (value === null || value === undefined)
    Reflect.deleteProperty(command, key);
  else command[key] = value;
}

/** The policy steps' decisions, written onto the store's command. */
function recordDecisions(
  command: AdmitRunCommand,
  decided: PolicyContext,
): void {
  setOrClear(command, 'agentDefinition', decided.agentDefinition);
  setOrClear(command, 'engineProfile', decided.engineProfile);
  setOrClear(command, 'codeTask', decided.codeTask === true ? true : undefined);
  setOrClear(
    command,
    'codeTaskAutoRoute',
    decided.codeTask === true && decided.codeTaskAutoRoute === false
      ? false
      : undefined,
  );
  setOrClear(command, 'modelAccess', decided.modelAccess);
  setOrClear(command, 'agentCode', decided.agentCode);
  setOrClear(command, 'memory', decided.memory);
  setOrClear(command, 'repository', decided.repository);
  // WP-31 D: the agent's own gates hold every run of it; the turn's may add to them, never drop one.
  // WP-64: an answer is held to every gate of the run it answers as well.
  setOrClear(
    command,
    'gates',
    answerGates(
      decided.answered?.gates,
      admittedGates(decided.agentDefinition?.gates, decided.request.gates),
    ),
  );
  setOrClear(
    command,
    'answered',
    decided.answered === undefined
      ? undefined
      : {
          runId: decided.answered.runId,
          ...(decided.answered.maxCostUsd !== undefined
            ? { maxCostUsd: decided.answered.maxCostUsd }
            : {}),
        },
  );
  setOrClear(command, 'resolved', decided.resolved);
  setOrClear(command, 'resolvedAt', decided.resolvedAt);
  setOrClear(
    command,
    'resolvedConfig',
    decided.resolvedConfig === true ? true : undefined,
  );
  setOrClear(
    command,
    'runSurface',
    decided.runSurface === true ? true : undefined,
  );
  setOrClear(
    command,
    'releasePin',
    decided.releasePin === true ? true : undefined,
  );
  setOrClear(
    command,
    'deliverablePin',
    decided.deliverablePin === true ? true : undefined,
  );
  setOrClear(
    command,
    'modelFallback',
    decided.modelFallback === true ? true : undefined,
  );
  setOrClear(
    command,
    'notices',
    decided.notices?.length ? decided.notices : undefined,
  );
  command.input = withoutGuardedFields(
    withOutputLocale(command.input, decided.outputLocale),
    (feature) =>
      feature !== KEYWORD_SEARCH_BUDGET_FEATURE ||
      decided.keywordSearchBudget === true,
  );
}

/** A question left Needs you: the turn that moved its chat on was admitted (ADR-INBOX-02 D4). */
export type QuestionAnswered = (orgId: string, askingRunId: string) => void;

/** What admission is built from; everything but the store and the rollout is optional. */
export interface RunAdmissionServiceOptions {
  store: RunAdmissionStore;
  rollout: HarnessRolloutPolicy;
  clock?: RunAdmissionClock;
  newId?: () => string;
  agentAccess?: AgentAccessPolicy;
  /** Present where a process serves inline runs: they are admitted inline. */
  inline?: InlineClaimHint;
  logger?: HarnessLogger;
  /** The record authorization a steering message's references are checked by (C-21). */
  references?: RecordReadAccess;
  /** Explains a refused admission into someone else's session (SS0); absent: it stays not found. */
  roles?: SessionRoleLookup;
  /** How the run would execute, as its claim will pin it; `profileExecutor` unless given. */
  executors?: ExecutorResolver;
  /**
   * Org engine profile ceilings (E4a), read once per admission of an engine
   * agent. Absent: an engine agent is refused (503), never run on the defaults.
   */
  engineProfiles?: EngineProfileCeilings;
  /**
   * Model access (A1): the access route an engine run pins, decided against
   * the signed vendor rules and the org's policy. Absent: an engine run, or a
   * run naming a model account, is refused (503), never pinned unchecked.
   */
  modelAccess?: ModelAccessService;
  /**
   * Which engines the sandbox broker cannot run (H1): an engine run whose
   * engine it lists is refused `engine_unavailable`. Absent, or the broker
   * cannot be asked: admitted, and the run finds out when it starts.
   */
  engineAvailability?: EngineAvailability;
  /** ADR-OPS-01: the operator controls admission honours; absent: nothing is stopped. */
  operatorControls?: OperatorAdmission;
  /** The default chat's coding engine (ENGPICK); absent: its Agent mode offers no engine. */
  codingEngine?: AdmissionCodingEngine;
  /**
   * GIT-FLOW (WP-26): chats' GitHub repositories, pinned on the turns that hand work to an engine. Absent: a new
   * chat that names one is refused (503); other turns pin none.
   */
  repositories?: AdmissionRepositories &
    Pick<SessionRepositoryService, 'saveNewChat'>;
  /**
   * Where the org's runs may be placed (OWD-12), asked before anything is
   * written; it refuses with `RUN_PLACEMENT_REFUSED`. Absent: every run is placed here.
   */
  placement?: RunPlacement;
  /** The release's engine versions (C14.01); absent: this release's. Tests give their own. */
  engineReleases?: readonly PinnedEngineProfile[];
  /** Whether the org lets collaborators send turns (SS2); absent: it does not. */
  collaboratorTurns?: CollaboratorTurnsSetting;
  /**
   * Decides which other participants' turns pass for the sender (P2.5a), with
   * the sender's own credential. Absent: none does; every one stays a stub.
   */
  turnLabels?: Pick<SharedCitationService, 'passingTurns'>;
  /** The run's root span (P9, `HARNESS_OTEL`); absent: none is recorded. */
  tracer?: HarnessTracer;
  /**
   * Governed memory (P7.3): what the run is given and may propose, decided
   * with the sender's own credential. Absent: memory off.
   */
  memory?: Pick<MemoryService, 'admission'>;
  /**
   * The deployment's probed capabilities (P5.11): an agent loop run admitted
   * while they prove a code sandbox needs `agent_code`. Absent: none does.
   */
  capabilities?: CapabilitySource;
  /**
   * Which features a live worker advertises (DUR2): `agent_code` is required
   * only while one does, so a rolling deploy that upgrades Node first runs agent
   * runs as before P5.11 instead of queueing them for a worker that does not
   * exist yet. Absent: required whenever the sandbox is proven.
   */
  workerFeatures?: Pick<WorkerFeatureAvailability, 'live' | 'metrics'>;
  /** Where an existing session sits (UX3): a project's turn routes to legacy. Absent: not checked. */
  sessions?: Pick<ChatSessionRepository, 'placement'>;
  /** Whether any worker was seen lately (UX3): absent, a turn is admitted whoever is there. */
  liveness?: Pick<WorkerFeatureAvailability, 'live'>;
  /**
   * Durable or legacy for a turn (LAT1). Absent: today's rollout with
   * `sessions` and `liveness`, until CPD-10 supplies the resolved one.
   */
  routing?: RoutingDecider;
  /** Steps that read every policy decision, after them (CPD-5's policy pinning). */
  policySteps?: readonly AdmissionStep<PolicyContext>[];
  /** Pins again on a refreshed snapshot when the store finds the org head moved (`CP_STALE`, CPD-5 `on`). */
  repin?: (context: PolicyContext) => Promise<void>;
  /** C4.10: an org's caps on its active runs for a run admitted without a pin; absent: the release's quotas. */
  quotaCaps?: AdmissionQuotaCapsSource;
  /**
   * G-41 (GA-21): who may use the harness, each engine and each model, by the org's groups, read live at every
   * admission and readmission. Absent: nothing is limited by group.
   */
  groupAccess?: GroupAccess;
  /**
   * Told once a turn that answers a run's question was admitted (ADR-INBOX-02): the question has left its asker's
   * Needs you. `askingRunId` names the run that asked, whose actor need not be the sender. Must not throw.
   */
  questionAnswered?: QuestionAnswered;
  /**
   * Told of every turn the harness takes (ADR-OPS-08): an org whose grant repair failed when its harness turned on
   * tries again. Must not throw or wait.
   */
  capabilityRepair?: CapabilityRepairOnTurn;
}

export interface CapabilityRepairOnTurn {
  noteTurn(orgId: string): void;
}

export class RunAdmissionService {
  private readonly store: RunAdmissionStore;
  private readonly gate: RolloutGate;
  private readonly policy: AdmissionPipeline<PolicyContext>;
  private readonly log: AdmissionLog;
  private readonly logger: HarnessLogger;
  private readonly clock: RunAdmissionClock;
  private readonly newId: () => string;
  private readonly inline?: InlineClaimHint;
  private readonly references?: RecordReadAccess;
  private readonly roles?: SessionRoleLookup;
  private readonly collaboratorTurns?: CollaboratorTurnsSetting;
  private readonly turnLabels?: Pick<SharedCitationService, 'passingTurns'>;
  private readonly repin?: (context: PolicyContext) => Promise<void>;
  private readonly quotaCaps?: AdmissionQuotaCapsSource;
  private readonly codingEngine?: RunAdmissionServiceOptions['codingEngine'];
  private readonly repositories?: RunAdmissionServiceOptions['repositories'];
  private readonly placement?: RunPlacement;
  private readonly questionAnswered?: QuestionAnswered;
  private readonly capabilityRepair?: CapabilityRepairOnTurn;

  constructor(options: RunAdmissionServiceOptions) {
    const logger = options.logger ?? silentHarnessLogger;
    this.store = options.store;
    if (options.capabilityRepair !== undefined)
      this.capabilityRepair = options.capabilityRepair;
    if (options.quotaCaps !== undefined) this.quotaCaps = options.quotaCaps;
    const routing =
      options.routing ??
      new RolloutRoutingDecider({
        rollout: options.rollout,
        ...(options.sessions !== undefined
          ? { sessions: options.sessions }
          : {}),
        ...(options.liveness !== undefined
          ? { liveness: options.liveness }
          : {}),
      });
    this.gate = new RolloutGate(
      options.groupAccess === undefined
        ? routing
        : new GroupAccessRouting(routing, options.groupAccess),
    );
    this.policy = policyPipeline({
      ...(options.agentAccess !== undefined
        ? { agentAccess: options.agentAccess }
        : {}),
      executors: options.executors ?? profileExecutor,
      ...(options.engineProfiles !== undefined
        ? { engineProfiles: options.engineProfiles }
        : {}),
      ...(options.engineAvailability !== undefined
        ? { engineAvailability: options.engineAvailability }
        : {}),
      ...(options.operatorControls !== undefined
        ? { operatorControls: options.operatorControls }
        : {}),
      ...(options.modelAccess !== undefined
        ? { modelAccess: options.modelAccess }
        : {}),
      ...(options.capabilities !== undefined
        ? { capabilities: options.capabilities }
        : {}),
      ...(options.workerFeatures !== undefined
        ? { workerFeatures: options.workerFeatures }
        : {}),
      ...(options.roles !== undefined ? { roles: options.roles } : {}),
      ...(options.memory !== undefined ? { memory: options.memory } : {}),
      ...(options.codingEngine !== undefined
        ? { codingEngine: options.codingEngine }
        : {}),
      ...(options.engineReleases !== undefined
        ? { engineReleases: options.engineReleases }
        : {}),
      ...(options.repositories !== undefined
        ? { repositories: options.repositories }
        : {}),
      logger,
      ...(options.policySteps !== undefined
        ? { after: options.policySteps }
        : {}),
      ...(options.groupAccess !== undefined
        ? { groupAccess: options.groupAccess }
        : {}),
    });
    this.logger = logger;
    this.log = new AdmissionLog(logger, options.tracer ?? noopHarnessTracer);
    this.clock = options.clock ?? systemClock;
    this.newId = options.newId ?? randomUUID;
    this.inline = options.inline;
    this.references = options.references;
    this.roles = options.roles;
    this.collaboratorTurns = options.collaboratorTurns;
    this.turnLabels = options.turnLabels;
    this.repin = options.repin;
    this.codingEngine = options.codingEngine;
    this.repositories = options.repositories;
    this.placement = options.placement;
    this.questionAnswered = options.questionAnswered;
  }

  /**
   * Admits a durable run when the rollout allows it; otherwise tells the caller
   * to use the legacy path. Nothing is written on the legacy route. Every
   * decision is logged with its reason code; never the input or the caller's
   * credential.
   */
  async submit(submitted: SubmitDurableRun): Promise<SubmitOutcome> {
    // SEC-CR-07: every admission path (chat, sessions, steering, schedules),
    // not only the routes that strip a pasted secret first.
    const request = withoutPastedSecrets(submitted);
    const fields = admissionLogFields({
      ...request,
      chatMode: request.input.model.chatMode,
    });
    const started = performance.now();
    const startedAt = Date.now();
    const timing: AdmissionTiming = {};
    let outcome: SubmitOutcome;
    try {
      outcome = await this.decide(request, timing);
    } catch (error) {
      const refused = asRefusal(error);
      this.log.refused(fields, refused);
      throw refused.error;
    }
    try {
      this.log.outcome(fields, request, outcome, timing, {
        ms: elapsedMs(started),
        startedAt,
      });
    } catch {
      // A log line must never fail an admission that succeeded.
    }
    return outcome;
  }

  private async decide(
    request: SubmitDurableRun,
    timing: AdmissionTiming,
  ): Promise<SubmitOutcome> {
    let step = performance.now();
    const decision = await this.gate.decide(request);
    const rollout = decision.routing;
    timing.rolloutMs = elapsedMs(step);
    const now = this.clock.now();
    const command = this.command(request, rollout, now);
    const answered = await this.answeredRun(request);
    if (!rollout.useHarness) {
      const legacy: SubmitOutcome = {
        route: 'legacy',
        rollout,
        ...(decision.resolved !== undefined
          ? { resolved: decision.resolved }
          : {}),
      };
      if (request.firstSubmission !== true) {
        const replay = await this.store.findAdmission(command);
        if (replay !== null) return { route: 'durable', admission: replay };
      }
      assertUngatedForLegacy(answered);
      return legacy;
    }
    this.capabilityRepair?.noteTurn(request.orgId);
    if (this.placement !== undefined) {
      try {
        await this.placement.place(request.orgId);
      } catch (error) {
        throw refusal(error, 'region');
      }
    }
    // The sender's policy and the labelled turns are independent reads; a refusal still wins in this order.
    const decided = policyContext(request, timing);
    if (answered !== null) decided.answered = answered;
    if (decision.resolved !== undefined) decided.routed = decision.resolved;
    if (decision.shadow !== undefined) decided.shadowRoute = decision.shadow;
    if (decision.classicRequested === true) decided.classicRequested = true;
    const [policy, visible] = await Promise.allSettled([
      this.policy.run(decided),
      this.visibleRuns(request),
    ]);
    if (
      policy.status === 'rejected' &&
      policy.reason instanceof RouteToLegacy
    ) {
      assertUngatedForLegacy(answered);
      const resolved = decision.resolved;
      return {
        route: 'legacy',
        rollout: legacyFor(policy.reason.reason),
        // The legacy turn's pin holds the choice as its route (CPD-10), not a durable route it did not take.
        ...(resolved !== undefined
          ? {
              resolved: {
                ...resolved,
                facts: { ...resolved.facts, classicChosen: true as const },
              },
            }
          : {}),
      };
    }
    settled(policy);
    recordDecisions(command, decided);
    const { executor } = decided;
    // The inline claimer has no engine kernel: an engine run goes straight to a worker that has one.
    if (this.inline !== undefined && executor !== 'engine')
      command.profile = 'inline';
    const visibleRuns = settled(visible);
    if (visibleRuns.length > 0) command.visibleRuns = visibleRuns;
    let admission: RunAdmissionResult;
    step = performance.now();
    try {
      admission = await this.admitPinned(request, command, now, decided);
    } catch (error) {
      if (error instanceof AdmissionRefusal) throw error;
      const collaborator = await this.asCollaborator(request, error, executor);
      if (collaborator !== true) {
        throw collaborator ?? refusal(error, 'unavailable');
      }
      // A collaborator sends as themselves (SS2); the store checks the share
      // again in its own transaction.
      try {
        admission = await this.admitPinned(
          request,
          { ...command, asCollaborator: true },
          now,
          decided,
        );
      } catch (retried) {
        if (retried instanceof AdmissionRefusal) throw retried;
        throw refusal(retried, 'unavailable');
      }
    }
    timing.admitMs = elapsedMs(step);
    await this.saveNewChatEngine(request, decided, admission);
    await this.saveNewChatRepository(request, decided, admission);
    await this.saveEnginePin(request, decided, admission);
    if (
      this.inline !== undefined &&
      admission.created &&
      admission.queued === undefined &&
      admission.run.profile === 'inline'
    ) {
      // Not awaited: admission answers at once, and the hint never rejects.
      void this.inline.hint({
        orgId: admission.run.orgId,
        runId: admission.run.runId,
      });
    }
    return { route: 'durable', admission };
  }

  /**
   * The new chat's checked engine (CMDUI2), saved on the session this admission created before the turn's
   * stream opens, so the chat's next turn and `/engine` read it. The run is already pinned to it. A save
   * that fails is logged: the chat then reads as the person's default, as after a refused `/engine`.
   */
  private async saveNewChatEngine(
    request: SubmitDurableRun,
    decided: PolicyContext,
    admission: RunAdmissionResult,
  ): Promise<void> {
    const engine = decided.newChatEngine;
    if (engine === undefined || this.codingEngine === undefined) return;
    const sessionId = admission.run.sessionId;
    try {
      await this.codingEngine.saveSessionChoice(
        request.orgId,
        request.actorId,
        sessionId,
        engine,
      );
    } catch (error) {
      this.logger.warn(
        'run.coding_engine_choice_not_saved',
        harnessLogMeta('run.coding_engine_choice_not_saved', {
          orgId: request.orgId,
          sessionId,
          runId: admission.run.runId,
          engine,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /**
   * The new chat's checked repository (GIT-FLOW), saved on the session this admission created, so its next turns
   * and `GET …/repository` read it. The run is already pinned to it. A save that fails is logged: the chat's next
   * turn then runs without it, as a chat that never chose one.
   */
  private async saveNewChatRepository(
    request: SubmitDurableRun,
    decided: PolicyContext,
    admission: RunAdmissionResult,
  ): Promise<void> {
    const repository = decided.newChatRepository;
    if (
      repository === undefined ||
      this.repositories === undefined ||
      !admission.created
    ) {
      return;
    }
    const sessionId = admission.run.sessionId;
    try {
      await this.repositories.saveNewChat(
        request.orgId,
        request.actorId,
        sessionId,
        repository,
      );
    } catch (error) {
      this.logger.warn(
        'run.repository_not_saved',
        harnessLogMeta('run.repository_not_saved', {
          orgId: request.orgId,
          sessionId,
          runId: admission.run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /**
   * The engine version this turn ran the chat's engine on (C14.01), saved after the new chat's choice (whose
   * save clears it) so the chat's next turns keep it. A save that fails is logged: the next turn then starts
   * on the default version and saves again.
   */
  private async saveEnginePin(
    request: SubmitDurableRun,
    decided: PolicyContext,
    admission: RunAdmissionResult,
  ): Promise<void> {
    const engine = decided.enginePin;
    const save = this.codingEngine?.saveSessionPin;
    if (engine === undefined || save === undefined || !admission.created) {
      return;
    }
    const sessionId = admission.run.sessionId;
    try {
      await save.call(this.codingEngine, request.orgId, sessionId, engine);
    } catch (error) {
      this.logger.warn(
        'run.engine_version_not_saved',
        harnessLogMeta('run.engine_version_not_saved', {
          orgId: request.orgId,
          sessionId,
          runId: admission.run.runId,
          engine,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /**
   * A queued next run (SS2) decided again when it drains, as a fresh run of
   * its sender's would be (R2 F6), with `authorization` the sender's own
   * credential: the command's policy fields are replaced, everything else
   * stays as queued. The run it answers is read as a fresh answer's is: the
   * one it named when queued, else the chat's last run if that ended asking.
   * Throws the refusal a fresh admission would.
   */
  async readmit(
    command: AdmitRunCommand,
    authorization: string,
  ): Promise<AdmitRunCommand> {
    const decided = policyContext(
      {
        orgId: command.orgId,
        actorId: command.actorId,
        ...(command.agentKey !== undefined
          ? { agentKey: command.agentKey }
          : {}),
        ...(command.sessionId !== null ? { sessionId: command.sessionId } : {}),
        idempotencyKey: command.idempotencyKey,
        deadlineMs: command.absoluteDeadlineAt - this.clock.now(),
        input: command.input,
        authorization,
        via: command.via,
        ...(command.trigger !== undefined ? { trigger: command.trigger } : {}),
        // The gates the run was admitted with (its turn's, its agent's, the run it answers): drained, it keeps them.
        ...(command.gates !== undefined ? { gates: command.gates } : {}),
      },
      {},
      true,
    );
    // Read now, not kept from the queue: the run it waited behind may since have ended on a question (WP-64).
    const answered = await this.answeredRun({
      orgId: command.orgId,
      ...(command.sessionId !== null ? { sessionId: command.sessionId } : {}),
      ...(command.answered !== undefined
        ? { answers: command.answered.runId }
        : {}),
    });
    if (answered !== null) decided.answered = answered;
    try {
      await this.policy.run(decided);
    } catch (error) {
      throw error instanceof AdmissionRefusal ? error.error : error;
    }
    const readmitted = structuredClone(command);
    recordDecisions(readmitted, decided);
    return readmitted;
  }

  /**
   * The store admits as the session's owner, and answers anyone else "not
   * found". A collaborator in an org that allows their turns is admitted as
   * themselves (`true`); any other participant is told why not, with their
   * role's reason code (nothing was written); a stranger stays not found
   * (null). The owner's admission never pays for the role read.
   */
  private async asCollaborator(
    request: SubmitDurableRun,
    error: unknown,
    executor: ExecutorKind,
  ): Promise<true | AdmissionRefusal | null> {
    if (
      !(error instanceof HarnessNotFoundError) ||
      request.sessionId === undefined ||
      this.roles === undefined
    ) {
      return null;
    }
    const role = await roleIn(this.roles, request, request.sessionId);
    const allowed =
      role === 'collaborator' && (await this.collaboratorTurnsOn(request));
    return refusedRole(role, executor, allowed) ?? (allowed ? true : null);
  }

  /**
   * WP-64: whether a message in this session would answer a gated run's question. A chat route that serves a turn on
   * today's loop without admitting it (a body the harness cannot take, admission unavailable) asks first: that loop
   * has no gates. A failed read throws.
   */
  async answersGatedQuestion(scope: SessionExecutionScope): Promise<boolean> {
    if (!(await this.mayAnswerIn(scope))) return false;
    const answered = await this.store.answeredRun(scope.orgId, scope.sessionId);
    return answered?.gates !== undefined;
  }

  /**
   * SEC-C-12: only the session's owner or a collaborator (OWD-19) may answer its questions, so only they learn one is
   * open. Anyone else reads exactly as a session that does not exist. Without a role lookup every caller is checked
   * (fail closed): an owner's answer to a gated question must never reach today's loop.
   */
  private async mayAnswerIn(scope: SessionExecutionScope): Promise<boolean> {
    if (this.roles === undefined) return true;
    const role = await this.roles.roleOf(scope);
    return role === 'owner' || role === 'collaborator';
  }

  /**
   * WP-64: the run this turn answers, or null. Read before the policy steps, which pin its spending cap. A read that
   * fails refuses the turn rather than admit an answer without its question's gates; a run the inbox names that this
   * org's session does not hold is not found.
   */
  private async answeredRun(
    request: Pick<
      SubmitDurableRun,
      'orgId' | 'sessionId' | 'steerOnly' | 'answers'
    > &
      Partial<Pick<SubmitDurableRun, 'actorId' | 'agentKey'>>,
  ): Promise<AnsweredRun | null> {
    if (request.sessionId === undefined || request.steerOnly === true) {
      if (request.answers !== undefined) {
        throw new AdmissionRefusal('answers', new HarnessNotFoundError());
      }
      return null;
    }
    let found: AnsweredRun | null;
    try {
      found = await this.store.answeredRun(
        request.orgId,
        request.sessionId,
        request.answers,
      );
      // SEC-C-12: read only when a question is open, so the owner's ordinary turn never pays for the role read.
      if (
        found !== null &&
        request.actorId !== undefined &&
        !(await this.mayAnswerIn({
          orgId: request.orgId,
          actorId: request.actorId,
          sessionId: request.sessionId,
          ...(request.agentKey !== undefined
            ? { agentKey: request.agentKey }
            : {}),
        }))
      ) {
        found = null;
      }
    } catch (error) {
      this.logger.warn(
        'run.answered_run_unreadable',
        harnessLogMeta('run.answered_run_unreadable', {
          orgId: request.orgId,
          sessionId: request.sessionId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      throw new AdmissionRefusal(
        'unavailable',
        new HarnessRunConfigUnavailableError(
          'Whether this message answers a question could not be checked; try again',
        ),
      );
    }
    if (found === null && request.answers !== undefined) {
      throw new AdmissionRefusal('answers', new HarnessNotFoundError());
    }
    return found;
  }

  /**
   * The other participants' finished turns whose label passes for the sender
   * (P2.5a), decided now with the sender's own credential and never
   * remembered: the run pins them, the next admission asks again. Read before
   * the store's transaction (the check is a call to the record authority); a
   * turn that ends after this read is simply not among them. Any failure
   * lists none.
   */
  private async visibleRuns(request: SubmitDurableRun): Promise<string[]> {
    if (
      request.sessionId === undefined ||
      request.steerOnly === true ||
      this.turnLabels === undefined ||
      this.store.labelledTurns === undefined
    ) {
      return [];
    }
    let candidates: LabelledTurn[];
    try {
      candidates = await this.store.labelledTurns(
        request.orgId,
        request.sessionId,
        request.actorId,
      );
    } catch (error) {
      this.logger.warn(
        'turn_labels.read_failed',
        harnessLogMeta('turn_labels.read_failed', {
          orgId: request.orgId,
          sessionId: request.sessionId,
          actorId: request.actorId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'stub_turns',
        }),
      );
      return [];
    }
    if (candidates.length === 0) return [];
    const passed = await this.turnLabels.passingTurns(
      {
        authorization: request.authorization,
        orgId: request.orgId,
        sessionId: request.sessionId,
        surface: 'admission',
      },
      candidates,
    );
    return [...passed].sort();
  }

  /** An unreadable setting reads as off: a collaborator is refused, never let through unchecked. */
  private async collaboratorTurnsOn(
    request: SubmitDurableRun,
  ): Promise<boolean> {
    if (this.collaboratorTurns === undefined) return false;
    try {
      return await this.collaboratorTurns.enabled(request.orgId);
    } catch {
      return false;
    }
  }

  /**
   * `admitChecked`, re-pinned once when the org head or the deployment's
   * revision moved after the pin (02-lld §6.3): a control-plane write that
   * commits first is the one the run sees. One that moves again is an outage
   * the caller retries.
   */
  private async admitPinned(
    request: SubmitDurableRun,
    command: AdmitRunCommand,
    now: number,
    decided: PolicyContext,
  ): Promise<RunAdmissionResult> {
    try {
      return await this.admitChecked(request, command, now);
    } catch (error) {
      if (!(error instanceof PinStale) || this.repin === undefined) throw error;
    }
    await this.repin(decided);
    // The re-pin decides again what the run holds and whether a worker reads it.
    const repinned = { ...command };
    recordDecisions(repinned, decided);
    try {
      return await this.admitChecked(request, repinned, now);
    } catch (again) {
      if (!(again instanceof PinStale)) throw again;
      throw new AdmissionRefusal(
        'unavailable',
        new HarnessRunConfigUnavailableError(
          'The organization changed its configuration during admission; try again',
        ),
      );
    }
  }

  /** `admitOnce`, then the question a new turn answers is told gone from Needs you. */
  private async admitChecked(
    request: SubmitDurableRun,
    admitted: AdmitRunCommand,
    now: number,
  ): Promise<RunAdmissionResult> {
    const result = await this.admitOnce(request, admitted, now);
    if (result.created && admitted.answered !== undefined) {
      try {
        this.questionAnswered?.(admitted.orgId, admitted.answered.runId);
      } catch {
        // A push is a hint: the person's next read counts without the question.
      }
    }
    return result;
  }

  /**
   * `store.admit`, with a steering message's references authorized when the
   * session has a running task: as the caller's own access, never the
   * client's ids. A refused reference is an `AdmissionRefusal`; anything else
   * the store throws is passed on.
   */
  private async admitOnce(
    request: SubmitDurableRun,
    admitted: AdmitRunCommand,
    now: number,
  ): Promise<RunAdmissionResult> {
    const command = await this.withQuotaCaps(admitted);
    try {
      return await this.store.admit(command, now);
    } catch (error) {
      if (!(error instanceof SteeringReferencesUnchecked)) throw error;
    }
    try {
      await this.authorizeReferences(request);
    } catch (denied) {
      throw refusal(denied, 'references');
    }
    try {
      return await this.store.admit(
        { ...command, referencesAuthorized: true },
        now,
      );
    } catch (retried) {
      throw refusal(retried, 'unavailable');
    }
  }

  /**
   * C4.10 `quotas.*`: the caps the run's pin resolved, or for a run without
   * one the org's stored layers; read before the store's transaction.
   */
  private async withQuotaCaps(
    command: AdmitRunCommand,
  ): Promise<AdmitRunCommand> {
    const caps =
      command.resolved !== undefined
        ? quotaCapsOfPin(command.resolved)
        : await this.quotaCaps?.caps(command.orgId);
    return caps === undefined ||
      (caps.activeRunsPerOrg === undefined &&
        caps.activeRunsPerUser === undefined)
      ? command
      : { ...command, quotaCaps: caps };
  }

  private command(
    request: SubmitDurableRun,
    rollout: HarnessRolloutDecision,
    now: number,
  ): AdmitRunCommand {
    const canonicalPayload = {
      agentKey: request.agentKey ?? null,
      sessionId: request.sessionId ?? null,
      input: request.input,
      deadlineMs: request.deadlineMs,
      ...(request.regenerate !== undefined
        ? { regenerate: request.regenerate.messageId }
        : {}),
    };
    const prefix = canonicalSha256({
      orgId: request.orgId,
      actorId: request.actorId,
      idempotencyKey: request.idempotencyKey,
      payload: canonicalPayload,
    }).replace('sha256:', '');
    return {
      orgId: request.orgId,
      actorId: request.actorId,
      ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
      sessionId: request.sessionId ?? null,
      idempotencyKey: request.idempotencyKey,
      payloadHash: canonicalSha256(canonicalPayload),
      runId: this.newId(),
      dispatchCommandId: `dispatch:${prefix}`,
      absoluteDeadlineAt: now + request.deadlineMs,
      input: structuredClone(request.input),
      harnessMajor: rollout.harnessMajor,
      via: request.via ?? 'session',
      attribution: runAttribution({
        actorId: request.actorId,
        principalKind: request.principalKind ?? 'user',
        ...(request.agentKey !== undefined
          ? { agentKey: request.agentKey }
          : {}),
        ...(request.trigger !== undefined ? { trigger: request.trigger } : {}),
      }),
      surface: request.surface ?? 'web',
      ...(request.steerOnly === true ? { steerOnly: true } : {}),
      ...(request.regenerate !== undefined
        ? { regenerate: structuredClone(request.regenerate) }
        : {}),
      ...(request.trigger !== undefined
        ? { trigger: structuredClone(request.trigger) }
        : {}),
    };
  }

  /**
   * Every record the message names must be readable by the caller now, per the
   * record authorization that serves their content, with the caller's own
   * credential. An attached file the caller may not read does not refuse the
   * message (R36-COVER, ADR-RUN-03 D5): a new turn goes on with such a file and
   * its answer names it, and the worker checks a steered message's files again
   * when it reads them and names one it was not given; refusing here lost the
   * person's words and read as "may not use this agent".
   */
  private async authorizeReferences(request: SubmitDurableRun): Promise<void> {
    if (this.references === undefined) {
      throw new HarnessUnavailableError(
        'Attachments cannot be verified for a running task',
      );
    }
    if (!request.authorization) throw new HarnessForbiddenError();
    const sources = steeringReferences(request.input);
    let decided: ReadonlyMap<string, SourceStatus>;
    try {
      decided = await this.references.decide({
        authorization: request.authorization,
        sources,
      });
    } catch (error) {
      if (
        error instanceof RecordAccessUnavailableError &&
        error.reason === 'refused'
      ) {
        throw new HarnessForbiddenError(UNREADABLE_RECORD);
      }
      throw new HarnessUnavailableError('Attachments could not be verified');
    }
    if (
      sources.some((source) => {
        const status = decided.get(sourceKey(source));
        // An unanswered source fails closed, a file as much as a record.
        return source.kind === 'attachment'
          ? status === undefined
          : status !== 'readable';
      })
    ) {
      throw new HarnessForbiddenError(UNREADABLE_RECORD);
    }
  }
}

/** Why a steered message naming a record the caller may not read is refused. */
const UNREADABLE_RECORD = "A record this message names isn't available to you";

function withoutPastedSecrets(request: SubmitDurableRun): SubmitDurableRun {
  const redacted = redactPastedSecrets(request.input.query);
  if (redacted.kinds.length === 0) return request;
  return { ...request, input: { ...request.input, query: redacted.text } };
}

/**
 * An answer to a gated run never goes to today's loop, which has no gates (WP-64): the turn is refused in plain words
 * (`GATES_NEED_RUNTIME`), rather than act for real on a dry run's question.
 */
function assertUngatedForLegacy(answered: AnsweredRun | null): void {
  if (answered?.gates === undefined) return;
  throw new AdmissionRefusal('answer_gates', gatesNeedRuntime());
}
