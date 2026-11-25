'use client';

import { useCallback } from 'react';
import { useDurableSession, useFollowsDurableRun, type DurableSessionRoute } from '../../../hooks/use-run-timeline';
import type { MessagePart } from '../../../types';

/**
 * Which of a turn's parts is a background job (CLASSIC-JOBS D2): a durable child run, named by its parent or seen on a
 * durable run's stream. The one rule both the steps (which leave a job out) and the jobs after them (which draw it)
 * use, so a job is drawn once, after the steps, as the design draws it (BGUI-2), and never twice.
 */
export function isJobPart(part: MessagePart, session: DurableSessionRoute | null, followsDurable: boolean): boolean {
  return part.type === 'sub_agent' && session !== null && !!part.runId && (part.parentRunId !== undefined || followsDurable);
}

export function useIsJobPart(): (part: MessagePart) => boolean {
  const session = useDurableSession();
  const followsDurable = useFollowsDurableRun();
  const sessionId = session?.sessionId ?? null;
  return useCallback(
    (part: MessagePart) => isJobPart(part, sessionId ? { sessionId } : null, followsDurable),
    [sessionId, followsDurable],
  );
}
