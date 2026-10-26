import { useEffect, useRef } from 'react';
import { chatExtension } from '@/config';
import { useChatStore } from '@/app/(main)/chat/store';
import { heldSendsOf, keepHeldSends, queueOf } from '../utils/queued-send';
import { queueMayGo, sendNextQueued } from '../utils/send-when-free';

/**
 * Sends the slot's queued questions one at a time, each as soon as the chat is free. It also keeps the tab's copy of
 * the queue, and gives a new slot for the chat what its dropped slot, or the tab before a reload, had queued.
 */
export function useSendWhenFree(slotId: string | null | undefined): void {
  const convId = useChatStore((s) => (slotId ? s.slots[slotId]?.convId ?? null : null));
  const loaded = useChatStore((s) => (slotId ? s.slots[slotId]?.isInitialized ?? false : false));
  const queue = useChatStore((s) => (slotId ? s.slots[slotId]?.queue : undefined));
  const restores = useChatStore((s) => {
    const slot = slotId ? s.slots[slotId] : undefined;
    return slot ? chatExtension.queue.restoresKept(slot) : false;
  });
  const mayGo = useChatStore((s) => (slotId ? queueMayGo(s.slots[slotId]) : false));
  const adopted = useRef<string | null>(null);

  useEffect(() => {
    // After the history load, so a send it lets go builds on the loaded thread.
    if (!slotId || !convId || !loaded) return;
    if (adopted.current !== slotId) {
      // Not restoring yet, with nothing queued (an extension may decide only once its settings load): the tab's copy
      // stays as it is until then, rather than being overwritten by this empty queue.
      if (!restores && queueOf({ queue }).length === 0) return;
      adopted.current = slotId;
      const kept = queueOf({ queue }).length > 0 || !restores ? [] : heldSendsOf(convId);
      if (kept.length > 0) {
        useChatStore.getState().updateSlot(slotId, { queue: kept });
        return;
      }
    }
    keepHeldSends(convId, queueOf({ queue }));
  }, [slotId, convId, loaded, queue, restores]);

  useEffect(() => {
    if (slotId && loaded && mayGo) sendNextQueued(slotId);
  }, [slotId, loaded, mayGo]);
}

/** The slot's queue and its paired slot's, each sent when its own chat is free. */
export function useQueuedSends(slotId: string | null | undefined): void {
  const pairedId = useChatStore((s) => (slotId ? chatExtension.queue.pairedSlotId(s.slots, slotId) : null));
  useSendWhenFree(slotId);
  useSendWhenFree(pairedId);
}
