'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store';
import { ChatApi } from '../api';
import type { RunTimelineView } from '../types';
import { useThreadIsActive } from './use-thread-owner';

export interface DurableSessionRoute {
  agentId?: string;
  sessionId: string;
}

/**
 * The active conversation's durable session routes, when it has a server id. None inside a thread that still draws
 * the previous chat (FIX-NAV): its runs are not this session's.
 */
export function useDurableSession(): DurableSessionRoute | null {
  const current = useThreadIsActive();
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  const agentId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.threadAgentId ?? null) : null));
  if (!convId || !current) return null;
  return { ...(agentId ? { agentId } : {}), sessionId: convId };
}

/** True while the active conversation follows a durable run (the live stream is Node's projection). */
export function useFollowsDurableRun(): boolean {
  return useChatStore((s) => (s.activeSlotId ? Boolean(s.slots[s.activeSlotId]?.durableRun) : false));
}

export interface RunTimelineState {
  timeline: RunTimelineView | null;
  failed: boolean;
  reload: () => Promise<void>;
}

/**
 * One durable run's timeline (U3), read when `enabled`; `refreshKey` re-reads it (e.g. when the live card says the
 * task ended). Never on a timer: a running job's steps come from its own stream (CLASSIC-JOBS D13).
 */
export function useRunTimeline(
  session: DurableSessionRoute | null,
  runId: string | undefined,
  options: { enabled: boolean; refreshKey?: unknown },
): RunTimelineState {
  const agentId = session?.agentId;
  const sessionId = session?.sessionId;
  // One key for the pair, so a read is kept only under the session and run it was asked for.
  const key = sessionId && runId ? `${agentId ?? ''}|${sessionId}|${runId}` : null;
  const [read, setRead] = useState<{ key: string; timeline: RunTimelineView | null; failed: boolean } | null>(null);
  const current = useRef(key);
  current.current = key;

  const reload = useCallback(async () => {
    if (!key || !sessionId || !runId) return;
    try {
      const view = await ChatApi.getRunTimeline({ ...(agentId ? { agentId } : {}), sessionId, runId });
      if (current.current === key) setRead({ key, timeline: view, failed: false });
    } catch {
      if (current.current === key) setRead((held) => ({ key, timeline: held?.key === key ? held.timeline : null, failed: true }));
    }
  }, [key, agentId, sessionId, runId]);

  useEffect(() => {
    if (options.enabled) void reload();
  }, [options.enabled, options.refreshKey, reload]);

  const mine = read && read.key === key ? read : null;
  return { timeline: mine?.timeline ?? null, failed: mine?.failed ?? false, reload };
}
