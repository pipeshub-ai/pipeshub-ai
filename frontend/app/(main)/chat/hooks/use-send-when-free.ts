import { useEffect } from 'react';
import { useChatStore } from '../store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { sendQueuedMessage } from '../utils/send-when-free';

/**
 * Sends the slot's queued message as soon as the other person's run ends. Mounted for as long as the
 * chat is open, since the busy banner itself disappears when the run clears.
 */
export function useSendWhenFree(slotId: string | null | undefined): void {
  const flagOn = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const queued = useChatStore((s) => (slotId ? s.slots[slotId]?.queuedSend ?? null : null));
  const busy = useChatStore((s) => (slotId ? s.slots[slotId]?.activeRun != null : false));
  const streaming = useChatStore((s) => (slotId ? s.slots[slotId]?.isStreaming ?? false : false));
  const lost = useChatStore((s) => (slotId ? s.slots[slotId]?.accessLost ?? false : false));

  useEffect(() => {
    if (!flagOn || !slotId || !queued || busy || streaming || lost) return;
    sendQueuedMessage(slotId);
  }, [flagOn, slotId, queued, busy, streaming, lost]);
}
