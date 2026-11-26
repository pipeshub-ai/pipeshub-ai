'use client';

import { useEffect, useRef } from 'react';
import { useSearchParams } from 'next/navigation';
import { isConversationStreamingInScope, useChatStore } from '@/chat/store';
import { selectAgentHarnessOn, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { JobsApi, type SessionRoute } from './jobs-api';
import { refreshJobLists } from './swr-keys';

/**
 * The person's "seen up to" mark on the chat they have open (ADR-UI-01 D2): `PUT …/seen` when the chat opens and
 * again when a run ends while it is open, so the rail's "finished, not seen yet" dot clears for what they watched
 * finish. The server's clock is the only writer. The rail's copy of the chat drops its unseen dot at once, and a
 * run that ended re-reads the rail, whose copy still says it is working.
 */
export function useSeenMarks(session: SessionRoute | null, running: boolean): void {
  const key = session ? `${session.agentId ?? ''}/${session.sessionId}` : null;
  const wasRunning = useRef(running);
  useEffect(() => {
    if (!session) return;
    void mark(session);
  }, [key]);
  useEffect(() => {
    if (session && wasRunning.current && !running) {
      void mark(session);
      refreshJobLists();
    }
    wasRunning.current = running;
  }, [running, key]);
}

/**
 * The seen mark for the chat the URL has open, with the harness on. Mounted once, by the chat sidebar, which every
 * page with a chat shows.
 */
export function useOpenChatSeenMark(): void {
  const params = useSearchParams();
  const harness = useFeatureFlagsStore(selectAgentHarnessOn);
  const sessionId = params?.get('conversationId')?.trim() || null;
  const agentId = params?.get('agentId')?.trim() || null;
  const running = useChatStore((s) => (sessionId ? isConversationStreamingInScope(s.slots, sessionId, agentId) : false));
  const session = harness && sessionId ? { sessionId, ...(agentId ? { agentId } : {}) } : null;
  useSeenMarks(session, running);
}

async function mark(session: SessionRoute): Promise<void> {
  const seen = await JobsApi.markSeen(session);
  if (!seen) return;
  const clear = <T extends { id: string; harnessState?: { finishedUnseen: boolean; seenUpTo: number | null } }>(list: T[]): T[] =>
    list.map((c) => (c.id === session.sessionId && c.harnessState ? { ...c, harnessState: { ...c.harnessState, finishedUnseen: false, seenUpTo: seen.seenUpTo } } : c));
  useChatStore.setState((s) => ({
    conversations: clear(s.conversations),
    sharedConversations: clear(s.sharedConversations),
    agentConversations: clear(s.agentConversations),
  }));
}
