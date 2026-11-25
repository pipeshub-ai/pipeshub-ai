'use client';

import { useEffect, useMemo } from 'react';
import { useChatStore } from '../../store';
import type { SessionRoute } from '@/lib/harness-jobs/jobs-api';
import { useChatGrantsStore, type ChatGrant } from './chat-grants-store';

const NONE: ChatGrant[] = [];

export interface ChatGrants {
  session: SessionRoute | null;
  /** Only the owner reads and takes back a chat's grants (ADR-GRANT-01 D8). */
  owner: boolean;
  /** Other people take part: the row names who allowed each one. */
  shared: boolean;
  grants: ChatGrant[];
}

/**
 * The grants of the chat on screen. The list is read when the chat opens, and again whenever an ask is answered or
 * a turn starts or ends; it is never polled, since each read walks the chat's streams
 * (ADR-GRANT-01 D8, consequences). Only the top-bar button, always mounted, passes `read`, so a chat reads once.
 */
export function useChatGrants(read = false): ChatGrants {
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  const agentId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.threadAgentId ?? null) : null));
  const owner = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner === true : false));
  const shared = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isShared === true : false));
  const durable = useChatStore((s) => {
    const slot = s.activeSlotId ? s.slots[s.activeSlotId] : undefined;
    return slot?.durable === true || slot?.durableRun != null;
  });
  const streaming = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isStreaming === true : false));
  const asks = useChatStore((s) => {
    const pending = s.activeSlotId ? s.slots[s.activeSlotId]?.pendingApprovals : undefined;
    return pending?.length ? pending.map((a) => a.interactionId).join(',') : '';
  });
  const grants = useChatGrantsStore((s) => (convId ? s.byChat[convId] : undefined)) ?? NONE;

  const session = useMemo<SessionRoute | null>(() => (convId ? { ...(agentId ? { agentId } : {}), sessionId: convId } : null), [convId, agentId]);
  const readable = owner && durable && session !== null;

  useEffect(() => {
    if (!read || !readable || !session) return;
    void useChatGrantsStore.getState().load(session);
    // `session` is named by its ids. `asks` changing is an ask answered or raised, and `streaming` a turn starting or
    // ending: the moments a grant can be given or reach this chat from one of its jobs.
  }, [read, readable, streaming, asks, convId, agentId]);

  return { session, owner, shared, grants: readable ? grants : NONE };
}
