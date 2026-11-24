import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProcessedError } from '@/lib/api';

const get = vi.fn();
vi.mock('@/lib/api', () => ({ apiClient: { get: (...args: unknown[]) => get(...args) } }));

import { BUSY_RETRIES, KnowledgeHubApi } from '../api';

const error = (statusCode: number) => ({ statusCode, message: 'x' }) as ProcessedError;

function suppress(): (e: ProcessedError) => boolean {
  const predicate = get.mock.calls[0][1].suppressErrorToast;
  expect(typeof predicate).toBe('function');
  return predicate;
}

describe('All Records listing refusals', () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: {} });
  });

  it('the root listing leaves a 422 or 503 to the table, not a toast', async () => {
    await KnowledgeHubApi.getAllRootItems({});
    const quiet = suppress();
    expect([quiet(error(422)), quiet(error(503)), quiet(error(500))]).toEqual([true, true, false]);
  });

  it('a drilled-down listing for the table does the same', async () => {
    await KnowledgeHubApi.loadFolderData('app', 'a1', {}, { quietRefusals: true });
    const quiet = suppress();
    expect([quiet(error(422)), quiet(error(503)), quiet(error(500))]).toEqual([true, true, false]);
  });

  it('does not ask a 503 again itself: the table counts it down on screen', async () => {
    get.mockReset();
    get.mockRejectedValue(error(503));
    await expect(KnowledgeHubApi.loadFolderData('app', 'a1', {}, { quietRefusals: true })).rejects.toMatchObject({ statusCode: 503 });
    expect(get).toHaveBeenCalledTimes(1);
  });
});

describe('Collections and sidebar listings answered 503', () => {
  const busy = (retryAfter?: string) =>
    ({ statusCode: 503, message: 'x', originalError: { response: { headers: retryAfter ? { 'retry-after': retryAfter } : {} } } }) as unknown as ProcessedError;

  beforeEach(() => {
    get.mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    ['a folder read', () => KnowledgeHubApi.loadFolderData('app', 'a1', {})],
    ['the sidebar root', () => KnowledgeHubApi.getNavigationNodes({})],
    ['a sidebar folder', () => KnowledgeHubApi.getNodeChildren('folder', 'f1')],
  ])('%s is asked again quietly after the seconds the server asked for', async (_name, read) => {
    get.mockRejectedValueOnce(busy('2')).mockResolvedValueOnce({ data: { items: [] } });
    const answer = read();
    await vi.advanceTimersByTimeAsync(1999);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0][1].suppressErrorToast(error(503))).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    await expect(answer).resolves.toBeDefined();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('stops after a few tries, and only the last can toast the 503', async () => {
    get.mockRejectedValue(busy());
    const answer = KnowledgeHubApi.loadFolderData('app', 'a1', {});
    const settled = expect(answer).rejects.toMatchObject({ statusCode: 503 });
    await vi.advanceTimersByTimeAsync(BUSY_RETRIES * 5000);
    await settled;
    expect(get).toHaveBeenCalledTimes(BUSY_RETRIES + 1);
    const last = get.mock.calls[BUSY_RETRIES][1].suppressErrorToast;
    expect(typeof last === 'function' ? last(error(503)) : !!last).toBe(false);
  });

  it('a read cancelled while it waits settles at once, without waiting out the delay', async () => {
    const cancelled = { type: 'REQUEST_CANCELLED', message: 'Request was cancelled.' };
    get.mockImplementation(async (_url: string, config: { signal?: AbortSignal }) => {
      throw config.signal?.aborted ? cancelled : busy('30');
    });
    const reading = new AbortController();
    const answer = KnowledgeHubApi.loadFolderData('app', 'a1', {}, { signal: reading.signal });
    const settled = expect(answer).rejects.toBe(cancelled);
    await vi.advanceTimersByTimeAsync(1000);
    reading.abort();
    await vi.advanceTimersByTimeAsync(0);
    await settled;
    expect(get).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not ask again after any other failure', async () => {
    get.mockRejectedValue(error(500));
    await expect(KnowledgeHubApi.loadFolderData('app', 'a1', {})).rejects.toMatchObject({ statusCode: 500 });
    expect(get).toHaveBeenCalledTimes(1);
  });
});
