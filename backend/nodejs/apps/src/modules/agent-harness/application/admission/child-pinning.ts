import {
  basisHeads,
  NO_FACTS,
  type ControlPlanePin,
  type Heads,
  type PolicyLayer,
  type ResolvedRunConfig,
  type RunConfigPinner,
} from '../../../control-plane';
import { policyLayerSchema } from '../../contracts/generated/core/policy/policy-layer';
import { roleKeysOf, type ChildModel } from '../../domain/child-model';
import {
  childRefused,
  type ChildExecutorRequest,
  type ChildGrants,
  type SubAgentDefinition,
} from '../../domain/child-runs';
import type { PinnedEngineProfile } from '../../domain/engine-profiles';
import { HarnessUnavailableError } from '../../domain/errors';
import type { HarnessRun } from '../../domain/execution';
import { modelRouteOf, pinnedEngineProfile } from '../../domain/pin-shadow';
import type { PinShadowMetrics } from '../../ports/pin-shadow';
import { harnessLogMeta, HarnessLogger, runLogFields } from '../harness-log';
import { policyContext } from './context';
import { pinReadByWorker, type PinWorkerFeatures } from './pin-read-by-worker';
import { pinSubjectOf, todayPolicy } from './policy-pinning';
import type { AgentRunDefinition } from '../../domain/agent-definition';

export interface ChildPinRequest {
  parent: HarnessRun;
  /** The parent's pin; null for a parent the control plane did not pin. */
  parentResolved: ResolvedRunConfig | null;
  /** What the ⊆ rule granted the child. */
  grants: Pick<ChildGrants, 'maxTurns' | 'maxTokens'>;
  executor?: ChildExecutorRequest;
  /** The account kind an engine child of a loop parent was admitted on; it narrows the child's profile. */
  accountKind?: string;
  /**
   * What a loop parent's agent grants, as its admission read it. With it the
   * shadow pins the parent first, so the child's chain is the one `on` folds.
   */
  loopParent?: { agentDefinition: AgentRunDefinition | null };
  /** What the child runs as: its role keys decide which cost ceiling bounds its model (MA1). */
  subAgent?: SubAgentDefinition;
  /** The model the child's role set (MA1), as its root's definition names it; absent: its parent's. */
  model?: RoleModel;
}

/** A role's model with the configured entry the definition names for it. */
export interface RoleModel extends ChildModel {
  modelKey: string;
}

const MODEL_ROLES = 'models.roles';
const MODEL_PRIMARY = 'models.primary';
const ROLE_COST_CEILINGS = 'models.roleCostCeilings';

/** Why a spawn is refused on its model, in the words its parent's model reads. */
const modelRefusal = (
  request: ChildPinRequest,
  dimension: string | null | undefined,
): { dimension: string; message: string } | null => {
  const { model } = request;
  if (dimension === ROLE_COST_CEILINGS) {
    const role = model?.role ?? roleKeysOf(request.subAgent)[0] ?? 'default';
    const llm = request.parentResolved?.llm ?? null;
    const named =
      model !== undefined
        ? modelRouteOf(model)
        : llm !== null
          ? modelRouteOf(llm)
          : "its parent's model";
    return {
      dimension,
      message: `The sub-agent role "${role}" would call ${named}, which is above the cost ceiling for that role or has no price`,
    };
  }
  if (model !== undefined && dimension === MODEL_PRIMARY) {
    return {
      dimension: MODEL_ROLES,
      message: `The sub-agent role "${model.role}" is set to ${modelRouteOf(model)}, which is no longer configured`,
    };
  }
  return null;
};

export interface ChildPinningOptions {
  mode: ControlPlanePin;
  pinner: RunConfigPinner;
  metrics: PinShadowMetrics;
  logger: HarnessLogger;
  /** Which features a live worker advertises: a pinned child requires `resolved_config` by the rule a root does. Absent: required. */
  workerFeatures?: PinWorkerFeatures;
  /** Drops the org's cached snapshot, before the one re-pin a stale pin earns (`CP_STALE`). */
  refresh?: (orgId: string) => void;
}

