import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import useSWR from 'swr';

import { revalidateSwrNamespace, SWR_NS, SWR_POLICY } from '../swr-cache';
import { clearSessionCache } from '../session-cache';

// SWR's default cache binds the real setTimeout for focus events when it loads.
const realSetTimeout = globalThis.setTimeout;

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mountKey(key: unknown, fetcher: () => Promise<string>, policy: object = SWR_POLICY.list60s) {
  const view = renderHook(() => useSWR(key as string, fetcher, policy));
  await settle();
  return view;
}

describe('revalidateSwrNamespace', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    clearSessionCache();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('refetches mounted keys of that namespace only', async () => {
    const agents = vi.fn(async () => 'agents');
    const connectors = vi.fn(async () => 'connectors');
    await mountKey([SWR_NS.agentLists, 'a'], agents);
    await mountKey([SWR_NS.teamConnectorLists, 'b'], connectors);

    act(() => revalidateSwrNamespace(SWR_NS.agentLists));
    await settle();

    expect(agents).toHaveBeenCalledTimes(2);
    expect(connectors).toHaveBeenCalledTimes(1);
  });

  it('makes a key that is not mounted refetch on its next mount, inside the window', async () => {
    const fetcher = vi.fn(async () => 'agents');
    const first = await mountKey([SWR_NS.agentLists, 'a'], fetcher);
    first.unmount();

    revalidateSwrNamespace(SWR_NS.agentLists);
    await settle(1_000);
    await mountKey([SWR_NS.agentLists, 'a'], fetcher);

    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('leaves keys outside the tuple convention alone', async () => {
    const fetcher = vi.fn(async () => 'speech');
    await mountKey(SWR_NS.agentLists, fetcher);

    act(() => revalidateSwrNamespace(SWR_NS.agentLists));
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('SWR_POLICY.list60s', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    clearSessionCache();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('is not refetched on tab focus, even after the window', async () => {
    const fetcher = vi.fn(async () => 'agents');
    await mountKey([SWR_NS.agentLists, 'a'], fetcher);
    await settle(61_000);

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await new Promise((r) => realSetTimeout(r, 10));
    });
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('reuses a response for 60 s, then refetches on the next mount', async () => {
    const fetcher = vi.fn(async () => 'agents');
    (await mountKey([SWR_NS.agentLists, 'a'], fetcher)).unmount();
    await settle(59_000);
    (await mountKey([SWR_NS.agentLists, 'a'], fetcher)).unmount();
    expect(fetcher).toHaveBeenCalledTimes(1);

    await settle(2_000);
    await mountKey([SWR_NS.agentLists, 'a'], fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
