import {
  approvalCeiling,
  PolicyResolver,
  type EffectiveApprovals,
} from '../../../../src/modules/agent-harness/domain/policy';
import type { NormalizedRules } from '../../../../src/modules/agent-harness/domain/approvals';

/**
 * What approval layers come to: the `approvals` dimension as the one policy
 * resolver folds it (strictest of every layer). No layer decides nothing, so
 * every call is allowed.
 */
export function foldedApprovals(layers: readonly NormalizedRules[]): EffectiveApprovals {
  const resolved = new PolicyResolver().resolve(layers.map((approvals) => ({ kind: 'org' as const, values: { approvals } })));
  if (resolved.refusal !== null) throw new Error(`approvals do not fold: ${resolved.refusal.detail}`);
  return (resolved.config.dimensions['approvals']?.value as EffectiveApprovals | undefined) ?? approvalCeiling({ classes: {}, tools: {} });
}
