import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from '@/lib/api';
import { ErrorType } from '@/lib/api/api-error';
import { clearSessionCache } from '@/lib/api/session-cache';
import { ProjectApi } from '@/chat/project-api';
import { useProjectDetail } from '../use-project-detail';

const api = vi.mocked(apiClient);
const PROJECT = { _id: 'p1', name: 'Launch plan', color: '#f00', role: 'owner' };
const projectGets = (id = 'p1') => api.get.mock.calls.filter(([url]) => url === `/api/v1/projects/${id}`).length;
const failure = (statusCode: number) => ({ type: ErrorType.SERVER_ERROR, message: 'x', statusCode });

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const mountReader = (projectId: string | null = 'p1') => renderHook(() => ({ ...useProjectDetail(projectId) }));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  clearSessionCache();
  api.get.mockResolvedValue({ data: { project: PROJECT } });
  api.post.mockResolvedValue({ data: { project: PROJECT } });
  api.delete.mockResolvedValue({ data: { removed: true } });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('useProjectDetail', () => {
  it('makes one quiet request for the chat page and the project sidebar together', async () => {
    const page = mountReader();
    const sidebar = mountReader();
    await settle();

    expect(projectGets()).toBe(1);
    expect(api.get).toHaveBeenCalledWith('/api/v1/projects/p1', { suppressErrorToast: true });
    expect(page.result.current.project?.name).toBe('Launch plan');
    expect(sidebar.result.current.project?.name).toBe('Launch plan');
  });

  it('reuses the project for a remount within 60 s', async () => {
    mountReader().unmount();
    await settle(30_000);
    mountReader();
    await settle();

    expect(projectGets()).toBe(1);
  });

  it('makes no request without a project', async () => {
    const view = mountReader(null);
    await settle();

    expect(api.get).not.toHaveBeenCalled();
    expect(view.result.current).toEqual({ project: null, unavailable: false, gone: false });
  });

  it.each(['pin', 'unpin', 'archive', 'unarchive'] as const)('refetches that project only after %s', async (action) => {
    mountReader('p1');
    mountReader('p2');
    await settle();

    await act(async () => {
      await ProjectApi[action]('p1');
    });
    await settle();

    expect(projectGets('p1')).toBe(2);
    expect(projectGets('p2')).toBe(1);
  });

  it('does not refetch after a failed pin', async () => {
    mountReader();
    await settle();
    api.post.mockRejectedValueOnce(new Error('500'));

    await act(async () => {
      await expect(ProjectApi.pin('p1')).rejects.toThrow('500');
    });
    await settle();

    expect(projectGets()).toBe(1);
  });

  it('does not refetch after an update: the workspace writes its own copy', async () => {
    mountReader();
    await settle();
    api.patch.mockResolvedValue({ data: { project: PROJECT } });

    await act(async () => {
      await ProjectApi.update('p1', { name: 'Renamed' });
    });
    await settle();

    expect(projectGets()).toBe(1);
  });

  it.each([
    ['deleting it', () => ProjectApi.remove('p1')],
    ['leaving it', () => ProjectApi.leave('p1', 'me')],
  ])('shows the project as gone after %s, with no request, now and on the next mount', async (_name, write) => {
    const open = mountReader();
    await settle();

    await act(async () => {
      await write();
    });
    await settle();
    expect(open.result.current).toMatchObject({ project: null, unavailable: true, gone: true });
    open.unmount();

    const back = mountReader();
    await settle();
    expect(back.result.current.gone).toBe(true);
    expect(projectGets()).toBe(1);
  });

  it('keeps the project after removing someone else from it', async () => {
    const open = mountReader();
    await settle();

    await act(async () => {
      await ProjectApi.removeMember('p1', 'someone-else');
    });
    await settle();

    expect(open.result.current.project?.name).toBe('Launch plan');
  });

  it('keeps the project when leaving was refused (membership through a team)', async () => {
    const open = mountReader();
    await settle();
    api.delete.mockResolvedValueOnce({ data: { removed: false } });

    await act(async () => {
      await ProjectApi.leave('p1', 'me');
    });
    await settle();

    expect(open.result.current.project?.name).toBe('Launch plan');
  });

  it('keeps the project shown when a later refresh fails, and drops it on a 404', async () => {
    mountReader().unmount();
    await settle(61_000);

    api.get.mockRejectedValueOnce(failure(500));
    const afterServerError = mountReader();
    await settle();
    expect(afterServerError.result.current).toMatchObject({ unavailable: false, gone: false });
    expect(afterServerError.result.current.project?.name).toBe('Launch plan');
    afterServerError.unmount();

    api.get.mockRejectedValueOnce(failure(404));
    const afterNotFound = mountReader();
    await settle();
    expect(afterNotFound.result.current).toMatchObject({ project: null, unavailable: true, gone: true });
  });

  it('reports a failure before anything loaded as unavailable but not gone', async () => {
    api.get.mockRejectedValueOnce(new Error('network'));
    const view = mountReader();
    await settle();

    expect(view.result.current).toEqual({ project: null, unavailable: true, gone: false });
  });

  it('starts over after sign-out', async () => {
    mountReader().unmount();
    await settle();

    clearSessionCache();
    mountReader();
    await settle();

    expect(projectGets()).toBe(2);
  });
});
