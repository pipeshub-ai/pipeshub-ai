/**
 * Unit tests for `useKbStreamUpload` — per-file SSE event mapping onto
 * `useUploadStore`, the pre-fail path when `getKbId` rejects, and the
 * `finalizeSession` safety net on a mid-stream error.
 *
 * Renders through a bare `react-dom/client` root rather than
 * `@testing-library/react`'s `renderHook` — `@testing-library/dom` (a
 * transitive dep of `@testing-library/react`) is missing from this
 * environment's `node_modules` (pre-existing gap, unrelated to this hook).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { useUploadStore } from '@/lib/store/upload-store';

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));

interface FakeEvent {
  event: string;
  data: Record<string, unknown>;
}

let scriptedEvents: FakeEvent[] = [];
let scriptedError: Error | null = null;

vi.mock('@/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    streamUpload: vi.fn(
      async (
        _kbId: string,
        _folderId: string | null,
        _files: File[],
        _filesMetadata: unknown,
        options: { onEvent: (e: FakeEvent) => void; onError: (e: Error) => void },
      ) => {
        for (const evt of scriptedEvents) options.onEvent(evt);
        if (scriptedError) {
          options.onError(scriptedError);
          throw scriptedError;
        }
      },
    ),
  },
}));

import { useKbStreamUpload } from '../use-kb-stream-upload';

function makeFile(name: string): File {
  return new File(['content'], name, { type: 'text/plain' });
}

/** Minimal `renderHook` replacement — mounts a bare component and captures the hook's return value. */
function mountHook<T>(useHook: () => T): { current: T } {
  const resultRef: { current: T | undefined } = { current: undefined };
  function Harness() {
    const value = useHook();
    // eslint-disable-next-line react-hooks/immutability -- test-only `renderHook` stand-in (see file docblock); writes the hook's value out for assertions, not a real render-time mutation.
    resultRef.current = value;
    return null;
  }
  const container = document.createElement('div');
  document.body.appendChild(container);
  act(() => {
    const root: Root = createRoot(container);
    root.render(React.createElement(Harness));
  });
  return resultRef as { current: T };
}

beforeEach(() => {
  useUploadStore.getState().clearAll();
  scriptedEvents = [];
  scriptedError = null;
  vi.clearAllMocks();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('useKbStreamUpload', () => {
  it('maps file:succeeded + done onto a completed store row', async () => {
    scriptedEvents = [
      { event: 'file:succeeded', data: { filePath: 'a.txt' } },
      { event: 'done', data: {} },
    ];
    const getKbId = vi.fn().mockResolvedValue('kb1');
    const onUploaded = vi.fn();
    const hook = mountHook(() => useKbStreamUpload({ getKbId, onUploaded }));

    await act(async () => {
      await hook.current.uploadFiles([makeFile('a.txt')]);
    });

    const items = useUploadStore.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0]!.status).toBe('completed');
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });

  it('maps file:failed with server errors onto a failed store row', async () => {
    scriptedEvents = [
      { event: 'file:failed', data: { filePath: 'bad.txt', errors: ['Unsupported file type'] } },
      { event: 'done', data: {} },
    ];
    const getKbId = vi.fn().mockResolvedValue('kb1');
    const hook = mountHook(() => useKbStreamUpload({ getKbId }));

    await act(async () => {
      await hook.current.uploadFiles([makeFile('bad.txt')]);
    });

    const items = useUploadStore.getState().items;
    expect(items[0]!.status).toBe('failed');
    expect(items[0]!.errors).toEqual(['Unsupported file type']);
  });

  it('fails every file immediately, without calling streamUpload, when getKbId rejects', async () => {
    const { KnowledgeBaseApi } = await import('@/knowledge-base/api');
    const getKbId = vi.fn().mockRejectedValue(new Error('ensure failed'));
    const hook = mountHook(() => useKbStreamUpload({ getKbId }));

    await act(async () => {
      await hook.current.uploadFiles([makeFile('a.txt'), makeFile('b.txt')]);
    });

    const items = useUploadStore.getState().items;
    expect(items).toHaveLength(2);
    expect(items.every((i) => i.status === 'failed')).toBe(true);
    expect(KnowledgeBaseApi.streamUpload).not.toHaveBeenCalled();
  });

  it('fails rows still in flight when the stream throws mid-batch (finalizeSession safety net)', async () => {
    // No terminal event scripted for this row — only a stream-level error.
    scriptedEvents = [];
    scriptedError = new Error('connection dropped');
    const getKbId = vi.fn().mockResolvedValue('kb1');
    const hook = mountHook(() => useKbStreamUpload({ getKbId }));

    await act(async () => {
      await hook.current.uploadFiles([makeFile('a.txt')]);
    });

    const items = useUploadStore.getState().items;
    expect(items[0]!.status).toBe('failed');
    expect(items[0]!.errors?.[0]).toContain('connection dropped');
  });

  it('is a no-op for an empty file list', async () => {
    const getKbId = vi.fn();
    const hook = mountHook(() => useKbStreamUpload({ getKbId }));

    await act(async () => {
      await hook.current.uploadFiles([]);
    });

    expect(getKbId).not.toHaveBeenCalled();
    expect(useUploadStore.getState().items).toHaveLength(0);
  });
});
