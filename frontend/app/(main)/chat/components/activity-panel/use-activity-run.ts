'use client';

import { useEffect, useMemo, useState } from 'react';
import { useChatStore } from '../../store';
import { activityRun } from '../../activity';
import { useThreadIsActive } from '../../hooks/use-thread-owner';
import type { DurableAgentRunHandle } from '../../types';

const NO_MESSAGES: never[] = [];

/**
 * The active conversation's run for the activity panel and the chips. While
 * a new turn waits for admission there is briefly no run: the previous one
 * stays, so an open panel does not blink shut.
 */
export function useActivityRun(): DurableAgentRunHandle | null {
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  const threadAgentId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.threadAgentId ?? null) : null));
  const durableRun = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.durableRun ?? null) : null));
  const lastDurableRun = useChatStore((s) =>
    s.activeSlotId ? (s.slots[s.activeSlotId]?.lastDurableRun ?? null) : null,
  );
  const messages = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.messages ?? NO_MESSAGES) : NO_MESSAGES));
  const isOwner = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.isOwner ?? null) : null));
  const isStreaming = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.isStreaming ?? false) : false));

  const run = useMemo(
    () => activityRun({ convId, threadAgentId, durableRun, lastDurableRun, messages, isOwner }),
    [convId, threadAgentId, durableRun, lastDurableRun, messages, isOwner],
  );

  const [held, setHeld] = useState<{ convId: string | null; run: DurableAgentRunHandle } | null>(null);
  useEffect(() => {
    if (run) setHeld({ convId, run });
  }, [run, convId]);

  if (run) return run;
  return isStreaming && held && held.convId === convId ? held.run : null;
}

/**
 * One answer's run for its "View activity" (CTL-3): the run its saved message names, or for the latest answer the
 * run this tab followed. None in a conversation that is someone else's: its runs are not the viewer's (D-5), and
 * none while the thread drawn is still the previous chat's (FIX-NAV).
 */
export function useMessageRun(runId: string | undefined, isLastMessage: boolean): DurableAgentRunHandle | null {
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  const threadAgentId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.threadAgentId ?? null) : null));
  const isOwner = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.isOwner ?? null) : null));
  const followed = useChatStore((s) => {
    const slot = s.activeSlotId ? s.slots[s.activeSlotId] : undefined;
    return (slot?.durableRun ?? slot?.lastDurableRun)?.runId ?? null;
  });
  const current = useThreadIsActive();
  const id = runId ?? (isLastMessage ? followed : null);
  return useMemo(
    () =>
      current && id && convId && isOwner !== false
        ? { ...(threadAgentId ? { agentId: threadAgentId } : {}), sessionId: convId, runId: id }
        : null,
    [current, id, convId, isOwner, threadAgentId],
  );
}
