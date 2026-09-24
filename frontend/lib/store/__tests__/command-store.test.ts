import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('command-store', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('dispatches to a registered handler', async () => {
    const { useCommandStore } = await import('../command-store');
    const handler = vi.fn();
    useCommandStore.getState().register('newChat', handler);
    expect(useCommandStore.getState().dispatch('newChat')).toBe(true);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('queues a command until a handler registers', async () => {
    const { useCommandStore } = await import('../command-store');
    const handler = vi.fn();
    expect(useCommandStore.getState().dispatch('newChat')).toBe(false);
    expect(handler).not.toHaveBeenCalled();
    useCommandStore.getState().register('newChat', handler);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
