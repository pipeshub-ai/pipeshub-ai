import type { ResolvedRunConfig } from '../../control-plane';
import type { RunSpec } from '../contracts/generated/core/run-spec';
import { modelRouteId } from './gateway-token';
import type { RuleLayer } from '../contracts/generated/core/enums';
import { ruleLayer } from './interactions';
import type { JournalEvent } from './journal';
import { isSharedGrant } from './conversation-grants';

type EffectClass = 'read' | 'workspace' | 'write' | 'execute';
type ApprovalLevel = 'allow' | 'ask' | 'deny';

/**
 * What a run may do, as its pinned spec says (SEC1): the tools it was granted,
 * where its sandbox may reach, the model routes it may call, its budgets and,
 * for an engine run, the profile it executes under. Read-only and derived from
 * the spec alone, so it can never disagree with what the worker enforces from
 * the same spec. Org approval rules and the deployment's flow rule narrow it
 * further when a call is made; a call they block is journaled `tool.denied`
 * with its rule.
 */
export interface EffectivePolicy {
  /** False until the run's first claim pins its spec. */
  pinned: boolean;
  tools: string[];
  sandboxProfile?: 'agent-code' | 'none';
  /** Network beyond the model and tool gateways: `none` or one named allowlist. */
  egress?: string;
  modelRoutes: string[];
  budgets?: {
    maxTurns: number;
    maxTokens: number;
    maxCostUsd: number;
    activeTimeS: number;
    maxChildren: number;
  };
  engine?: {
    kind: string;
    version: string;
    mode?: 'plan' | 'build' | 'review';
    egress?: string;
    toolEffects?: EffectClass[];
    maxTools?: number | null;
    approvals?: Record<EffectClass, ApprovalLevel>;
  };
  /** SEC2: what the run's sandbox gateways refused (`gateway.refused`), oldest first; absent when none. */
  blockedRequests?: BlockedRequest[];
  /** UX1: the calls the run held for a person's approval and why, oldest first; absent when none. */
  heldRequests?: HeldRequest[];
  /** CPD-5: the run's pin, when the control plane pinned it; the fields above are derived from it. */
  resolved?: { headRevision: number; digest: string };
}

/**
 * The run's pin as `GET …/runs/{runId}/resolved` answers it (02-lld §9.1): the
 * pin, what the live layer narrowed (CPD-6: none yet) and the calls the run
 * held or the gateways refused, as `effectivePolicy` lists them.
 */
export interface ResolvedRunView {
  resolved: ResolvedRunConfig;
  liveApplied: never[];
  heldRequests: HeldRequest[];
  blockedRequests: BlockedRequest[];
}

/**
 * A run admitted with no pin (`CONTROL_PLANE_PIN` off or shadow, the default): nothing was resolved, which is
 * an ordinary answer for `/status` to fall back from, not a missing run (CMDUI2).
 */
export interface UnpinnedRunView {
  resolved: null;
  liveApplied: never[];
  heldRequests: never[];
  blockedRequests: never[];
}

export const unpinnedRunView = (): UnpinnedRunView => ({
  resolved: null,
  liveApplied: [],
  heldRequests: [],
  blockedRequests: [],
});

/**
 * A call held for approval, by the rule its `interaction.explained` names and
 * the layer that set it (QA6-F6); `approval_policy` for one held with no
 * reason journaled (an org's or agent's rule before reasons named them).
 */
export interface HeldRequest {
  interactionId: string;
  rule: string;
  layer?: RuleLayer;
  toolId?: string;
  sensitivity?: 'public' | 'private' | 'unknown';
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'cancelled';
  scope?: 'conversation';
  ts: string;
}

const APPROVAL_INTERACTION = 'ix.approve.';
export const HELD_REQUEST_EVENTS: ReadonlySet<string> = new Set([
  'interaction.explained',
  'interaction.requested',
  'interaction.resolved',
  'interaction.granted',
]);

