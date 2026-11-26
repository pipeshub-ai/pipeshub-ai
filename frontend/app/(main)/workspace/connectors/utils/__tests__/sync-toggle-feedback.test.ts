/**
 * Feedback while a connector's sync turns on or off: the pending flag the card reads,
 * and the toast that appears for a slow request and then reports how it ended.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { TFunction } from 'i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { useConnectorsStore } from '../../store';
import { runSyncToggleWithFeedback, type SyncToggleResult } from '../sync-toggle-feedback';

const t = ((key: string, opts?: Record<string, unknown>) =>
  opts ? `${key}|${JSON.stringify(opts)}` : key) as unknown as TFunction;

/** Toasts on screen: a removed toast stays in the list while its exit animation runs. */
const toasts = () => useToastStore.getState().toasts.filter((x) => !x.isExiting);
const pending = () => useConnectorsStore.getState().syncTogglePending;

function after<T>(ms: number, value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}
function failAfter(ms: number, error: unknown): Promise<never> {
  return new Promise((_, reject) => setTimeout(() => reject(error), ms));
}

const base = { connectorId: 'c1', connectorName: 'Team wiki', sourceName: 'BookStack', t };

describe('runSyncToggleWithFeedback', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useConnectorsStore.getState().reset();
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('fast success shows only the success toast', async () => {
    const run = runSyncToggleWithFeedback({ ...base, direction: 'on', action: () => after(100, 'done' as const) });
    expect(pending().c1).toBe('on');
    await vi.advanceTimersByTimeAsync(100);
    await expect(run).resolves.toBe('done');

    expect(toasts().map((x) => x.variant)).toEqual(['success']);
    expect(toasts()[0].title).toBe('workspace.connectors.toasts.syncTurnedOn|{"name":"Team wiki"}');
    expect('c1' in pending()).toBe(false);
  });

  it('slow success turns the progress toast into success', async () => {
    const run = runSyncToggleWithFeedback({ ...base, direction: 'on', action: () => after(2000, 'done' as const) });

    await vi.advanceTimersByTimeAsync(499);
    expect(toasts()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(toasts()).toHaveLength(1);
    const progress = toasts()[0];
    expect(progress.variant).toBe('loading');
    expect(progress.title).toBe('workspace.connectors.toasts.syncTurningOn|{"name":"Team wiki"}');
    expect(progress.description).toBe('workspace.connectors.toasts.syncTurningOnDescription|{"source":"BookStack"}');

    await vi.advanceTimersByTimeAsync(1500);
    await expect(run).resolves.toBe('done');
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].id).toBe(progress.id);
    expect(toasts()[0].variant).toBe('success');
    expect(toasts()[0].title).toBe('workspace.connectors.toasts.syncTurnedOn|{"name":"Team wiki"}');
    expect(toasts()[0].description).toBeUndefined();
    expect('c1' in pending()).toBe(false);
  });

  it('failure after progress shows the error in place', async () => {
    const run = runSyncToggleWithFeedback({ ...base, direction: 'on', action: () => failAfter(2000, new Error('Check the API token.')) });
    await vi.advanceTimersByTimeAsync(2000);
    await expect(run).resolves.toBe('failed');

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].variant).toBe('error');
    expect(toasts()[0].title).toBe('workspace.connectors.toasts.syncToggleFailed|{"name":"Team wiki"}');
    expect(toasts()[0].description).toBe('Check the API token.');
    expect('c1' in pending()).toBe(false);
  });

  it('fast failure shows one error toast', async () => {
    const run = runSyncToggleWithFeedback({ ...base, direction: 'on', action: () => failAfter(50, new Error('Check the API token.')) });
    await vi.advanceTimersByTimeAsync(50);
    await expect(run).resolves.toBe('failed');
    expect(toasts().map((x) => x.variant)).toEqual(['error']);
  });

  it('an error the API client already reported removes the progress toast', async () => {
    const processed = { type: 'SERVER_ERROR', message: 'Failed to initialize connector.' };
    const run = runSyncToggleWithFeedback({ ...base, direction: 'on', action: () => failAfter(2000, processed) });
    await vi.advanceTimersByTimeAsync(2000);
    await expect(run).resolves.toBe('failed');
    expect(toasts()).toHaveLength(0);
    expect('c1' in pending()).toBe(false);
  });

  it('a handled outcome removes the progress toast', async () => {
    const run = runSyncToggleWithFeedback({ ...base, direction: 'on', action: () => after(2000, 'handled' as SyncToggleResult) });
    await vi.advanceTimersByTimeAsync(2000);
    await expect(run).resolves.toBe('handled');
    expect(toasts()).toHaveLength(0);
    expect('c1' in pending()).toBe(false);
  });

  it('a second call while pending is skipped', async () => {
    const action = vi.fn(() => after(1000, 'done' as const));
    const first = runSyncToggleWithFeedback({ ...base, direction: 'on', action });
    const second = runSyncToggleWithFeedback({ ...base, direction: 'on', action });
    await expect(second).resolves.toBe('skipped');
    await vi.advanceTimersByTimeAsync(1000);
    await expect(first).resolves.toBe('done');
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('off direction uses turning-off copy', async () => {
    const run = runSyncToggleWithFeedback({ ...base, direction: 'off', action: () => after(1000, 'done' as const) });
    expect(pending().c1).toBe('off');
    await vi.advanceTimersByTimeAsync(500);
    expect(toasts()[0].title).toBe('workspace.connectors.toasts.syncTurningOff|{"name":"Team wiki"}');
    expect(toasts()[0].description).toBeUndefined();
    await vi.advanceTimersByTimeAsync(500);
    await run;
    expect(toasts()[0].title).toBe('workspace.connectors.toasts.syncTurnedOff|{"name":"Team wiki"}');
  });

  it('a success toast override replaces the default copy', async () => {
    const run = runSyncToggleWithFeedback({
      ...base,
      direction: 'on',
      action: () => after(2000, 'done' as const),
      successToast: { title: 'Sync started', description: 'Records appear as they sync.' },
    });
    await vi.advanceTimersByTimeAsync(2000);
    await run;
    expect(toasts()[0].variant).toBe('success');
    expect(toasts()[0].title).toBe('Sync started');
    expect(toasts()[0].description).toBe('Records appear as they sync.');
  });
});
