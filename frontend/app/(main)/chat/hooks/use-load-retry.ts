'use client';

import { useEffect } from 'react';
import { useChatStore } from '@/chat/store';
import { loadAgain, retryLater } from '@/chat/utils/load-retry';

/**
 * A load of the open chat's history that failed is tried again by itself (`retryLater`), also on coming back to the chat;
 * not while an answer is written here, nor while a load is under way.
 */
export function useLoadRetry(slotId: string | null): void {
  const failed = useChatStore((s) => (slotId ? s.slots[slotId]?.failedLoad ?? null : null));
  const busy = useChatStore((s) => {
    const slot = slotId ? s.slots[slotId] : undefined;
    return !slot?.isInitialized || slot.isStreaming || slot.stopping;
  });
  useEffect(() => {
    if (!slotId || !failed || failed.trying || busy) return undefined;
    return retryLater(failed, () => loadAgain(slotId));
  }, [slotId, failed, busy]);
}
