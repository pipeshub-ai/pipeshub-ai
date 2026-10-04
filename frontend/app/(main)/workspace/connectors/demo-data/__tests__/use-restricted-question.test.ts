import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { renderHook, waitFor, act, cleanup } from '@testing-library/react';
import type { Connector } from '../../types';
import { useDemoDataStore } from '../store';
import { checkRestrictedQuestionAccess } from '../restricted-question';
import { useRestrictedQuestionAccess } from '../use-restricted-question';

vi.mock('../../api', () => ({ ConnectorsApi: { getActiveConnectors: vi.fn(), getConnectorStats: vi.fn() } }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeHubApi: { searchAllRecords: vi.fn() } }));
vi.mock('../restricted-question', () => ({ checkRestrictedQuestionAccess: vi.fn() }));

const check = vi.mocked(checkRestrictedQuestionAccess);
const demo = (key: string) => ({ _key: key, type: 'Demo', name: 'Acme', isActive: true }) as Connector;

beforeEach(() => {
  check.mockReset();
  check.mockResolvedValue(null);
});

afterEach(() => {
  // Unmount before the store is reset, or the hook looks up again mid-teardown.
  cleanup();
  useDemoDataStore.getState().reset();
});

describe('useRestrictedQuestionAccess', () => {
  it('checks against the demo connectors the page found', async () => {
    useDemoDataStore.setState({ demoConnectors: [demo('demo-2'), demo('demo-1')] });
    check.mockResolvedValue({ canSee: false, readerEmail: 'bob@acme-demo.example' });

    const { result } = renderHook(() => useRestrictedQuestionAccess());

    await waitFor(() => expect(result.current).toEqual({ canSee: false, readerEmail: 'bob@acme-demo.example' }));
    expect(check).toHaveBeenCalledWith(['demo-1', 'demo-2']);
  });

  it('drops an answer for connectors that are no longer the current ones', async () => {
    let answerFirst: (v: { canSee: boolean; readerEmail: null }) => void = () => {};
    check.mockImplementationOnce(() => new Promise((resolve) => (answerFirst = resolve)));
    check.mockResolvedValueOnce({ canSee: true, readerEmail: null });
    useDemoDataStore.setState({ demoConnectors: [demo('old')] });

    const { result } = renderHook(() => useRestrictedQuestionAccess());
    act(() => useDemoDataStore.setState({ demoConnectors: [demo('new')] }));
    await waitFor(() => expect(result.current).toEqual({ canSee: true, readerEmail: null }));
    await act(async () => answerFirst({ canSee: false, readerEmail: null }));

    expect(result.current).toEqual({ canSee: true, readerEmail: null });
  });

  it('looks again while access is unknown, e.g. during the first sync', async () => {
    vi.useFakeTimers();
    try {
      check.mockResolvedValueOnce(null).mockResolvedValueOnce({ canSee: false, readerEmail: null });
      useDemoDataStore.setState({ demoConnectors: [demo('demo-1')] });

      const { result } = renderHook(() => useRestrictedQuestionAccess());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(15_000);
      });
      expect(result.current).toEqual({ canSee: false, readerEmail: null });
      expect(check).toHaveBeenCalledTimes(2);

      // Once known, it stops asking.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(check).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
