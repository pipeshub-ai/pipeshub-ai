import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Only the selectors (pure functions of already-loaded state) are under
// test here — stub the API client so importing the store doesn't pull in
// the real axios instance / auth interceptor chain.
vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from '@/lib/api/axios-instance';
import { ErrorType } from '@/lib/api/api-error';
import {
  useFeatureFlagsStore,
  selectAgentHarnessEnabled,
  selectAgentHarnessResumeEnabled,
  selectAgentHarnessOn,
  selectFeatureFlagsLoaded,
  selectProjectsEnabled,
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

describe('fetchFlags', () => {
  const get = vi.mocked(apiClient.get);
  const noAnswer = { type: ErrorType.NETWORK_ERROR, message: 'Network error. Please check your connection.' };

  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: null, loading: false });
    get.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('asks again when a read gets no answer, so a blip does not turn Projects off', async () => {
    get
      .mockRejectedValueOnce(noAnswer)
      .mockResolvedValueOnce({ data: { featureFlags: { ENABLE_PROJECTS: true } } });
    const done = useFeatureFlagsStore.getState().fetchFlags();
    await vi.advanceTimersByTimeAsync(0);
    // Still unknown between the tries: the rail keeps the Projects row it showed last time.
    expect(useFeatureFlagsStore.getState().flags).toBeNull();
    await vi.runAllTimersAsync();
    await done;
    expect(selectProjectsEnabled(useFeatureFlagsStore.getState())).toBe(true);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('reads every opt-in flag as off once the last try gets no answer', async () => {
    get.mockRejectedValue(noAnswer);
    const done = useFeatureFlagsStore.getState().fetchFlags();
    // The read's own tries (300 ms, then 1 s); the store's later retries are feature-flags-retry.test.ts's.
    await vi.advanceTimersByTimeAsync(1300);
    await done;
    expect(useFeatureFlagsStore.getState().flags).toEqual({});
    expect(get).toHaveBeenCalledTimes(3);
    expect(useFeatureFlagsStore.getState().retrying).toBe(true);
  });

  it('does not ask again when the server answered with an error', async () => {
    get.mockRejectedValueOnce({ type: ErrorType.SERVER_ERROR, statusCode: 500, message: 'Server error' });
    await useFeatureFlagsStore.getState().fetchFlags();
    expect(useFeatureFlagsStore.getState().flags).toEqual({});
    expect(get).toHaveBeenCalledTimes(1);
  });
});
