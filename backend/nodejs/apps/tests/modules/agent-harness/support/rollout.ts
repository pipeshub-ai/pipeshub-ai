import { HarnessRolloutDecision } from '../../../../src/modules/agent-harness/domain/rollout';
import { HarnessRolloutPolicy } from '../../../../src/modules/agent-harness/ports/harness-rollout';

export const HARNESS_ENABLED: HarnessRolloutDecision = {
  useHarness: true,
  harnessMajor: 1,
  reason: 'enabled',
};

export const KILL_SWITCH: HarnessRolloutDecision = {
  useHarness: false,
  harnessMajor: 1,
  reason: 'kill_switch',
};

/** A policy that answers every admission with whatever `current` returns. */
export const fixedRollout = (
  current: () => HarnessRolloutDecision = () => HARNESS_ENABLED,
): HarnessRolloutPolicy => ({
  decide: async () => current(),
  decideForOrg: async () => ({ ...current(), harnessOn: true, resumeInFlightRuns: true }),
});
