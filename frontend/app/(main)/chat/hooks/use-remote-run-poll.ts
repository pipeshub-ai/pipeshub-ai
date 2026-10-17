'use client';

import { useEffect } from 'react';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ChatApi } from '@/chat/api';
import { useChatStore } from '@/chat/store';

const POLL_INTERVAL_MS = 3000;
/** A little over the server's detached-run cap, after which the run is abandoned. */
const MAX_POLL_MS = 35 * 60 * 1000;
const IN_PROGRESS = 'Inprogress';

interface RemoteRunPollTarget {
  slotId: string;
  convId: string;
  agentId: string | null;
}

async function fetchStatus({ convId, agentId }: RemoteRunPollTarget): Promise<string> {
  const detail = agentId
    ? await AgentsApi.fetchAgentConversation(agentId, convId, { page: 1, limit: 1 })
    : await ChatApi.fetchConversation(convId, 1, 1);
  return detail.conversation.status;
}

/**
 * A refresh drops the SSE connection but the server finishes the run and saves the answer. While
 * the conversation is still `Inprogress`, check its status and, once it settles, reload the history
 * once. Returns a function that stops polling.
 */
export function startRemoteRunPoll(target: RemoteRunPollTarget): () => void {
  const deadline = Date.now() + MAX_POLL_MS;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;

  const finish = (): void => {
    useChatStore.getState().updateSlot(target.slotId, { remoteRunInProgress: false });
  };

  const tick = async (): Promise<void> => {
    if (stopped) return;
    const slot = useChatStore.getState().slots[target.slotId];
    // Leave `remoteRunInProgress` set: the hook re-arms once the local stream is done.
    if (!slot || slot.isStreaming || slot.stopping) return;

    let settled = false;
    try {
      settled = (await fetchStatus(target)) !== IN_PROGRESS;
    } catch {
      // A failed check is retried until the deadline.
    }
    if (stopped) return;

    if (settled) {
      finish();
      useChatStore.getState().invalidateConversation(target.convId);
      return;
    }
    if (Date.now() >= deadline) {
      finish();
      return;
    }
    timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
  };

  timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);

  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}

export function useRemoteRunPoll(
  slotId: string | null,
  convId: string | null,
  agentId: string | null,
  enabled: boolean,
): void {
  useEffect(() => {
    if (!enabled || !slotId || !convId) return undefined;
    return startRemoteRunPoll({ slotId, convId, agentId });
  }, [enabled, slotId, convId, agentId]);
}
