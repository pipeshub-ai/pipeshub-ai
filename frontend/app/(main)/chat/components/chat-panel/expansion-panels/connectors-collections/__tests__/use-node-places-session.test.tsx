/**
 * Where a selected node sits in the tree is looked up once and kept with the
 * chat store. At session end both are forgotten, so the next person (maybe
 * sharing the same collections) looks the places up again.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const hub = vi.hoisted(() => ({ getNodeChildren: vi.fn() }));
vi.mock('@/app/(main)/knowledge-base/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/knowledge-base/api')>()),
  KnowledgeHubApi: hub,
}));

import { clearSessionCache } from '@/lib/api/session-cache';
import { useNodePlaces } from '../knowledge-tree';

const node = [{ id: 'folder-1', nodeType: 'folder' }];

beforeEach(() => {
  clearSessionCache();
  hub.getNodeChildren.mockReset().mockResolvedValue({
    breadcrumbs: [
      { id: 'kb-1', name: 'Finance', nodeType: 'kb' },
      { id: 'folder-1', name: 'Q3', nodeType: 'folder' },
    ],
  });
});

describe('node places at session end', () => {
  it('a node looked up in the last session is looked up again in the next one', async () => {
    const first = renderHook(() => useNodePlaces(node));
    await act(async () => {});
    expect(hub.getNodeChildren).toHaveBeenCalledTimes(1);
    first.unmount();

    // Same session: the place is known, so no second lookup.
    renderHook(() => useNodePlaces(node)).unmount();
    await act(async () => {});
    expect(hub.getNodeChildren).toHaveBeenCalledTimes(1);

    clearSessionCache();
    renderHook(() => useNodePlaces(node));
    await act(async () => {});

    expect(hub.getNodeChildren).toHaveBeenCalledTimes(2);
  });
});
