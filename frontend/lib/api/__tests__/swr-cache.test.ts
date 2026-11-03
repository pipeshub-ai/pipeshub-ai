import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import useSWR from 'swr';

import {
  isSwrReadUnavailable,
  revalidateSwrKey,
  revalidateSwrNamespace,
  SWR_NS,
  SWR_POLICY,
  writeSwrKey,
} from '../swr-cache';
import { ErrorType } from '../api-error';
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

describe('single-key helpers', () => {
  const key = [SWR_NS.agentDetails, 'a1'] as const;
  const other = [SWR_NS.agentDetails, 'a2'] as const;

  beforeEach(() => {
    vi.useFakeTimers();
    clearSessionCache();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('revalidateSwrKey refetches that key only, now when mounted', async () => {
    const one = vi.fn(async () => 'a1');
    const two = vi.fn(async () => 'a2');
    await mountKey(key, one);
    await mountKey(other, two);

    act(() => revalidateSwrKey(key));
    await settle();

    expect(one).toHaveBeenCalledTimes(2);
    expect(two).toHaveBeenCalledTimes(1);
  });

  it('revalidateSwrKey makes an unmounted key refetch on its next mount, inside the window', async () => {
    const fetcher = vi.fn(async () => 'a1');
    (await mountKey(key, fetcher)).unmount();

    revalidateSwrKey(key);
    await settle(1_000);
    await mountKey(key, fetcher);

    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  // Reads `data` while rendering, so SWR re-renders when it changes.
  const watchData = (fetcher: () => Promise<string>) =>
    renderHook(() => useSWR(key, fetcher, SWR_POLICY.list60s).data);

  it('writeSwrKey shows the written data without a request', async () => {
    const fetcher = vi.fn(async () => 'from server');
    const view = watchData(fetcher);
    await settle();
    expect(view.result.current).toBe('from server');

    act(() => writeSwrKey(key, 'edited here'));
    await settle();

    expect(view.result.current).toBe('edited here');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('writeSwrKey wins over a response that was already in flight', async () => {
    let answer: (value: string) => void = () => {};
    const fetcher = vi.fn(() => new Promise<string>((resolve) => { answer = resolve; }));
    const view = watchData(fetcher);
    await settle();

    act(() => writeSwrKey(key, 'edited here'));
    await act(async () => { answer('older server copy'); });
    await settle();

    expect(view.result.current).toBe('edited here');
  });

  it('a written "gone" value is what the next mount inside the window shows, with no request', async () => {
    const fetcher = vi.fn(async () => 'from server');
    const first = watchData(fetcher);
    await settle();
    first.unmount();

    writeSwrKey(key, 'gone');
    const next = watchData(fetcher);
    await settle();

    expect(next.result.current).toBe('gone');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('isSwrReadUnavailable', () => {
  const failure = (statusCode: number) => ({ type: ErrorType.SERVER_ERROR, message: 'x', statusCode });

  it('is false without an error', () => {
    expect(isSwrReadUnavailable(false, undefined)).toBe(false);
    expect(isSwrReadUnavailable(true, undefined)).toBe(false);
  });

  it('is true for any error before anything was loaded', () => {
    expect(isSwrReadUnavailable(false, failure(500))).toBe(true);
    expect(isSwrReadUnavailable(false, new Error('network'))).toBe(true);
  });

  it('keeps data already shown through a server or network failure', () => {
    expect(isSwrReadUnavailable(true, failure(500))).toBe(false);
    expect(isSwrReadUnavailable(true, new Error('network'))).toBe(false);
  });

  it('is true when the thing is gone or forbidden, even with data shown', () => {
    expect(isSwrReadUnavailable(true, failure(404))).toBe(true);
    expect(isSwrReadUnavailable(true, failure(403))).toBe(true);
  });
});
