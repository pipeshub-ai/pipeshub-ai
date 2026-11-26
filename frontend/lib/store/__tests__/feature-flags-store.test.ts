import { describe, it, expect, beforeEach, vi } from 'vitest';

// Only the selectors (pure functions of already-loaded state) are under
// test here — stub the API client so importing the store doesn't pull in
// the real axios instance / auth interceptor chain.
vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from '@/lib/api/axios-instance';
import {
  useFeatureFlagsStore,
  selectAgentHarnessEnabled,
  selectAgentHarnessResumeEnabled,
  selectAgentHarnessOn,
  selectFeatureFlagsLoaded,
  selectMcpEnabled,
  selectActionsEnabled,
  selectVectorStoreRebuildEnabled,
  selectSkillsEnabled,
  selectUserContextEnabled,
  selectEngineRunsEnabled,
  selectSoftDeleteEnabled,
} from '../feature-flags-store';

describe('feature-flags-store selectors', () => {
  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: null, agentHarness: null, harnessReconcile: false, engineRuns: false, loading: false });
  });

  describe('selectFeatureFlagsLoaded', () => {
    it('is false while flags is null (not yet fetched)', () => {
      expect(selectFeatureFlagsLoaded(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('is true once flags is an object, even if empty', () => {
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectFeatureFlagsLoaded(useFeatureFlagsStore.getState())).toBe(true);
    });
  });

  describe('selectMcpEnabled (defaults to enabled)', () => {
    it('is true when flags is null (unloaded must not read as disabled)', () => {
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is true when the key is absent from a loaded flags map', () => {
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is true when explicitly true', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true } });
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is false only when explicitly false (admin opt-out)', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: false } });
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });
  });

  describe('selectActionsEnabled (defaults to enabled)', () => {
    it('is true when flags is null (unloaded must not read as disabled)', () => {
      expect(selectActionsEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is true when the key is absent from a loaded flags map', () => {
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectActionsEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is false only when explicitly false', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: false } });
      expect(selectActionsEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });
  });

  describe('selectSoftDeleteEnabled (defaults to disabled)', () => {
    it('is false while flags are unloaded or the key is absent', () => {
      expect(selectSoftDeleteEnabled(useFeatureFlagsStore.getState())).toBe(false);
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectSoftDeleteEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('is true only when explicitly true', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_SOFT_DELETE: true } });
      expect(selectSoftDeleteEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });
  });

  describe('selectVectorStoreRebuildEnabled (defaults to disabled)', () => {
    it('is false when flags is null', () => {
      expect(selectVectorStoreRebuildEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('is true only when explicitly true', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_VECTOR_STORE_REBUILD: true } });
      expect(selectVectorStoreRebuildEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });
  });

  describe('selectSkillsEnabled (defaults to enabled, Beta)', () => {
    it('is true when flags is null (unloaded must not read as disabled)', () => {
      expect(selectSkillsEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is true when the key is absent from a loaded flags map', () => {
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectSkillsEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is true when explicitly true', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: true } });
      expect(selectSkillsEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is false only when explicitly false (admin opt-out)', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: false } });
      expect(selectSkillsEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });
  });

  describe('selectUserContextEnabled (defaults to enabled)', () => {
    it('is true when flags is null (unloaded must not read as disabled)', () => {
      expect(selectUserContextEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is true when the key is absent from a loaded flags map', () => {
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectUserContextEnabled(useFeatureFlagsStore.getState())).toBe(true);
    });

    it('is false only when explicitly false', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_USER_CONTEXT: false } });
      expect(selectUserContextEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });
  });

  describe('agent harness (server rollout decision)', () => {
    it('is off until the server has answered', () => {
      expect(selectAgentHarnessEnabled(useFeatureFlagsStore.getState())).toBe(false);
      expect(selectAgentHarnessResumeEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('never reads the raw Labs flag', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_AGENT_HARNESS: true } });
      expect(selectAgentHarnessEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('stores the decision from the effective flags endpoint', async () => {
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        data: {
          featureFlags: { ENABLE_AGENT_HARNESS: false },
          agentHarness: {
            useHarness: false,
            harnessMajor: 1,
            reason: 'kill_switch',
            resumeInFlightRuns: true,
            harnessOn: true,
          },
        },
      });

      await useFeatureFlagsStore.getState().fetchFlags();

      const state = useFeatureFlagsStore.getState();
      expect(state.agentHarness).toEqual({
        useHarness: false,
        harnessMajor: 1,
        reason: 'kill_switch',
        resumeInFlightRuns: true,
        harnessOn: true,
      });
      // Kill switch: no new durable turns, but in-flight runs are still reattached and the harness stays on for the org.
      expect(selectAgentHarnessEnabled(state)).toBe(false);
      expect(selectAgentHarnessResumeEnabled(state)).toBe(true);
      expect(selectAgentHarnessOn(state)).toBe(true);
    });
  });

  // UX3 SE-4: whether a worker runs engine runs, which personal AI accounts serve.
  describe('engineRuns', () => {
    it('is off until the server has answered', () => {
      expect(selectEngineRunsEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    for (const [sent, expected] of [[true, true], [false, false], ['true', false], [undefined, false]] as const) {
      it(`reads ${JSON.stringify(sent)} as ${String(expected)}`, async () => {
        vi.mocked(apiClient.get).mockResolvedValueOnce({
          data: { featureFlags: {}, agentHarness: null, ...(sent === undefined ? {} : { engineRuns: sent }) },
        });
        await useFeatureFlagsStore.getState().fetchFlags();
        expect(selectEngineRunsEnabled(useFeatureFlagsStore.getState())).toBe(expected);
      });
    }
  });
});
