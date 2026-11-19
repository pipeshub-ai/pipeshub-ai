import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import useSWR from 'swr';
import { ConnectorsApi } from '../../api';
import { ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors } from '../../cache-keys';
import { DemoDataApi, type DemoDataStatus } from '../api';
import type { Connector } from '../../types';
import { useDemoDataStore } from '../store';
import { useDemoDataActive, useDemoDataStatus } from '../use-demo-data';
import { clearSessionCache } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { useAuthStore } from '@/lib/store/auth-store';

vi.mock('../../api', () => ({ ConnectorsApi: { getActiveConnectors: vi.fn(), getConnectorStats: vi.fn() } }));
vi.mock('../api', () => ({ DemoDataApi: { getStatus: vi.fn(), setInclude: vi.fn(), setEnabledForEveryone: vi.fn() } }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeHubApi: { searchAllRecords: vi.fn() } }));

const getActiveConnectors = vi.mocked(ConnectorsApi.getActiveConnectors);
const getStatus = vi.mocked(DemoDataApi.getStatus);
const setInclude = vi.mocked(DemoDataApi.setInclude);

function demo(isActive = true): Connector {
  return { _key: 'demo', type: 'Demo', name: 'Acme', isActive, scope: 'team' } as Connector;
}

function listing(...connectors: Connector[]) {
  return { success: true, connectors } as Awaited<ReturnType<typeof ConnectorsApi.getActiveConnectors>>;
}

function status(over: Partial<DemoDataStatus> = {}): DemoDataStatus {
  return { hasDemo: true, include: true, chosen: null, realData: false, offForEveryone: false, demoConnectorIds: ['demo'], ...over };
}

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function visit<T>(hook: () => T) {
  const view = renderHook(hook);
  await settle();
  view.unmount();
}

