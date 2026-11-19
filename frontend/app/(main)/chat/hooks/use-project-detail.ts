'use client';

import useSWR from 'swr';
import { isSwrReadUnavailable, SWR_POLICY } from '@/lib/api/swr-cache';
import { isGoneError } from '@/lib/api/api-error';
import { ProjectApi, projectDetailKey } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';

export interface ProjectDetailState {
  /** The project to show; null while loading or when it cannot be shown. */
  project: ProjectDetail | null;
  /** Nothing to show: it failed before loading, or the project is gone. */
  unavailable: boolean;
  /** Deleted, left, or not this user's to open (403/404). */
  gone: boolean;
}

/**
 * One project's detail, shared by every reader of the same id (one request).
 * Callers show their own error, so the client's toast is suppressed.
 */
export function useProjectDetail(projectId: string | null): ProjectDetailState {
  // `null` data is what a delete or leave in this tab wrote (see `ProjectApi`).
  const { data, error } = useSWR<ProjectDetail | null>(
    projectId ? projectDetailKey(projectId) : null,
    ([, id]: ReturnType<typeof projectDetailKey>) => ProjectApi.get(id, { suppressErrorToast: true }),
    SWR_POLICY.list60s,
  );
  const gone = Boolean(projectId) && (data === null || isGoneError(error));
  const unavailable = gone || isSwrReadUnavailable(Boolean(data), error);
  return { project: unavailable ? null : data ?? null, unavailable, gone };
}
