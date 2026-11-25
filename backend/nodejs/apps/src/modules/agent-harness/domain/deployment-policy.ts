/**
 * The deployment's own policy as layers of a pin (02-lld §3.1, CPD-6): the
 * operator's sections of the approvals document (`default`, `agents[*]`) and
 * the flow rule its environment sets. Pure; the worker's readers
 * (`app/harness/approvals.py`) decide the same from the same inputs
 * (`contracts/harness/vectors/flow-settings.json`, `approvals.json`).
 */
import {
  DEPLOYMENT_FLOW_SCOPE,
  DEPLOYMENT_SCOPE,
  deploymentAgentScope,
  type PolicyLayer,
} from '../../control-plane';
import {
  FLOW_THRESHOLDS,
  type FlowThreshold,
} from '../contracts/generated/core/enums';
import {
  approvalLayers,
  isApprovalRules,
  readApprovalsDocument,
  type EffectiveApprovals,
  type FlowRule,
  type OrgApprovalRules,
} from './approvals';
import { approvalCeiling, meetApprovals } from './policy/approval-rules';

/** The flow settings as the deployment's environment gives them (`config.ts` `flowSettings`). */
export interface FlowSettings {
  /** `HARNESS_FLOW_GUARD`, parsed. */
  guard: boolean;
  /** `HARNESS_FLOW_GUARD_LEVEL`, as set. */
  level?: string | undefined;
  /** `HARNESS_FLOW_GUARD_ABOVE`, as set. */
  above?: string | undefined;
}

const FLOW_LEVELS: readonly FlowRule['level'][] = ['ask', 'deny'];

export interface DeploymentFlow {
  /** Null while `HARNESS_FLOW_GUARD` is off. */
  rule: FlowRule | null;
  /** The settings whose value was not one of theirs and fell back to the strictest (logged once at boot). */
  invalid: ('level' | 'above')[];
}

function setting<T extends string>(
  raw: string | undefined,
  allowed: readonly T[],
  strictest: T,
): { value: T; valid: boolean } {
  const value = (raw ?? '').trim().toLowerCase();
  if (value === '') return { value: allowed[0] as T, valid: true };
  const known = allowed.find((item) => item === value);
  return known !== undefined
    ? { value: known, valid: true }
    : { value: strictest, valid: false };
}

/**
 * The deployment's flow rule (SEC1), as the worker reads it from the same
 * settings: none while the guard is off; a level that is not one asks, a
 * threshold that is not one is the lowest (never wider).
 */
export function deploymentFlow(settings: FlowSettings): DeploymentFlow {
  if (!settings.guard) return { rule: null, invalid: [] };
  const level = setting(settings.level, FLOW_LEVELS, 'ask');
  const above = setting<FlowThreshold>(
    settings.above,
    FLOW_THRESHOLDS,
    'public',
  );
  return {
    rule: { level: level.value, above: above.value },
    invalid: [
      ...(level.valid ? [] : (['level'] as const)),
      ...(above.valid ? [] : (['above'] as const)),
    ],
  };
}

/** What a rule nobody can read decides, as the worker does: writes ask. */
export const UNREADABLE_RULES = { writes: 'ask' } as const;

/** A deployment layer holding approval rules the readers above admitted (or the rules that ask). */
const deployment = (approvals: PolicyLayer['approvals']): PolicyLayer => ({
  schemaVersion: 1,
  scope: { kind: 'deployment' },
  approvals,
});

const rulesOf = (raw: unknown): PolicyLayer['approvals'] =>
  isApprovalRules(raw) ? raw : UNREADABLE_RULES;

/**
 * The deployment's layers by scope: `deployment` (the document's `default`),
 * `deployment:flow` (the environment's rule) and `deployment:agent:<key>`
 * (the document's `agents[key]`). A document or an entry the schema refuses
 * asks for writes, as the worker fails closed on it.
 */
export function deploymentLayers(
  approvalsDocument: unknown,
  flow: FlowRule | null,
): Map<string, PolicyLayer> {
  const layers = new Map<string, PolicyLayer>();
  if (flow !== null) {
    layers.set(DEPLOYMENT_FLOW_SCOPE, deployment({ flow }));
  }
  const read = readApprovalsDocument(approvalsDocument);
  if (read.status === 'absent') return layers;
  if (read.status === 'invalid') {
    return layers.set(DEPLOYMENT_SCOPE, deployment(UNREADABLE_RULES));
  }
  const { sections } = read;
  if (sections.default !== undefined && sections.default !== null) {
    layers.set(DEPLOYMENT_SCOPE, deployment(rulesOf(sections.default)));
  }
  for (const [agentKey, rules] of Object.entries(sections.agents)) {
    if (rules === undefined || rules === null) continue;
    layers.set(deploymentAgentScope(agentKey), deployment(rulesOf(rules)));
  }
  return layers;
}

/** What no rule at all decides: every class allowed, no tool rule, no flow rule. */
export const NO_RULES: EffectiveApprovals = approvalCeiling({
  classes: {},
  tools: {},
});

/**
 * What the worker decides today for a run of (org, agent) from the approvals
 * document and the deployment's flow rule: the value a pin's `approvals`
 * dimension is compared with before the worker reads the pin instead.
 */
export function todayApprovals(
  approvalsDocument: unknown,
  orgId: string,
  agentKey: string | null,
  flow: FlowRule | null,
  edition: OrgApprovalRules | null = null,
): EffectiveApprovals {
  const rules = approvalLayers(
    readApprovalsDocument(approvalsDocument),
    orgId,
    agentKey,
    edition,
  )
    .map(approvalCeiling)
    .reduce(meetApprovals, NO_RULES);
  return flow === null
    ? rules
    : meetApprovals(rules, { ...NO_RULES, flow: [flow] });
}