/** A child's pin, and whether a worker reads it (then its spec carries it and it requires `resolved_config`). */
export interface ChildPin {
  resolved: ResolvedRunConfig;
  /** The revisions it was resolved at: its admission holds it to both. */
  resolvedAt: Heads;
  readByWorker: boolean;
}

/** What the child asks of its parent's pin: its budgets, for an engine child its narrowing, and its role's model. */
export const childRequestLayer = (request: ChildPinRequest): PolicyLayer =>
  policyLayerSchema.parse({
    schemaVersion: 1,
    scope: { kind: 'request' },
    budgets: {
      maxTurns: request.grants.maxTurns,
      maxTokens: request.grants.maxTokens,
    },
    ...(request.executor?.profile !== undefined ||
    request.accountKind !== undefined
      ? {
          engineProfile: {
            ...(request.executor?.profile ?? {}),
            ...(request.accountKind !== undefined
              ? { accountKinds: [request.accountKind] }
              : {}),
          },
        }
      : {}),
    ...(request.model !== undefined
      ? { models: { primary: modelRouteOf(request.model) } }
      : {}),
  });

/**
 * A child spawn on the one decision path (PIN-25, CPD5-55). `on`: a child of
 * a pinned parent is pinned under it (`parent ⊓ request`, on today's org and
 * agent layers); a pin the control plane refuses refuses the spawn, naming
 * the dimension, and one it cannot make fails closed. A parent without a pin
 * (admitted before the switch) keeps today's path. `shadow`: the child is
 * pinned after the spawn answers, under its parent pinned again in memory,
 * and a pin that is refused or cannot be made is counted, never acted on.
 */
export class ChildPinning {
  private readonly running = new Set<Promise<void>>();

  constructor(private readonly options: ChildPinningOptions) {}

  /** The engine authority a pinned loop parent may grant an engine child: its pin's profile. */
  engineAuthority(
    parentResolved: ResolvedRunConfig,
  ): PinnedEngineProfile | null {
    return pinnedEngineProfile(parentResolved.effectiveConfig);
  }

  /** Whether this spawn is decided by a pin: `on`, under a parent the control plane pinned. */
  decides(parentResolved: ResolvedRunConfig | null): boolean {
    return this.options.mode === 'on' && parentResolved !== null;
  }

  /**
   * A role's model is the one its root was admitted with (MA1 C3): the
   * parent's pin holds it under that role, or the org's routes dropped it
   * and the spawn is refused, never run on the parent's model instead.
   */
  assertRoleModel(
    parent: HarnessRun,
    parentResolved: ResolvedRunConfig,
    model: RoleModel,
  ): void {
    const roles = parentResolved.effectiveConfig.dimensions[MODEL_ROLES]
      ?.value as Record<string, unknown> | null | undefined;
    if (roles?.[model.role] === modelRouteOf(model)) return;
    this.refuse(parent, 'CP_OUTSIDE_CEILING', {
      dimension: MODEL_ROLES,
      message: `The sub-agent role "${model.role}" is set to ${modelRouteOf(model)}, which the organization does not allow`,
    });
  }

  private refuse(
    parent: HarnessRun,
    reason: string,
    refusal: { dimension: string | null; message: string },
  ): never {
    this.options.logger.warn(
      'child.refused',
      harnessLogMeta('child.refused', {
        ...runLogFields(parent),
        reason,
        dimension: refusal.dimension,
      }),
    );
    throw childRefused('CHILD_GRANT_TOO_LARGE', refusal.message);
  }

