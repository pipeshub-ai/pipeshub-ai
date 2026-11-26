import { ownCostCap } from '../../domain/answered-run';
import {
  basisHeads,
  type ControlPlanePin,
  type PinSubject,
  type RunConfigPinner,
} from '../../../control-plane';
import { requestNarrowing } from '../../domain/engine-profiles';
import {
  definitionLayer,
  modelRouteOf,
  pinShadowMismatches,
  requestLayer,
  triggerLayer,
  type PinShadowDimension,
  type TodayPolicy,
} from '../../domain/pin-shadow';
import { grantedModel, legacyBudgets } from '../../domain/run-spec';
import type { PinShadowMetrics } from '../../ports/pin-shadow';
import type { EffectiveApprovals } from '../../domain/approvals';
import type { ResolvedRoute } from '../../ports/routing-decider';
import { pinReadByWorker, type PinWorkerFeatures } from './pin-read-by-worker';
import { harnessLogMeta, HarnessLogger } from '../harness-log';
import type { PolicyContext } from './context';
import type { AdmissionStep } from './pipeline';
import { AdmissionRefusal } from './refusals';
import {
  HarnessForbiddenError,
  HarnessRunConfigUnavailableError,
} from '../../domain/errors';

export interface PolicyPinningOptions {
  mode: ControlPlanePin;
  pinner: RunConfigPinner;
  metrics: PinShadowMetrics;
  logger: HarnessLogger;
  /** Whether the snapshot folds the org's Labs switches (the rollout policy exposes them); absent: it does. */
  rolloutFolded?: boolean;
  /** Drops the org's cached snapshot, before the one re-pin a stale head earns (`CP_STALE`). */
  refresh?: (orgId: string) => void;
  /**
   * Which features a live worker advertises: a pinned run requires
   * `resolved_config` only while one does (the F2 rule), so a deploy that
   * upgrades Node first runs it on the spec's legacy fields instead of
   * queueing it for a worker that does not exist yet. Absent: required.
   */
  workerFeatures?: PinWorkerFeatures;
  /**
   * What the worker decides per call today for a run of (org, agent): in
   * `shadow` the pinned `approvals` are compared with it. Absent: not compared.
   */
  approvals?: (
    orgId: string,
    agentKey: string | null,
  ) => Promise<EffectiveApprovals>;
}

const surfaceOf = (context: PolicyContext): PinSubject['surface'] =>
  context.draining
    ? 'next_run'
    : context.request.trigger !== undefined
      ? 'schedule'
      : (context.request.surface ?? 'web');

/** What today's admission decided, as the pin is compared on it. */
export function todayPolicy(context: PolicyContext): TodayPolicy {
  const { request } = context;
  const granted = grantedModel(context.agentDefinition, request.input.model);
  const route = context.modelAccess?.route ?? granted.route;
  return {
    ...(granted.model !== undefined
      ? { modelKey: granted.model.modelKey }
      : {}),
    executor: context.executor,
    engineProfile: context.engineProfile,
    // CTL-6: the turn runs on the loop; the engine profile bounds its child, not its own budgets.
    budgets: legacyBudgets(
      context.codeTask === true ? null : context.engineProfile,
      ownCostCap(request.trigger, context.answered),
      Math.max(1, Math.ceil(request.deadlineMs / 1000)),
    ),
    modelRoute: modelRouteOf(route),
  };
}

/**
 * The configured model the run names: the agent's granted model, else the
 * key the client chose. A client that names a key without a model name gets
 * that entry's first name (the spec then repeats the key as the name, which
 * no entry lists).
 */
function modelOf(context: PolicyContext): Pick<PinSubject, 'model'> {
  const requested = context.request.input.model;
  const { model } = grantedModel(context.agentDefinition, requested);
  if (model === undefined) return {};
  const named =
    (context.agentDefinition?.models ?? []).length > 0 ||
    requested.modelName !== undefined;
  return {
    model: {
      modelKey: model.modelKey,
      ...(named ? { modelName: model.modelName } : {}),
    },
  };
}

/**
 * The subject the control plane pins for this admission, from what the steps
 * above decided and, when the control plane resolved the turn's route, the
 * facts it was routed on: the pin then holds the same route.
 */
export function pinSubjectOf(
  context: PolicyContext,
  today: TodayPolicy,
  routed: Pick<ResolvedRoute, 'facts'> | undefined = context.routed,
): PinSubject {
  const { request } = context;
  // An engine profile withheld at admission (CTL-6) is not pinned either: the turn runs on the loop without it.
  const agent = definitionLayer(
    context.engineProfile === null && context.agentDefinition !== null
      ? { ...context.agentDefinition, engineProfile: null }
      : context.agentDefinition,
  );
  // An answer carries on its question's cap (WP-64): the pin narrows the run's budget as a firing's cap does.
  const trigger = triggerLayer(
    ownCostCap(request.trigger, context.answered) ?? undefined,
  );
  return {
    orgId: request.orgId,
    actorId: request.actorId,
    agentKey: request.agentKey ?? null,
    agentOwner: context.agentDefinition?.owner ?? null,
    projectId: routed?.facts.placement?.projectId ?? request.projectId ?? null,
    chatMode: request.input.model.chatMode,
    surface: surfaceOf(context),
    request: requestLayer(
      request.deadlineMs,
      context.engineProfile === null
        ? undefined
        : {
            ...requestNarrowing(request.input.model.reasoningEffort),
            // The account kind the run was admitted on narrows the profile (OI-CPD3-1).
            ...(context.modelAccess !== null
              ? { accountKinds: [context.modelAccess.access.accountKind] }
              : {}),
          },
      today.modelRoute,
    ),
    ...(agent !== undefined ? { agent } : {}),
    ...(trigger !== undefined ? { trigger } : {}),
    ...modelOf(context),
    // A1 decided this route against the vendor rules and the account: it need not be a configured model.
    ...(context.modelAccess !== null
      ? { verifiedRoutes: [today.modelRoute] }
      : {}),
    ...(routed !== undefined ? { routingFacts: routed.facts } : {}),
  };
}

