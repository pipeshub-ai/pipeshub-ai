import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useImageSettings, DEFAULT_IMAGE_SETTINGS } from '../use-image-settings';
import type { WebSearchSettings } from '../types';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const SAVED: WebSearchSettings = { includeImages: true, maxImages: 7 };
const STALE: WebSearchSettings = { includeImages: false, maxImages: 3 };

function setup() {
  const put = deferred<WebSearchSettings>();
  const save = vi.fn(() => put.promise);
  const onSaved = vi.fn();
  const onFailed = vi.fn();
  const hook = renderHook(() => useImageSettings({ save, onSaved, onFailed }));
  return { hook, put, save, onSaved, onFailed };
}

describe('useImageSettings — a reload never puts older settings back over a save', () => {
  it('a load with no save in between applies its settings', () => {
    const { hook } = setup();
    const startedAt = hook.result.current.beginLoad();
    act(() => hook.result.current.applyLoaded(SAVED, startedAt));
    expect(hook.result.current.settings).toEqual(SAVED);
  });

  it('a load that began before a save is ignored when it returns during the save', async () => {
    const { hook, put } = setup();
    const startedAt = hook.result.current.beginLoad();
    let saving!: Promise<void>;
    act(() => {
      saving = hook.result.current.update(SAVED, 'saved');
    });
    act(() => hook.result.current.applyLoaded(STALE, startedAt));
    expect(hook.result.current.settings).toEqual(SAVED);
    await act(async () => {
      put.resolve(SAVED);
      await saving;
    });
    expect(hook.result.current.settings).toEqual(SAVED);
  });

  it('a load that began during a save and returns after it is ignored', async () => {
    const { hook, put } = setup();
    let saving!: Promise<void>;
    act(() => {
      saving = hook.result.current.update(SAVED, 'saved');
    });
    const startedAt = hook.result.current.beginLoad(); // GET sent while the PUT is in flight
    await act(async () => {
      put.resolve(SAVED);
      await saving;
    });
    act(() => hook.result.current.applyLoaded(STALE, startedAt)); // GET answers with the old settings
    expect(hook.result.current.settings).toEqual(SAVED);
  });

  it('a load that begins and ends while a save is pending is ignored: the switch never flickers back', async () => {
    const { hook, put } = setup();
    let saving!: Promise<void>;
    act(() => {
      saving = hook.result.current.update(SAVED, 'saved');
    });
    const startedAt = hook.result.current.beginLoad();
    act(() => hook.result.current.applyLoaded(STALE, startedAt));
    expect(hook.result.current.settings).toEqual(SAVED);
    await act(async () => {
      put.resolve(SAVED);
      await saving;
    });
    expect(hook.result.current.settings).toEqual(SAVED);
  });

  it('a load that began after the save settled applies again', async () => {
    const { hook, put } = setup();
    let saving!: Promise<void>;
    act(() => {
      saving = hook.result.current.update(SAVED, 'saved');
    });
    await act(async () => {
      put.resolve(SAVED);
      await saving;
    });
    const fresh: WebSearchSettings = { includeImages: true, maxImages: 9 };
    const startedAt = hook.result.current.beginLoad();
    act(() => hook.result.current.applyLoaded(fresh, startedAt));
    expect(hook.result.current.settings).toEqual(fresh);
  });
});

describe('useImageSettings — a failed read', () => {
  it('nothing is editable until a read succeeds', () => {
    const { hook } = setup();
    expect(hook.result.current.hasLoaded).toBe(false);
    act(() => hook.result.current.applyLoaded(undefined, hook.result.current.beginLoad()));
    expect(hook.result.current.hasLoaded).toBe(false);
    act(() => hook.result.current.applyLoaded(SAVED, hook.result.current.beginLoad()));
    expect(hook.result.current.hasLoaded).toBe(true);
  });

  it('a failed refresh keeps the last confirmed settings, never defaults', () => {
    const { hook } = setup();
    act(() => hook.result.current.applyLoaded(SAVED, hook.result.current.beginLoad()));
    act(() => hook.result.current.applyLoaded(undefined, hook.result.current.beginLoad()));
    expect(hook.result.current.settings).toEqual(SAVED);
    expect(hook.result.current.hasLoaded).toBe(true);
  });
});

describe('useImageSettings — saving', () => {
  it('shows the change at once, keeps what the server returned, and reports success', async () => {
    const { hook, put, onSaved } = setup();
    let saving!: Promise<void>;
    act(() => {
      saving = hook.result.current.update(SAVED, 'Images will be sent');
    });
    expect(hook.result.current.settings).toEqual(SAVED);
    expect(hook.result.current.isSaving).toBe(true);
    await act(async () => {
      put.resolve({ includeImages: true, maxImages: 50 });
      await saving;
    });
    expect(hook.result.current.settings).toEqual({ includeImages: true, maxImages: 50 });
    expect(hook.result.current.isSaving).toBe(false);
    expect(onSaved).toHaveBeenCalledWith('Images will be sent');
  });

  it('a failed save puts the previous settings back and reports the error', async () => {
    const { hook, put, onFailed } = setup();
    let saving!: Promise<void>;
    act(() => {
      saving = hook.result.current.update(SAVED, 'saved');
    });
    await act(async () => {
      put.reject(new Error('Forbidden'));
      await saving;
    });
    expect(hook.result.current.settings).toEqual(DEFAULT_IMAGE_SETTINGS);
    expect(hook.result.current.isSaving).toBe(false);
    expect(onFailed).toHaveBeenCalledWith(new Error('Forbidden'));
  });
});
