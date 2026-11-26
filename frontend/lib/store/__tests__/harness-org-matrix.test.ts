import { describe, it, expect, vi } from 'vitest';

vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from '@/lib/api/axios-instance';
import {
  useFeatureFlagsStore,
  selectAgentHarnessOn,
  selectAgentHarnessEnabled,
  selectAgentHarnessResumeEnabled,
  selectScheduledRunsEnabled,
  selectDurableAssistantAgentOffered,
  selectAgentHarnessPaused,
} from '@/lib/store/feature-flags-store';
import { HARNESS_ORG_ANSWERS, SURFACES_SHOWN } from './harness-org-answers';

/**
 * GATE (ADR-OPS-08): every harness selector reads the same under "server off" and "server on, Labs off"; with Labs
 * on the harness shows; the kill switch and infrastructure that is not ready stop new turns only.
 */
const SELECTORS = {
  selectAgentHarnessOn,
  selectAgentHarnessEnabled,
  selectAgentHarnessResumeEnabled,
  selectScheduledRunsEnabled,
  selectDurableAssistantAgentOffered,
};

const stateFor = (answer: keyof typeof HARNESS_ORG_ANSWERS) =>
  ({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness: HARNESS_ORG_ANSWERS[answer] }) as never;

describe('GATE: the harness off for the org reads as the harness off for the server', () => {
  for (const [name, select] of Object.entries(SELECTORS)) {
    it(`${name}: server on + Labs off ≡ server off (false)`, () => {
      expect(select(stateFor('labsOff'))).toBe(false);
      expect(select(stateFor('labsOff'))).toBe(select(stateFor('serverOff')));
    });

    it(`${name}: server on + Labs on → on`, () => {
      expect(select(stateFor('labsOn'))).toBe(true);
    });
  }

  it('the kill switch keeps the harness on for the org and reattach, and admits no new turn', () => {
    const state = stateFor('killSwitch');
    expect(selectAgentHarnessOn(state)).toBe(SURFACES_SHOWN.killSwitch);
    expect(selectAgentHarnessResumeEnabled(state)).toBe(true);
    expect(selectAgentHarnessEnabled(state)).toBe(false);
    expect(selectDurableAssistantAgentOffered(state)).toBe(false);
  });

  it('infrastructure that is not ready keeps the harness on for the org, with no reattach and no new turn', () => {
    const state = stateFor('infraNotReady');
    expect(selectAgentHarnessOn(state)).toBe(SURFACES_SHOWN.infraNotReady);
    expect(selectAgentHarnessResumeEnabled(state)).toBe(false);
    expect(selectAgentHarnessEnabled(state)).toBe(false);
  });

  for (const answer of ['killSwitch', 'infraNotReady'] as const) {
    it(`${answer} + Labs on → pages visible, new run refused`, () => {
      const state = stateFor(answer);
      // Pages, navigation and settings (HarnessOnly, the sidebar's gates, Schedules) follow the org's harness.
      expect(selectAgentHarnessOn(state)).toBe(true);
      expect(selectScheduledRunsEnabled(state)).toBe(true);
      // Starting a run follows admission.
      expect(selectAgentHarnessEnabled(state)).toBe(false);
      expect(selectDurableAssistantAgentOffered(state)).toBe(false);
      // And the surfaces that stay say they are paused.
      expect(selectAgentHarnessPaused(state)).toBe(true);
    });
  }

  it('nothing reads as paused where the harness is off for the org or admits new runs', () => {
    for (const answer of ['serverOff', 'labsOff', 'labsOn'] as const) {
      expect(selectAgentHarnessPaused(stateFor(answer)), answer).toBe(false);
    }
  });

  it('parses harnessOn from the effective flags, false when the server leaves it out', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { featureFlags: {}, agentHarness: { useHarness: false, harnessMajor: 2, reason: 'org_disabled', resumeInFlightRuns: false } },
    });
    await useFeatureFlagsStore.getState().fetchFlags();
    expect(useFeatureFlagsStore.getState().agentHarness?.harnessOn).toBe(false);
    expect(selectAgentHarnessOn(useFeatureFlagsStore.getState())).toBe(false);

    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { featureFlags: {}, agentHarness: { ...HARNESS_ORG_ANSWERS.labsOn } },
    });
    await useFeatureFlagsStore.getState().fetchFlags();
    expect(selectAgentHarnessOn(useFeatureFlagsStore.getState())).toBe(true);
  });
});