describe('demo data checks', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    clearSessionCache();
    getActiveConnectors.mockResolvedValue(listing());
    getStatus.mockResolvedValue(status());
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('asks once, quietly, for chat and Collections visits within a minute when the org has no demo', async () => {
    await visit(useDemoDataActive);
    await settle(30_000);
    await visit(useDemoDataActive);

    expect(getActiveConnectors).toHaveBeenCalledTimes(1);
    expect(getActiveConnectors).toHaveBeenCalledWith('team', 1, 100, { suppressErrorToast: true });
  });

  it('shares one request with every reader of the active team connector list', async () => {
    const { result } = renderHook(() => useSWR(ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors).data);
    await visit(useDemoDataActive);

    expect(getActiveConnectors).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual(listing());
  });

  it('takes the demo from a list another reader fetched, such as the connectors page', async () => {
    let answer!: (value: ReturnType<typeof listing>) => void;
    getActiveConnectors.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
    renderHook(() => useSWR(ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors).data);
    await settle();
    const demoCheck = renderHook(() => useDemoDataActive());
    await settle();

    await act(async () => answer(listing(demo())));
    await settle();

    expect(getActiveConnectors).toHaveBeenCalledTimes(1);
    expect(demoCheck.result.current).toBe(true);
  });

  it('keeps the earlier answer when a lookup fails', async () => {
    getActiveConnectors.mockResolvedValue(listing(demo()));
    await visit(useDemoDataActive);
    await settle(61_000);
    getActiveConnectors.mockRejectedValue(new Error('offline'));
    await visit(useDemoDataActive);

    expect(getActiveConnectors).toHaveBeenCalledTimes(2);
    expect(useDemoDataStore.getState().demoConnectors).toHaveLength(1);
  });

  it('does not bring back a removed demo from a lookup that started before the removal', async () => {
    let answer!: (value: ReturnType<typeof listing>) => void;
    getActiveConnectors.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
    const view = renderHook(() => useDemoDataActive());
    await settle();

    // Removing the demo deletes its connectors, which invalidates the list, then resets the store.
    getActiveConnectors.mockResolvedValue(listing());
    act(() => revalidateSwrNamespace(SWR_NS.teamConnectorLists));
    act(() => useDemoDataStore.getState().reset());
    await act(async () => answer(listing(demo())));
    await settle();

    expect(view.result.current).toBe(false);
    expect(useDemoDataStore.getState().demoConnectors).toEqual([]);
  });

  it('asks again after a minute, so a demo turned on later is found', async () => {
    await visit(useDemoDataActive);
    await settle(61_000);
    getActiveConnectors.mockResolvedValue(listing(demo()));

    const { result } = renderHook(() => useDemoDataActive());
    await settle();

    expect(getActiveConnectors).toHaveBeenCalledTimes(2);
    expect(result.current).toBe(true);
  });

  it('asks again after a minute when a demo was found, so a demo turned off is noticed', async () => {
    getActiveConnectors.mockResolvedValue(listing(demo()));
    await visit(useDemoDataActive);
    await settle(61_000);
    getActiveConnectors.mockResolvedValue(listing(demo(false)));

    const { result } = renderHook(() => useDemoDataActive());
    await settle();

    expect(result.current).toBe(false);
  });

  it('checks again inside the minute after a connector is created, changed or deleted', async () => {
    await visit(useDemoDataActive);
    await visit(useDemoDataStatus);
    revalidateSwrNamespace(SWR_NS.teamConnectorLists);
    await settle(1_000);

    await visit(useDemoDataActive);
    await visit(useDemoDataStatus);

    expect(getActiveConnectors).toHaveBeenCalledTimes(2);
    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it('reads the switch again on the next visit when the read failed, keeping what was known', async () => {
    await visit(useDemoDataStatus);
    await settle(61_000);
    getStatus.mockRejectedValueOnce(new Error('offline'));
    await visit(useDemoDataStatus);
    expect(useDemoDataStore.getState().status?.include).toBe(true);

    getStatus.mockResolvedValue(status({ include: false, chosen: false }));
    await visit(useDemoDataStatus);

    expect(getStatus).toHaveBeenCalledTimes(3);
    expect(useDemoDataStore.getState().status?.include).toBe(false);
  });

  it('checks for the demo again on the next visit when the lookup failed', async () => {
    getActiveConnectors.mockRejectedValueOnce(new Error('offline'));
    await visit(useDemoDataActive);
    getActiveConnectors.mockResolvedValue(listing(demo()));

    const { result } = renderHook(() => useDemoDataActive());
    await settle();

    expect(getActiveConnectors).toHaveBeenCalledTimes(2);
    expect(result.current).toBe(true);
  });

  it('reads the switch once a minute, and shows a change at once', async () => {
    await visit(useDemoDataStatus);
    setInclude.mockResolvedValue(status({ include: false, chosen: false }));
    await act(() => useDemoDataStore.getState().setInclude(false));

    const { result } = renderHook(() => useDemoDataStatus());
    await settle();

    expect(getStatus).toHaveBeenCalledTimes(1);
    expect(result.current?.include).toBe(false);
  });

  it('reads the switch for the new org after an org switch on the same screen', async () => {
    const { result } = renderHook(() => useDemoDataStatus());
    await settle();
    expect(result.current?.include).toBe(true);

    getStatus.mockResolvedValue(status({ include: false, chosen: false }));
    act(() => clearSessionCache({ refetch: true }));
    await settle();

    expect(getStatus).toHaveBeenCalledTimes(2);
    expect(result.current?.include).toBe(false);
  });

  it("forgets the last user's demo at sign-out and asks again for the next one", async () => {
    getActiveConnectors.mockResolvedValue(listing(demo()));
    await visit(useDemoDataActive);
    expect(useDemoDataStore.getState().demoConnectors).toHaveLength(1);

    act(() => useAuthStore.getState().logout());
    expect(useDemoDataStore.getState().demoConnectors).toEqual([]);

    getActiveConnectors.mockResolvedValue(listing());
    await visit(useDemoDataActive);
    expect(getActiveConnectors).toHaveBeenCalledTimes(2);
  });
});
