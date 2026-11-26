import { coveringControls } from '../domain/operator-controls';
import type { OperatorStops } from '../ports/operator-stops';
import type { SignInContext } from '../domain/engine-signin';
import { performance } from 'perf_hooks';
import { PinStale } from '../domain/admission';
import { canonicalSha256 } from '../contracts/canonical-json';
import type { TurnLabel } from '../domain/turn-labels';
import type { PipeshubRunSpecExtension } from '../contracts/generated/ext/pipeshub';
import {
  childAccessRefusal,
  ChildExecutorRequest,
  childExecutor,
  ChildGrantRequest,
  McpFirstUsePins,
  ChildGrants,
  childAdmittedEvent,
  childGrantDigest,
  childGrants,
  childNeedsAccess,
  childRefused,
  childRunIdFor,
  childRunSpec,
  childSpawnedEvent,
  ChildWorkspacePin,
  CHILD_MAX_DEPTH,
  parentCanAsk,
  pipeshub,
  SubAgentDefinition,
} from '../domain/child-runs';
import { CHILD_ASKS_FEATURE } from '../domain/worker-compat';
import type { WorkerFeatureAvailability } from './worker-feature-availability';
import { RunSpec } from '../contracts/generated/core/run-spec';
import { childForkOf, principalWorkspaceId } from '../domain/workspace';
import { WorkspaceService } from './workspace-service';
import { pipeshubRunSpecExtensionSchema } from '../contracts/generated/ext/pipeshub/run-spec';
import type { GroupAccess } from '../ports/group-access';
import type { EngineEligibility } from './engine-eligibility';
import {
  HarnessConflictError,
  HarnessGroupAccessRefusedError,
  HarnessModelAccessRefusedError,
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../domain/errors';
import { AccessSubject, ModelAccessDecision } from './model-access-service';
import {
  AttemptIdentity,
  attemptOwnsRun,
  HarnessRun,
  nodeAppend,
  RunInputSnapshot,
  RunTaskParts,
  runNoticeEvent,
  runSpecPinnedEvent,
} from '../domain/execution';
import { readPinnedSpec, RunExecutor, SpecSubject } from '../domain/run-spec';
import type { ChildPinning, RoleModel } from './admission/child-pinning';
import { selectLlm } from '../../control-plane';
import type { AgentRunDefinition } from '../domain/agent-definition';
import { childModelOf, roleRoutesOf } from '../domain/child-model';
import { codeTaskAuthority, modelRouteOf } from '../domain/pin-shadow';
import type { ConfiguredLlmSource } from '../ports/pin-sources';
import {
  acpOptInOf,
  engineExecutor,
  engineRef,
  PinnedEngineProfile,
  resolveEngineProfile,
} from '../domain/engine-profiles';
import { engineImageDigests, EngineImageSource } from './engine-image-pin';
import { EngineProfileCeilings } from '../ports/engine-profiles';
import { childCap, pinnedCap } from '../domain/budget-ledger';
import { budgetChain } from './budget-ledger-service';
import { ExecutionStore } from '../ports/execution-store';
import {
  AdmitChildCommand,
  ChildAdmission,
  ChildRunStore,
} from '../ports/child-runs';
import { JournalService } from './journal-service';
import type { EngineCarry } from './engine-carry';
import { snapshotTtlDays } from '../domain/snapshot-ttl';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from './harness-log';

/** Whose engine authority a loop parent's engine child draws on: its org and its own agent's narrowing. */
export interface ChildEngineParent {
  orgId: string;
  /** The parent agent's `engineProfile` layer as admitted, unparsed; absent when it sets none. */
  agent?: unknown;
}

/**
 * The engine profile a loop parent may grant an engine child (E1): the release
 * default under the org ceiling, read when a child asks, and under the parent
 * agent's own layer. Null when the deployment has no engine profiles, the
 * ceiling cannot be read or a layer refuses: the child is then refused, never
 * run on defaults.
 */
export const childEngineAuthority =
  (ceilings: EngineProfileCeilings | null) =>
  async (parent: ChildEngineParent): Promise<PinnedEngineProfile | null> => {
    if (ceilings === null) return null;
    try {
      const ceiling = await ceilings.read(parent.orgId);
      return resolveEngineProfile(
        { org: ceiling, agent: parent.agent },
        undefined,
        acpOptInOf(ceilings),
      ).profile;
    } catch {
      return null;
    }
  };

/** What a parent's worker asks for, from one tool invocation. */
export interface SpawnChild {
  invocationId: string;
  taskId?: string;
  /** The child's task, its input query. */
  goal: string;
  grants: ChildGrantRequest;
  /** What the child runs as (K-L), pinned in its spec. */
  subAgent?: SubAgentDefinition;
  /** An engine child (E1); absent: the loop. */
  executor?: ChildExecutorRequest;
  /** The parent's label when it asked (UX2), pinned as the child's `x.pipeshub.inheritedLabel`. */
  inheritedLabel?: TurnLabel;
  /** The conversation grants the parent held (FLOWASK), pinned as the child's `x.pipeshub.inheritedGrants`. */
  inheritedGrants?: PipeshubRunSpecExtension['inheritedGrants'];
  /** The child's task in its parts (BGUI-10): kept on its input for its brief, outside the request's identity. */
  task?: RunTaskParts;
  /**
   * The first-use pins of the MCP servers the parent has listed (R31HOT2, ADR-OWD-07 D-LAZY-5): the child is pinned
   * to them, narrowed to what it holds. Context, outside the request's identity, like the label.
   */
  mcpPins?: McpFirstUsePins;
}

export interface SpawnedChild {
  childRunId: string;
  created: boolean;
}

/** Fallback when the parent's input was erased; a child of an erased session is refused later anyway. */
const NO_MODEL: RunInputSnapshot['model'] = { chatMode: 'auto' };

export interface ChildRunServiceOptions {
  runs: Pick<ExecutionStore, 'get' | 'readClaimSubject'>;
  children: ChildRunStore;
  journals: JournalService;
  logger?: HarnessLogger;
  /** Absent: no child gets a workspace fork (P6.4). */
  workspaces?: Pick<WorkspaceService, 'forkForChild'> | null;
  /** `childEngineAuthority`; absent: a loop parent has no engine children. */
  engineAuthority?: (
    parent: ChildEngineParent,
  ) => Promise<PinnedEngineProfile | null>;
  /**
   * Model access as admission decides it (A1), for an engine child of a loop
   * parent; absent: such a child is refused unavailable, never pinned unchecked.
   */
  engineAccess?:
    ((subject: AccessSubject) => Promise<ModelAccessDecision | null>) | null;
  /** The control plane's pin for a child (CPD-5); absent: `CONTROL_PLANE_PIN=off`. */
  pinning?: ChildPinning | null;
  /**
   * Whether the parent's conversation is shared (SIGNIN): read only when the
   * actor chose their own plan. Absent: read as shared, so no plan is used.
   */
  sessionShared?:
    ((orgId: string, sessionId: string) => Promise<boolean>) | null;
  /**
   * The deployment's configured models, read only for a spawn whose role sets
   * a model and that no pin decides (MA1): a role's model that is no longer
   * configured refuses the spawn. Absent: not checked here.
   */
  configuredLlms?: ConfiguredLlmSource | null;
  /** `engineCarry`: what a code_task child starts from (LCFIX); absent: every one starts fresh. */
  engineCarry?:
    | ((
        parent: HarnessRun,
        engineKind: string,
        snapshotTtlDays: number,
      ) => Promise<EngineCarry | null>)
    | null;
  /** The broker's engine image digests an engine child pins (C2.20); absent: none pinned. */
  engineImages?: EngineImageSource | null;
  /**
   * G-41: the org's model groups, for a sub-agent role's model. A role model
   * the actor's groups refuse falls back to the run's own model if they allow
   * that, else the spawn is refused. Absent: no model is limited to groups.
   */
  groupAccess?:
    | (Pick<GroupAccess, 'admits'> &
        Partial<Pick<GroupAccess, 'allowedModelNames'>>)
    | null;
  /** ADR-OPS-01: the operator controls a child is held to as every admission is; absent: nothing is stopped. */
  operatorControls?: Pick<OperatorStops, 'controls'> | null;
  /**
   * WP-111: whether the engine child's engine is still on for the org and the actor's groups when its spec is
   * pinned: a turn admitted before an admin turned the engine off starts no new engine session. Absent: not checked.
   */
  engineEligibility?: Pick<EngineEligibility, 'check'> | null;
  /**
   * Which features a live worker advertises: a child pins whether its parent can ask (`child_asks`) only while one
   * reads the pin, so a deploy that upgrades Node first admits it as before. Absent: pinned.
   */
  workerFeatures?: Pick<WorkerFeatureAvailability, 'live' | 'metrics'> | null;
}

/** The definition's entry for a role's model, with the provider as the definition wrote it. */
interface AskedModel extends RoleModel {
  definedProvider: string;
}

/**
 * Child admission (P6.1, P6.3). Everything the transaction writes is derived
 * here from the parent's pinned spec, which never changes: the child's grant
 * (refused when it widens, never narrowed), its spec, both streams' events.
 * The store re-checks the parent's attempt and bounds inside the transaction.
 */
export class ChildRunService {
  private readonly runs: Pick<ExecutionStore, 'get' | 'readClaimSubject'>;
  private readonly children: ChildRunStore;
  private readonly journals: JournalService;
  private readonly logger: HarnessLogger;
  private readonly workspaces: Pick<WorkspaceService, 'forkForChild'> | null;
  private readonly engineAuthority: (
    parent: ChildEngineParent,
  ) => Promise<PinnedEngineProfile | null>;
  private readonly engineAccess:
    ((subject: AccessSubject) => Promise<ModelAccessDecision | null>) | null;
  private readonly pinning: ChildPinning | null;
  private readonly configuredLlms: ConfiguredLlmSource | null;
  private readonly engineCarry:
    | ((
        parent: HarnessRun,
        engineKind: string,
        snapshotTtlDays: number,
      ) => Promise<EngineCarry | null>)
    | null;
  private readonly sessionShared:
    ((orgId: string, sessionId: string) => Promise<boolean>) | null;
  private readonly engineImages: EngineImageSource | null;
  private readonly groupAccess:
    | (Pick<GroupAccess, 'admits'> &
        Partial<Pick<GroupAccess, 'allowedModelNames'>>)
    | null;
  private readonly operatorControls: Pick<OperatorStops, 'controls'> | null;
  private readonly engineEligibility: Pick<EngineEligibility, 'check'> | null;
  private readonly workerFeatures: Pick<
    WorkerFeatureAvailability,
    'live' | 'metrics'
  > | null;

  constructor(options: ChildRunServiceOptions) {
    this.runs = options.runs;
    this.children = options.children;
    this.journals = options.journals;
    this.logger = options.logger ?? silentHarnessLogger;
    this.workspaces = options.workspaces ?? null;
    this.engineAuthority =
      options.engineAuthority ?? (() => Promise.resolve(null));
    this.engineAccess = options.engineAccess ?? null;
    this.pinning = options.pinning ?? null;
    this.configuredLlms = options.configuredLlms ?? null;
    this.engineCarry = options.engineCarry ?? null;
    this.sessionShared = options.sessionShared ?? null;
    this.engineImages = options.engineImages ?? null;
    this.groupAccess = options.groupAccess ?? null;
    this.operatorControls = options.operatorControls ?? null;
    this.engineEligibility = options.engineEligibility ?? null;
    this.workerFeatures = options.workerFeatures ?? null;
  }

  /**
   * ANSWER-CONTRACT-FU-3: whether the child's parent can ask a person, pinned on the child so its worker offers
   * `ask_user` only then; `undefined` (not pinned) while no live worker reads it.
   */
  private async parentAsks(parentSpec: RunSpec): Promise<boolean | undefined> {
    if (
      this.workerFeatures !== null &&
      !(await this.workerFeatures.live(CHILD_ASKS_FEATURE))
    ) {
      this.workerFeatures.metrics.withheld(CHILD_ASKS_FEATURE);
      return undefined;
    }
    return parentCanAsk(parentSpec);
  }

  /**
   * A child whose pin its admission finds stale (a control-plane write, or an
   * AI-model save, committed after the pin was resolved) is spawned again
   * once on a refreshed snapshot: the spawn is idempotent on the child's run
   * id and nothing of the first try was written. Revisions that move again
   * are an outage the worker retries.
   */
  /**
   * ADR-OPS-01: a child under any control that covers it is refused, as a
   * fresh admission would be: its conversation, agent or org, the instance, or
   * (an engine child) its engine version or the sandboxes' egress.
   */
  private async refuseStopped(
    parent: HarnessRun,
    executor: RunExecutor,
    fields: Record<string, unknown>,
  ): Promise<void> {
    if (this.operatorControls === null) return;
    const engine =
      executor.kind === 'engine'
        ? { kind: executor.engine.kind, version: executor.engine.version }
        : null;
    const covering = coveringControls(
      {
        orgId: parent.orgId,
        sessionId: parent.sessionId,
        agentKey: parent.agentKey ?? null,
        engine,
      },
      await this.operatorControls.controls(),
    );
    const stop = covering[0];
    if (stop === undefined) return;
    this.logger.info(
      'child.refused',
      harnessLogMeta('child.refused', {
        ...fields,
        reason: 'CHILD_OPERATOR_STOPPED',
        scope: stop.scope.kind,
      }),
    );
    throw childRefused(
      'CHILD_OPERATOR_STOPPED',
      'An operator control stops this work for now',
    );
  }

  /**
   * WP-111: an engine child is pinned only while its engine is on. The parent's authority was pinned at its turn's
   * admission; an admin's switch since then is a control, so the hand-off is refused (`ENGINE_OFF`) and the loop
   * answers itself. Limits and other dimensions stay as the parent pinned them (CTL-6).
   */
  private async refuseIneligible(
    parent: HarnessRun,
    executor: RunExecutor,
    fields: Record<string, unknown>,
  ): Promise<void> {
    const eligibility = this.engineEligibility;
    if (executor.kind !== 'engine' || eligibility === null) return;
    const engine = {
      kind: executor.engine.kind,
      version: executor.engine.version,
    };
    const off = await eligibility.check(parent.orgId, parent.actorId, engine);
    if (off === null) return;
    const code =
      off === 'engine_off' || off === 'engine_group_required'
        ? 'ENGINE_OFF'
        : 'CHILD_OPERATOR_STOPPED';
    this.logger.info(
      'child.refused',
      harnessLogMeta('child.refused', {
        ...fields,
        reason: code,
        engine: engineRef(engine),
        because: off,
      }),
    );
    throw code === 'ENGINE_OFF'
      ? new HarnessConflictError(
          'ENGINE_OFF',
          'The coding engine this hand-off needs is turned off now',
        )
      : childRefused(
          'CHILD_OPERATOR_STOPPED',
          'An operator control stops this work for now',
        );
  }

  async spawn(
    attempt: AttemptIdentity & { orgId: string; runId: string },
    request: SpawnChild,
  ): Promise<SpawnedChild> {
    try {
      return await this.spawnOnce(attempt, request);
    } catch (error) {
      if (!(error instanceof PinStale) || this.pinning === null) throw error;
      this.pinning.refresh(attempt.orgId);
    }
    try {
      return await this.spawnOnce(attempt, request);
    } catch (again) {
      if (!(again instanceof PinStale)) throw again;
      throw new HarnessUnavailableError(
        'The organization changed its configuration while the child run was admitted; try again',
      );
    }
  }

  private async spawnOnce(
    attempt: AttemptIdentity & { orgId: string; runId: string },
    request: SpawnChild,
  ): Promise<SpawnedChild> {
    const started = performance.now();
    const subject = await this.runs.readClaimSubject(
      attempt.orgId,
      attempt.runId,
    );
    if (subject === null) throw new HarnessNotFoundError();
    const parent = subject.run;
    const fields = {
      ...runLogFields(parent),
      attemptId: attempt.attemptId,
      fence: attempt.fence,
      invocationId: request.invocationId,
    };
    try {
      if (!attemptOwnsRun(parent, attempt)) {
        throw new HarnessConflictError(
          'STALE_ATTEMPT',
          'Worker no longer owns this run',
        );
      }
      if (parent.harnessMajor !== 2 || subject.pinned === null) {
        throw childRefused(
          'CHILDREN_NOT_GRANTED',
          'The run has no pinned spec',
        );
      }
      const parentSpec = readPinnedSpec(subject.pinned.spec);
      if (parentSpec.budgets.maxChildren <= 0) {
        throw childRefused(
          'CHILDREN_NOT_GRANTED',
          'The run is not granted child runs',
        );
      }
      const depth = (parent.parent?.depth ?? 0) + 1;
      const decision = childGrants(
        parentSpec,
        request.grants,
        depth,
        request.mcpPins,
      );
      if (decision.refusal !== null) {
        this.logger.warn(
          'child.refused',
          harnessLogMeta('child.refused', {
            ...fields,
            reason: decision.refusal.code,
            dimension: decision.refusal.dimension,
            items: decision.refusal.items.slice(0, 20),
            depth,
          }),
        );
        throw childRefused(
          decision.refusal.code,
          `The child asks for more ${decision.refusal.dimension} than its parent holds`,
        );
      }
      const model = await this.roleModelInGroups(
        subject.run,
        parentSpec,
        await this.roleModel(subject, request, fields, depth),
        fields,
      );
      const executor = childExecutor(
        parentSpec,
        request.executor,
        request.executor !== undefined && parentSpec.executor?.kind !== 'engine'
          ? // A loop turn admitted to hand off holds its authority pinned (CTL-6): never read again mid-turn.
            (codeTaskAuthority(parentSpec) ??
              (await this.loopParentAuthority(subject)))
          : null,
      );
      if (executor.refusal !== null) {
        this.logger.warn(
          'child.refused',
          harnessLogMeta('child.refused', {
            ...fields,
            reason: executor.refusal.code,
            dimension: executor.refusal.dimension,
            items: executor.refusal.items.slice(0, 20),
            depth,
          }),
        );
        throw childRefused(
          executor.refusal.code,
          'The child asks for an engine its parent does not hold',
        );
      }
      const childRunId = childRunIdFor(
        parent.orgId,
        parent.runId,
        request.invocationId,
      );
      const workspace = await this.childWorkspace(
        attempt,
        parent,
        parentSpec,
        decision.grants,
        childRunId,
      );
      // A role's route is not one of its parent's, so no access pinned there reaches it.
      const needsAccess =
        model !== null
          ? executor.executor.kind === 'engine'
          : childNeedsAccess(parentSpec, decision.grants, executor.executor);
      const access = needsAccess
        ? await this.childEngineAccess(
            parent,
            executor.executor,
            decision.grants,
            fields,
            depth,
            model,
            // CTL-6: a code_task child chooses its account as admission chose a coder agent's run (AG1).
            codeTaskAuthority(parentSpec) !== null
              ? await this.rootDefinition(subject)
              : null,
            // SIGNIN: where the turn came from, for a person's own plan (read only if they chose it).
            () => this.signInContext(parent, subject.via, parentSpec),
          )
        : undefined;
      const modelAccess = access?.access;
      // An engine child of a loop parent holds its profile narrowed to the account kind it was admitted on (OI-CPD3-1).
      const childExecutorPinned =
        access !== undefined
          ? engineExecutor(access.engineProfile)
          : executor.executor;
      await this.refuseStopped(parent, childExecutorPinned, fields);
      await this.refuseIneligible(parent, childExecutorPinned, fields);
      const childPin = await this.pinning?.pin({
        parent,
        parentResolved: subject.resolved ?? null,
        grants: decision.grants,
        ...(request.executor !== undefined
          ? { executor: request.executor }
          : {}),
        ...(modelAccess !== undefined
          ? { accountKind: modelAccess.access.accountKind }
          : {}),
        ...(parentSpec.executor?.kind !== 'engine'
          ? { loopParent: { agentDefinition: subject.agentDefinition ?? null } }
          : {}),
        ...(request.subAgent !== undefined
          ? { subAgent: request.subAgent }
          : {}),
        ...(model !== null ? { model } : {}),
      });
      const carry =
        childExecutorPinned.kind === 'engine' &&
        codeTaskAuthority(parentSpec) !== null &&
        this.engineCarry !== null
          ? await this.engineCarry(
              parent,
              childExecutorPinned.engine.kind,
              snapshotTtlDays(
                childPin?.resolved.effectiveConfig ??
                  subject.resolved?.effectiveConfig,
              ),
            )
          : null;
      const imageDigest =
        childExecutorPinned.kind === 'engine' &&
        pipeshub(parentSpec)?.release !== undefined
          ? (
              await engineImageDigests(this.engineImages, this.logger, fields)
            )?.get(engineRef(childExecutorPinned.engine))
          : undefined;
      const pinned = childRunSpec(
        { ...subject.pinned, spec: parentSpec },
        decision.grants,
        request.subAgent,
        workspace ?? undefined,
        childExecutorPinned,
        modelAccess,
        request.inheritedLabel,
        childPin?.readByWorker === true ? childPin.resolved : undefined,
        model !== null
          ? {
              provider: model.provider,
              model: model.model,
              // The entry the pin chose is the one the worker builds; without a pin, the definition's.
              modelKey: childPin?.resolved.llm?.modelKey ?? model.modelKey,
            }
          : undefined,
        carry ?? undefined,
        request.inheritedGrants,
        imageDigest,
        await this.parentAsks(parentSpec),
        request.mcpPins,
      );
      const grantDigest = childGrantDigest(pinned.spec);
      const childJournal = await this.journals.prepare({
        orgId: parent.orgId,
        sessionId: parent.sessionId,
        streamId: childRunId,
        runId: childRunId,
        writer: 'node',
        events: [
          childAdmittedEvent(parent, subject.via),
          runSpecPinnedEvent(pinned),
        ],
      });
      const parentJournal = await this.journals.prepare(
        nodeAppend(parent, [
          childSpawnedEvent(
            childRunId,
            request.invocationId,
            grantDigest,
            request.taskId,
            model ?? undefined,
          ),
          // SIGNGAP: the person's own plan could not be carried here; the chat says the task ran on the org's access.
          ...(access?.notice !== undefined
            ? [runNoticeEvent(access.notice)]
            : []),
        ]),
      );
      const budget = {
        chain: await budgetChain(
          {
            pinned: async (orgId, runId) => {
              const found = await this.runs.readClaimSubject(orgId, runId);
              return found === null
                ? null
                : { run: found.run, pinned: found.pinned };
            },
          },
          parent,
          { ...subject.pinned, spec: parentSpec },
        ),
        cap: childCap(pinnedCap(parentSpec), pinnedCap(pinned.spec)),
      };
      const admitted = await this.admit({
        orgId: parent.orgId,
        parentRunId: parent.runId,
        attempt: {
          workerId: attempt.workerId,
          attemptId: attempt.attemptId,
          fence: attempt.fence,
        },
        childRunId,
        invocationId: request.invocationId,
        requestHash: canonicalSha256({
          invocationId: request.invocationId,
          taskId: request.taskId ?? null,
          goal: request.goal,
          grants: request.grants,
          ...(request.subAgent !== undefined
            ? { subAgent: request.subAgent }
            : {}),
        }),
        maxChildren: parentSpec.budgets.maxChildren,
        input: childInput(
          parent,
          request.goal,
          model,
          // CTL-6: the turn's own input artifacts go to the engine it hands off to, never to a worker's choice.
          childExecutorPinned.kind === 'engine' &&
            codeTaskAuthority(parentSpec) !== null,
          request.task,
        ),
        pinned,
        via: subject.via,
        childJournal,
        parentJournal,
        budget,
        ...(childPin !== undefined
          ? { resolved: childPin.resolved, resolvedAt: childPin.resolvedAt }
          : {}),
        ...(model !== null ? { roleModel: true } : {}),
      });
      this.logger.info(
        'child.admitted',
        harnessLogMeta('child.admitted', {
          ...fields,
          childRunId,
          created: admitted.created,
          depth,
          maxDepth: CHILD_MAX_DEPTH,
          tools: pinned.spec.grants.tools.length,
          grantDigest,
          specDigest: pinned.specDigest,
          ...(carry !== null ? { carriedFrom: carry.runId } : {}),
          ...(workspace !== null
            ? {
                workspaceId: workspace.workspaceId,
                forkOf: workspace.forkOf.workspaceId,
                forkRevision: workspace.forkOf.revision,
              }
            : {}),
          ms: elapsedMs(started),
        }),
      );
      if (access?.notice !== undefined && admitted.created) {
        this.logger.info(
          'child.signin_unavailable',
          harnessLogMeta('child.signin_unavailable', {
            ...fields,
            childRunId,
            engine:
              childExecutorPinned.kind === 'engine'
                ? childExecutorPinned.engine.kind
                : null,
            reason: 'no_live_worker',
          }),
        );
      }
      return { childRunId, created: admitted.created };
    } catch (error) {
      if (
        error instanceof HarnessConflictError &&
        error.code !== 'CHILD_GRANT_TOO_LARGE' &&
        error.code !== 'CHILD_DEPTH_EXCEEDED'
      ) {
        this.logger.warn(
          'child.refused',
          harnessLogMeta('child.refused', { ...fields, reason: error.code }),
        );
      }
      throw error;
    }
  }

  /**
   * The model the spawn's role sets (MA1 §3), from the root run's definition
   * as its admission stored it; null: the child calls its parent's, exactly
   * as before. A parent the control plane pinned must hold that model under
   * that role; without a pin, a model that is no longer configured refuses
   * the spawn here (the pin refuses it otherwise).
   */
  private async roleModel(
    subject: Pick<SpecSubject, 'run' | 'agentDefinition' | 'resolved'>,
    request: SpawnChild,
    fields: Record<string, unknown>,
    depth: number,
  ): Promise<AskedModel | null> {
    const definition = await this.rootDefinition(subject);
    const asked = childModelOf(roleRoutesOf(definition), request.subAgent);
    const entry =
      asked === null ? undefined : definition?.roleModels?.[asked.role];
    if (asked === null || entry === undefined) return null;
    const model: AskedModel = {
      ...asked,
      modelKey: entry.modelKey,
      definedProvider: entry.provider,
    };
    const resolved = subject.resolved ?? null;
    if (resolved !== null && this.pinning?.decides(resolved) === true) {
      this.pinning.assertRoleModel(subject.run, resolved, model);
      return model;
    }
    if (this.configuredLlms === null) return model;
    let configured: Awaited<ReturnType<ConfiguredLlmSource['read']>>;
    try {
      configured = await this.configuredLlms.read(subject.run.orgId);
    } catch {
      throw new HarnessUnavailableError(
        'The configured models could not be read',
      );
    }
    // No model configured at all: nothing can be named, and the worker says so (as the pinner reads it).
    if (
      configured.llms.length > 0 &&
      selectLlm(configured.llms, {
        modelKey: model.modelKey,
        modelName: model.model,
        provider: model.provider,
      }) === null
    ) {
      this.logger.warn(
        'child.refused',
        harnessLogMeta('child.refused', {
          ...fields,
          reason: 'CHILD_GRANT_TOO_LARGE',
          dimension: 'models.roles',
          depth,
        }),
      );
      throw childRefused(
        'CHILD_GRANT_TOO_LARGE',
        `The sub-agent role "${model.role}" is set to ${modelRouteOf(model)}, which is no longer configured`,
      );
    }
    return model;
  }

  /** G-41: whether the actor's groups may use this configured model, on the same live read as admission. */
  private async modelInGroups(
    run: HarnessRun,
    modelKey: string | undefined,
  ): Promise<boolean> {
    if (this.groupAccess === null) return true;
    try {
      return await this.groupAccess.admits(run.orgId, run.actorId, {
        kind: 'model',
        modelKey,
      });
    } catch {
      throw new HarnessUnavailableError(
        'Which models you may use could not be checked; try again',
      );
    }
  }

  /**
   * G-41: a sub-agent role's model held to the actor's
   * groups, on the same live read as admission. Refused: the child calls the
   * run's own model (null) if the groups allow that one, else the spawn is
   * refused MODEL_GROUP_REQUIRED; an unreadable policy refuses it.
   */
  private async roleModelInGroups(
    run: HarnessRun,
    parentSpec: RunSpec,
    model: AskedModel | null,
    fields: Record<string, unknown>,
  ): Promise<AskedModel | null> {
    if (model === null || this.groupAccess === null) return model;
    const admits = (modelKey: string | undefined): Promise<boolean> =>
      this.modelInGroups(run, modelKey);
    if (await admits(model.modelKey)) return model;
    const extension = pipeshubRunSpecExtensionSchema.safeParse(
      parentSpec.extensions?.['x.pipeshub'],
    );
    const own = extension.success ? extension.data.model?.modelKey : undefined;
    if (await admits(own)) {
      this.logger.info(
        'child.role_model_withheld',
        harnessLogMeta('child.role_model_withheld', {
          ...fields,
          role: model.role,
          reason: 'model_group_required',
        }),
      );
      return null;
    }
    throw new HarnessGroupAccessRefusedError('MODEL_GROUP_REQUIRED');
  }

  /** The definition the tree's root was admitted with; a child of a child reads up to it. Null when erased or absent. */
  private async rootDefinition(
    subject: Pick<SpecSubject, 'run' | 'agentDefinition'>,
  ): Promise<AgentRunDefinition | null> {
    let { run, agentDefinition } = subject;
    while (run.parent != null) {
      const above = await this.runs.readClaimSubject(
        run.orgId,
        run.parent.runId,
      );
      if (above === null) return null;
      ({ run, agentDefinition } = above);
    }
    return agentDefinition ?? null;
  }

  /**
   * The engine profile a loop parent may grant an engine child: its pin's,
   * when the control plane pinned it (CPD-5 `on`); otherwise today's read of
   * the org ceiling under the parent agent's layer.
   */
  private async loopParentAuthority(
    subject: Pick<SpecSubject, 'run' | 'agentDefinition' | 'resolved'>,
  ): Promise<PinnedEngineProfile | null> {
    const resolved = subject.resolved ?? null;
    if (resolved !== null && this.pinning !== null) {
      return this.pinning.engineAuthority(resolved);
    }
    return this.engineAuthority({
      orgId: subject.run.orgId,
      agent: subject.agentDefinition?.engineProfile,
    });
  }

  /**
   * The access route of an engine child whose parent's routes pin none (A1):
   * decided as admission decides it for the parent's actor and model, under
   * the child's engine profile, then held to the ⊆ rule on `access`. A child
   * whose role sets its model (MA1) is decided for that model, reached as the
   * org configures it, and the route decided must be the role's.
   */
  private async childEngineAccess(
    parent: HarnessRun,
    executor: RunExecutor,
    grants: ChildGrants,
    fields: Record<string, unknown>,
    depth: number,
    model: AskedModel | null,
    definition: AgentRunDefinition | null = null,
    signIn?: () => Promise<SignInContext>,
  ): Promise<ModelAccessDecision> {
    const profile =
      executor.kind === 'engine' ? (executor.engine.profile ?? null) : null;
    if (
      this.engineAccess === null ||
      profile === null ||
      parent.input === null ||
      parent.input === undefined
    ) {
      throw new HarnessUnavailableError(
        'Model access for an engine child cannot be decided here',
      );
    }
    let decided: ModelAccessDecision | null;
    try {
      decided = await this.engineAccess({
        orgId: parent.orgId,
        actorId: parent.actorId,
        definition,
        model: inputModel(parent.input.model, model),
        engineProfile: profile,
        ...(signIn !== undefined ? { signIn } : {}),
      });
    } catch (error) {
      if (error instanceof HarnessModelAccessRefusedError) {
        this.logger.warn(
          'child.refused',
          harnessLogMeta('child.refused', {
            ...fields,
            reason: error.code,
            dimension: 'access',
            depth,
          }),
        );
      }
      throw error;
    }
    if (decided === null) {
      throw new HarnessUnavailableError('No model access for an engine child');
    }
    // SEC-C-02: the groups are held to the model access resolved, whichever account reaches it, not only to the
    // model the turn named when it was admitted.
    const resolvedKey = decided.access.model?.modelKey;
    if (
      resolvedKey !== undefined &&
      !(await this.modelInGroups(parent, resolvedKey))
    ) {
      this.logger.warn(
        'child.refused',
        harnessLogMeta('child.refused', {
          ...fields,
          reason: 'model_group_required',
          dimension: 'access',
          depth,
        }),
      );
      throw new HarnessGroupAccessRefusedError(
        'MODEL_GROUP_REQUIRED',
        await this.groupAccess
          ?.allowedModelNames?.(parent.orgId, parent.actorId)
          .catch(() => undefined),
      );
    }
    // A code_task child's access is decided as admission decided a coder agent's engine run: the turn's own model
    // and the agent's accounts, never routes its loop parent holds (CTL-6).
    const refusal =
      definition !== null
        ? null
        : childAccessRefusal(
            model !== null ? { access: [modelRouteOf(model)] } : grants,
            decided.access,
          );
    if (refusal !== null) {
      this.logger.warn(
        'child.refused',
        harnessLogMeta('child.refused', {
          ...fields,
          reason: refusal.code,
          dimension: refusal.dimension,
          items: refusal.items,
          depth,
        }),
      );
      throw childRefused(
        refusal.code,
        'The child asks for a model route its parent does not hold',
      );
    }
    return decided;
  }

  /** The parent turn's origin as SIGNIN judges it: how the actor came, the surface, whether the chat is shared. */
  private async signInContext(
    parent: HarnessRun,
    via: string,
    spec: RunSpec,
  ): Promise<SignInContext> {
    const surface = (
      spec.extensions?.['x.pipeshub'] as { surface?: string } | undefined
    )?.surface;
    let shared: boolean;
    try {
      shared =
        this.sessionShared === null
          ? true
          : await this.sessionShared(parent.orgId, parent.sessionId);
    } catch {
      shared = true;
    }
    return {
      via,
      ...(surface !== undefined ? { surface } : {}),
      // A child of a child is the same actor's (CH-1); a child spawned for anyone else never gets here.
      shared,
    };
  }

  /**
   * The fork a new child granted `/workspace` works on (P6.4): of the parent's
   * own workspace (a child parent's pinned fork, else its principal's, OWD-16), at its
   * head. Nothing for a replay (its pin is the first admission's) or a parent
   * already at its child bound (the store refuses it; no fork is left behind).
   */
  private async childWorkspace(
    attempt: AttemptIdentity & { orgId: string; runId: string },
    parent: HarnessRun,
    parentSpec: RunSpec,
    grants: ChildGrants,
    childRunId: string,
  ): Promise<ChildWorkspacePin | null> {
    const extension = pipeshub(parentSpec);
    if (
      this.workspaces === null ||
      extension === undefined ||
      !grants.mounts.includes('/workspace') ||
      (parent.children?.spawned ?? 0) >= parentSpec.budgets.maxChildren ||
      (await this.runs.get(parent.orgId, childRunId)) !== null
    ) {
      return null;
    }
    const origin =
      extension.workspace?.workspaceId ?? principalWorkspaceId(parent);
    const workspaceId = childForkOf(origin, childRunId);
    const forked = await this.workspaces.forkForChild(
      { ...attempt, orgId: parent.orgId, runId: parent.runId },
      origin,
      workspaceId,
    );
    return {
      workspaceId,
      forkOf: { workspaceId: origin, revision: forked.revision },
    };
  }

  /** A child whose budget its parent cannot cover is a child refusal (P6.5); nothing is written. */
  private async admit(command: AdmitChildCommand): Promise<ChildAdmission> {
    try {
      return await this.children.admitChild(command);
    } catch (error) {
      if (
        error instanceof HarnessConflictError &&
        (error.code === 'BUDGET_SHARE_EXCEEDS_REMAINING' ||
          error.code === 'BUDGET_ACCOUNT_CLOSED')
      ) {
        throw childRefused(
          'CHILD_BUDGET_EXCEEDS_REMAINING',
          "The child's budget is more than its parent has left",
        );
      }
      throw error;
    }
  }
}

/**
 * The model a child's input names: its parent's choice, or its role's model
 * as the org configures it (never the parent's account or display name).
 */
function inputModel(
  parent: RunInputSnapshot['model'],
  model: AskedModel | null,
): RunInputSnapshot['model'] {
  if (model === null) return structuredClone(parent);
  return {
    chatMode: parent.chatMode,
    ...(parent.reasoningEffort !== undefined
      ? { reasoningEffort: parent.reasoningEffort }
      : {}),
    modelKey: model.modelKey,
    modelName: model.model,
    modelProvider: model.definedProvider,
  };
}

/**
 * A child's input: its goal as the query, its model choice (its parent's, or its role's), the parent's clock and
 * the person's locale (G-52: the child's own run says the answer language, so its task text does not); for a
 * `code_task` engine child, the turn's input artifacts (CTL-6); and the task's parts its brief shows (BGUI-10).
 * Nothing else of the parent's turn.
 */
export function childInput(
  parent: HarnessRun,
  goal: string,
  model: AskedModel | null,
  handsOffInputs = false,
  task?: RunTaskParts,
): RunInputSnapshot {
  const input = parent.input ?? null;
  const artifacts = handsOffInputs ? (input?.inputArtifacts ?? []) : [];
  return {
    query: goal,
    recordIds: [],
    filters: {},
    attachments: [],
    model: input !== null ? inputModel(input.model, model) : NO_MODEL,
    ...(input?.timezone !== undefined ? { timezone: input.timezone } : {}),
    ...(input?.currentTime !== undefined
      ? { currentTime: input.currentTime }
      : {}),
    ...(input?.locale !== undefined ? { locale: input.locale } : {}),
    ...(input?.keywordSearchBudget !== undefined
      ? { keywordSearchBudget: input.keywordSearchBudget }
      : {}),
    ...(artifacts.length > 0
      ? { inputArtifacts: structuredClone(artifacts) }
      : {}),
    ...(task !== undefined ? { task: structuredClone(task) } : {}),
  };
}