/** The run's approvals as the admin view shows them: rules, ids and codes, never arguments or titles. */
export function heldRequestsOf(events: readonly JournalEvent[]): HeldRequest[] {
  const held = new Map<string, HeldRequest>();
  const explained = new Map<string, Record<string, unknown>>();
  for (const event of events) {
    const data = event.data ?? {};
    const id = data['interactionId'];
    if (typeof id !== 'string' || !id.startsWith(APPROVAL_INTERACTION))
      continue;
    if (event.type === 'interaction.explained' && event.writer === 'worker') {
      explained.set(id, data);
    } else if (event.type === 'interaction.requested' && !held.has(id)) {
      const reason = explained.get(id);
      const sensitivity = reason?.['sensitivity'];
      const layer = ruleLayer(reason?.['layer']);
      held.set(id, {
        interactionId: id,
        rule:
          typeof reason?.['rule'] === 'string'
            ? reason['rule']
            : 'approval_policy',
        ...(layer !== undefined ? { layer } : {}),
        ...(typeof reason?.['toolId'] === 'string'
          ? { toolId: reason['toolId'] }
          : {}),
        ...(sensitivity === 'public' ||
        sensitivity === 'private' ||
        sensitivity === 'unknown'
          ? { sensitivity }
          : {}),
        status: 'pending',
        ts: event.ts,
      });
    } else if (event.type === 'interaction.resolved') {
      const view = held.get(id);
      const decision = data['decision'];
      if (view !== undefined) {
        view.status =
          decision === 'approved' ||
          decision === 'denied' ||
          decision === 'expired'
            ? decision
            : 'cancelled';
      }
    } else if (
      event.type === 'interaction.granted' &&
      data['scope'] === 'conversation' &&
      !isSharedGrant(data)
    ) {
      const view = held.get(id);
      if (view !== undefined) view.scope = 'conversation';
    }
  }
  return [...held.values()];
}

export interface BlockedRequest {
  gateway: 'egress' | 'model';
  reason: string;
  status: number;
  host?: string;
  param?: string;
  ts: string;
}

/** The run's `gateway.refused` events as the blocked view shows them: codes, a host or a field path. */
export function blockedRequestsOf(
  events: readonly JournalEvent[],
): BlockedRequest[] {
  const blocked: BlockedRequest[] = [];
  for (const event of events) {
    const data = event.data;
    if (event.type !== 'gateway.refused' || data === undefined) continue;
    const { gateway, reason, status, host, param } = data;
    if (
      (gateway !== 'egress' && gateway !== 'model') ||
      typeof reason !== 'string' ||
      typeof status !== 'number'
    ) {
      continue;
    }
    blocked.push({
      gateway,
      reason,
      status,
      ...(typeof host === 'string' ? { host } : {}),
      ...(typeof param === 'string' ? { param } : {}),
      ts: event.ts,
    });
  }
  return blocked;
}

export const UNPINNED_POLICY: EffectivePolicy = {
  pinned: false,
  tools: [],
  modelRoutes: [],
};

export function effectivePolicyOf(
  spec: RunSpec | null,
  resolved: ResolvedRunConfig | null = null,
): EffectivePolicy {
  if (spec === null) return UNPINNED_POLICY;
  const policy: EffectivePolicy = {
    pinned: true,
    ...(resolved !== null
      ? {
          resolved: {
            headRevision: resolved.headRevision,
            digest: resolved.digest,
          },
        }
      : {}),
    tools: spec.grants.tools.map((tool) => tool.toolId).sort(),
    sandboxProfile: spec.grants.sandboxProfile,
    egress: spec.grants.egress,
    modelRoutes: spec.model.policy.map((route) => modelRouteId(route)),
    budgets: {
      maxTurns: spec.budgets.maxTurns,
      maxTokens: spec.budgets.maxTokens,
      maxCostUsd: spec.budgets.maxCostUsd,
      activeTimeS: spec.budgets.activeTimeS,
      maxChildren: spec.budgets.maxChildren,
    },
  };
  const executor = spec.executor;
  if (executor?.kind === 'engine') {
    const profile = executor.engine.profile;
    policy.engine = {
      kind: executor.engine.kind,
      version: executor.engine.version,
      ...(profile !== undefined
        ? {
            mode: profile.mode,
            egress: profile.egress,
            toolEffects: [...profile.tools.effects],
            maxTools: profile.tools.maxTools,
            approvals: { ...profile.approvals },
          }
        : {}),
    };
  }
  return policy;
}
