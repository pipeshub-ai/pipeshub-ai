import type { AgentHarnessRollout } from '@/lib/store/feature-flags-store';

/**
 * The effective-flags answers the GATE matrix runs every harness surface under (ADR-OPS-08). `serverOff` and
 * `labsOff` must render identically; `labsOn` shows the harness; `killSwitch` and `infraNotReady` admit no new
 * turn but keep the product behaviour that came with the harness.
 */
export const HARNESS_ORG_ANSWERS: Record<
  'serverOff' | 'labsOff' | 'labsOn' | 'killSwitch' | 'infraNotReady',
  AgentHarnessRollout | null
> = {
  serverOff: null,
  labsOff: { useHarness: false, harnessMajor: 2, reason: 'org_disabled', resumeInFlightRuns: false, harnessOn: false },
  labsOn: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
  killSwitch: { useHarness: false, harnessMajor: 2, reason: 'kill_switch', resumeInFlightRuns: true, harnessOn: true },
  infraNotReady: {
    useHarness: false,
    harnessMajor: 2,
    reason: 'infrastructure_unavailable',
    resumeInFlightRuns: false,
    harnessOn: true,
  },
};

/** Whether the harness's product surfaces show under each answer. */
export const SURFACES_SHOWN: Record<keyof typeof HARNESS_ORG_ANSWERS, boolean> = {
  serverOff: false,
  labsOff: false,
  labsOn: true,
  killSwitch: true,
  infraNotReady: true,
};