  async pin(request: ChildPinRequest): Promise<ChildPin | undefined> {
    const { mode } = this.options;
    if (mode === 'shadow') {
      const shadow = this.shadow(request);
      this.running.add(shadow);
      void shadow.finally(() => this.running.delete(shadow));
      return undefined;
    }
    if (mode !== 'on' || request.parentResolved === null) return undefined;
    const subject = this.subject(request);
    let pin: Awaited<ReturnType<RunConfigPinner['pin']>>;
    let resolvedAt: Heads;
    try {
      const { basis } = await this.options.pinner.route(subject);
      pin = await this.options.pinner.pin(subject, basis);
      resolvedAt = basisHeads(basis);
    } catch {
      throw new HarnessUnavailableError(
        "The child run's configuration could not be resolved",
      );
    }
    if ('code' in pin) {
      this.refuse(
        request.parent,
        pin.code,
        modelRefusal(request, pin.dimension) ?? {
          dimension: pin.dimension ?? null,
          message: `The child asks for more ${pin.dimension ?? 'authority'} than its parent holds`,
        },
      );
    }
    return {
      resolved: pin,
      resolvedAt,
      readByWorker: await pinReadByWorker(
        this.options.workerFeatures,
        this.options.logger,
        request.parent,
      ),
    };
  }

  /** Before the one re-pin of a child whose pin its admission found stale. */
  refresh(orgId: string): void {
    this.options.refresh?.(orgId);
  }

  /** Settles once every shadow pin started so far has finished (tests, shutdown). */
  async idle(): Promise<void> {
    while (this.running.size > 0) await Promise.all([...this.running]);
  }

  private subject(
    request: ChildPinRequest,
  ): Parameters<RunConfigPinner['pin']>[0] {
    const { parent } = request;
    return {
      orgId: parent.orgId,
      actorId: parent.actorId,
      agentKey: parent.agentKey ?? null,
      projectId: request.parentResolved?.scope.projectId ?? null,
      chatMode: parent.input?.model.chatMode ?? 'auto',
      surface: 'child',
      request: childRequestLayer(request),
      roleKeys: roleKeysOf(request.subAgent),
      ...(request.model !== undefined
        ? {
            model: {
              modelKey: request.model.modelKey,
              modelName: request.model.model,
              provider: request.model.provider,
            },
          }
        : {}),
      ...(request.parentResolved !== null
        ? { parent: request.parentResolved }
        : {}),
    };
  }

  /**
   * A parent admitted in `shadow` holds no pin (nothing is written), so the
   * child's chain could not be compared. The shadow therefore pins the parent
   * again, in memory, from its run as admission read it, and the child under
   * that pin: release ⊓ parent ⊓ request, as `on` folds it. A parent it
   * cannot pin was counted at its own admission; the child is then pinned
   * without one, as before.
   */
  private async shadowParent(
    request: ChildPinRequest,
  ): Promise<ResolvedRunConfig | null> {
    const { parent, loopParent } = request;
    if (request.parentResolved !== null) return request.parentResolved;
    if (loopParent === undefined || parent.input == null) return null;
    const context = policyContext(
      {
        orgId: parent.orgId,
        actorId: parent.actorId,
        ...(parent.agentKey !== undefined ? { agentKey: parent.agentKey } : {}),
        idempotencyKey: parent.idempotencyKey,
        deadlineMs: parent.absoluteDeadlineAt - parent.createdAt,
        input: parent.input,
      },
      {},
    );
    context.agentDefinition = loopParent.agentDefinition;
    // The parent runs, so nothing but policy is asked of its route: the child's chain is what is compared.
    const pin = await this.options.pinner.pin(
      pinSubjectOf(context, todayPolicy(context), { facts: NO_FACTS }),
    );
    return 'code' in pin ? null : pin;
  }

  private async shadow(request: ChildPinRequest): Promise<void> {
    let found: 'refused' | 'unavailable' | null = null;
    try {
      const parentResolved = await this.shadowParent(request);
      const pin = await this.options.pinner.pin(
        this.subject({ ...request, parentResolved }),
      );
      if ('code' in pin) found = 'refused';
    } catch {
      found = 'unavailable';
    }
    if (found === null) return;
    this.options.metrics.mismatch(found);
    this.options.logger.warn(
      'cp.shadow_mismatch',
      harnessLogMeta('cp.shadow_mismatch', {
        ...runLogFields(request.parent),
        surface: 'child',
        dimensions: [found],
      }),
    );
  }
}
