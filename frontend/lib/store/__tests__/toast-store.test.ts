import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { toast, useToastStore } from '../toast-store';

const find = (id: string) => useToastStore.getState().toasts.find((t) => t.id === id);

describe('toast updates', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('a loading toast turned into an error with a duration can be closed and goes away', () => {
    const id = toast.loading('Checking…');
    expect(find(id)?.showCloseButton).toBe(false);

    toast.update(id, { variant: 'error', title: 'Check failed', duration: 6000 });
    expect(find(id)?.showCloseButton).toBe(true);
    expect(find(id)?.duration).toBe(6000);

    vi.advanceTimersByTime(6000 + 300);
    expect(find(id)).toBeUndefined();
  });

  it('a variant change without a duration uses the variant default', () => {
    const id = toast.loading('Saving…');
    toast.update(id, { variant: 'success', title: 'Saved' });
    expect(find(id)?.showCloseButton).toBe(true);
    expect(find(id)?.duration).toBeGreaterThan(0);
  });

  it('a timed toast switched back to loading is not removed by its old timer', () => {
    const id = toast.error('Check failed', { duration: 6000 });
    toast.update(id, { variant: 'loading', title: 'Retrying…' });
    vi.advanceTimersByTime(60_000);
    expect(find(id)?.title).toBe('Retrying…');
  });

  it('a title-only update keeps a loading toast open', () => {
    const id = toast.loading('Checking 1 of 3');
    toast.update(id, { title: 'Checking 2 of 3' });
    vi.advanceTimersByTime(60_000);
    expect(find(id)?.title).toBe('Checking 2 of 3');
    expect(find(id)?.showCloseButton).toBe(false);
  });
});
