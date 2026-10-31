import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({ getMyToolsets: vi.fn() }));
vi.mock('@/app/(main)/toolsets/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/toolsets/api')>()),
  ToolsetsApi: api,
}));

import { useToolsetTypeInstanceList } from '../hooks/use-toolset-type-instance-list';

type Res = Awaited<ReturnType<typeof import('@/app/(main)/toolsets/api').ToolsetsApi.getMyToolsets>>;

function deferred() {
  let resolve!: (v: Res) => void;
  const promise = new Promise<Res>((r) => (resolve = r));
  return { promise, resolve };
}

function page(type: string, names: string[]): Res {
  return {
    toolsets: names.map((n) => ({ instanceId: n, instanceName: n, toolsetType: type }) as BuilderSidebarToolset),
    pagination: { page: 1, limit: 20, total: names.length, totalPages: 1, hasNext: false, hasPrev: false },
    filterCounts: { all: names.length, authenticated: 1, notAuthenticated: names.length - 1 },
  } as Res;
}

function setup() {
  return renderHook(
    ({ type }: { type: string }) =>
      useToolsetTypeInstanceList({ toolsetTypeParam: type, instanceFilterTab: 'all', refreshKey: 0, enabled: true }),
    { initialProps: { type: 'calendar' } }
  );
}

afterEach(() => {
  api.getMyToolsets.mockReset();
});

describe('useToolsetTypeInstanceList', () => {
  it('does not show the previous toolset type while the new one loads', async () => {
    const calendar = deferred();
    const drive = deferred();
    api.getMyToolsets.mockImplementation(({ toolsetType }: { toolsetType: string }) =>
      toolsetType === 'calendar' ? calendar.promise : drive.promise
    );
    const { result, rerender } = setup();
    await act(async () => calendar.resolve(page('calendar', ['Calendar', 'Calendar Harshit'])));
    expect(result.current.typeListInstances.map((i) => i.instanceName)).toEqual(['Calendar', 'Calendar Harshit']);
    act(() => result.current.setInstanceSearch('cal'));

    rerender({ type: 'drive' });
    expect(result.current.typeListInstances).toEqual([]);
    expect(result.current.typeListFilterCounts).toBeNull();
    expect(result.current.typeListPagination).toBeNull();
    expect(result.current.typeListLoading).toBe(true);
    expect(result.current.instanceSearch).toBe('');

    await act(async () => drive.resolve(page('drive', ['Drive'])));
    expect(result.current.typeListInstances.map((i) => i.instanceName)).toEqual(['Drive']);
    expect(result.current.typeListFilterCounts?.all).toBe(1);
    expect(result.current.typeListLoading).toBe(false);
  });

  it('ignores a late response for the previous toolset type', async () => {
    const calendar = deferred();
    const drive = deferred();
    api.getMyToolsets.mockImplementation(({ toolsetType }: { toolsetType: string }) =>
      toolsetType === 'calendar' ? calendar.promise : drive.promise
    );
    const { result, rerender } = setup();
    rerender({ type: 'drive' });
    await act(async () => drive.resolve(page('drive', ['Drive'])));
    await act(async () => calendar.resolve(page('calendar', ['Calendar'])));
    await waitFor(() => expect(result.current.typeListLoading).toBe(false));
    expect(result.current.typeListInstances.map((i) => i.instanceName)).toEqual(['Drive']);
    expect(result.current.typeListFilterCounts?.all).toBe(1);
  });

  it('shows the empty state, not the previous type, when the new type fails to load', async () => {
    api.getMyToolsets.mockImplementation(({ toolsetType }: { toolsetType: string }) =>
      toolsetType === 'calendar' ? Promise.resolve(page('calendar', ['Calendar'])) : Promise.reject(new Error('boom'))
    );
    const { result, rerender } = setup();
    await waitFor(() => expect(result.current.typeListInstances).toHaveLength(1));
    rerender({ type: 'drive' });
    await waitFor(() => expect(result.current.typeListLoading).toBe(false));
    expect(result.current.typeListInstances).toEqual([]);
    expect(result.current.typeListFilterCounts).toBeNull();
  });
});
