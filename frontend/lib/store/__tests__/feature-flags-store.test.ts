import { describe, it, expect, beforeEach, vi } from 'vitest';

// Only the selectors (pure functions of already-loaded state) are under
// test here — stub the API client so importing the store doesn't pull in
// the real axios instance / auth interceptor chain.
vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: { get: vi.fn() },
}));

import {
  useFeatureFlagsStore,
  selectFeatureFlagsLoaded,
  selectMcpEnabled,
  selectActionsEnabled,
  selectVectorStoreRebuildEnabled,
  selectSkillsEnabled,
  selectUserContextEnabled,
} from '../feature-flags-store';

describe('feature-flags-store selectors', () => {
  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: null, loading: false });
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

  describe('selectMcpEnabled (defaults to disabled)', () => {
    it('is false when flags is null', () => {
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('is false when the key is absent from a loaded flags map', () => {
      useFeatureFlagsStore.setState({ flags: {} });
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(false);
    });

    it('is true only when explicitly true', () => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true } });
      expect(selectMcpEnabled(useFeatureFlagsStore.getState())).toBe(true);
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
});