/**
 * `PolicyPinning` (R2 CP11, 05-migration-plan §1 rule 6), the last policy
 * step. `off`: nothing. `shadow`: the control plane pins beside today's
 * decision and every dimension they disagree on is counted and logged, never
 * acted on; the pin runs after the admission answers, so it costs the turn
 * nothing, and a pin that fails or refuses changes nothing either. `on`: the
 * pin is the run's (`context.resolved`, written in the admission's
 * transaction); a pin the control plane refuses refuses the run, and one it
 * cannot make fails closed.
 */
export type { PinWorkerFeatures };

export class PolicyPinning implements AdmissionStep<PolicyContext> {
  readonly name = 'policy_pinning';
  private readonly running = new Set<Promise<void>>();

  constructor(private readonly options: PolicyPinningOptions) {}

  /** Whether the shadow compares the rollout switches (the snapshot folds them). */
  get comparesRollout(): boolean {
    return this.options.rolloutFolded ?? true;
  }

  run(context: PolicyContext): Promise<void> {
    if (this.options.mode === 'on') return this.pinOn(context);
    if (this.options.mode === 'shadow') {
      const shadow = this.shadow(context);
      this.running.add(shadow);
      void shadow.finally(() => this.running.delete(shadow));
    }
    return Promise.resolve();
  }

  /**
   * Pins again on a refreshed snapshot, once, after the admission found the
   * org head or the deployment's revision moved. A turn the control plane
   * routed durable that the change sends to today's loop is not admitted on
   * the route it lost: the caller asks again and is routed afresh.
   */
  async repin(context: PolicyContext): Promise<void> {
    this.options.refresh?.(context.request.orgId);
    await this.pinOn(context, true);
    if (
      context.routed !== undefined &&
      context.resolved?.routing.path !== 'durable'
    ) {
      throw new AdmissionRefusal(
        'unavailable',
        new HarnessRunConfigUnavailableError(
          'The organization changed its configuration during admission; try again',
        ),
      );
    }
  }

  /** `fresh`: the route's snapshot is behind a write, so the pin loads its own. */
  private async pinOn(context: PolicyContext, fresh = false): Promise<void> {
    const subject = pinSubjectOf(context, todayPolicy(context));
    let pin: Awaited<ReturnType<RunConfigPinner['pin']>>;
    let basis: NonNullable<ResolvedRoute['basis']>;
    try {
      basis =
        (fresh ? undefined : context.routed?.basis) ??
        (await this.options.pinner.route(subject)).basis;
      pin = await this.options.pinner.pin(subject, basis);
    } catch {
      throw new AdmissionRefusal(
        'unavailable',
        new HarnessRunConfigUnavailableError(),
      );
    }
    if ('code' in pin) {
      throw new AdmissionRefusal(
        pin.code.toLowerCase(),
        new HarnessForbiddenError(
          `The organization's policy refuses this run (${pin.code})`,
        ),
      );
    }
    context.resolved = pin;
    context.resolvedAt = basisHeads(basis);
    context.resolvedConfig = await pinReadByWorker(
      this.options.workerFeatures,
      this.options.logger,
      context.request,
    );
  }

  /** Settles once every shadow pin started so far has been compared (tests, shutdown). */
  async idle(): Promise<void> {
    while (this.running.size > 0) await Promise.all([...this.running]);
  }

  private async shadow(context: PolicyContext): Promise<void> {
    let found: PinShadowDimension[];
    try {
      const today = todayPolicy(context);
      const { orgId, agentKey } = context.request;
      // The shadow route's facts and snapshot, when it resolved: the pin compared is the one `on` would make.
      const routed = await context.shadowRoute;
      const [pin, approvals] = await Promise.all([
        this.options.pinner.pin(
          pinSubjectOf(context, today, routed),
          routed?.basis,
        ),
        this.options.approvals?.(orgId, agentKey ?? null),
      ]);
      found =
        'code' in pin
          ? ['refused']
          : pinShadowMismatches(
              { ...today, ...(approvals !== undefined ? { approvals } : {}) },
              pin.effectiveConfig,
              { llm: pin.llm, rolloutFolded: this.comparesRollout },
            );
    } catch {
      found = ['unavailable'];
    }
    if (found.length === 0) return;
    for (const dimension of found) this.options.metrics.mismatch(dimension);
    this.options.logger.warn(
      'cp.shadow_mismatch',
      harnessLogMeta('cp.shadow_mismatch', {
        orgId: context.request.orgId,
        sessionId: context.request.sessionId,
        actorId: context.request.actorId,
        agentKey: context.request.agentKey,
        dimensions: found,
      }),
    );
  }
}
