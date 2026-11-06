import { useEffect, useRef, type MutableRefObject } from 'react';
import { chatExtension } from '@/config';
import { useChatStore } from '@/app/(main)/chat/store';

/**
 * Ties the composer's box to the slot: a text given back (an edited queued question, a refused send) fills an empty
 * box, and the slot knows whether the box holds text, so a queued question is edited only into an empty one. Queued
 * questions never sit in the box. Texts given back to the paired slot come into this box too.
 */
export function useQueuedSendComposer(
  slotId: string | null | undefined,
  text: string,
  setText: (text: string) => void,
): void {
  const latest = useRef({ text });
  useEffect(() => {
    latest.current = { text };
  });

  const hasText = text.trim().length > 0;
  useEffect(() => {
    if (slotId && useChatStore.getState().slots[slotId]) useChatStore.getState().updateSlot(slotId, { composerHasText: hasText });
  }, [slotId, hasText]);

  const pairedId = useChatStore((s) => (slotId ? chatExtension.queue.pairedSlotId(s.slots, slotId) : null));
  useRestoredText(slotId, setText, latest);
  useRestoredText(pairedId, setText, latest);
}

/** One slot's text for the box: it fills an empty box, and is taken either way. */
function useRestoredText(
  slotId: string | null | undefined,
  setText: (text: string) => void,
  latest: MutableRefObject<{ text: string }>,
): void {
  const restore = useChatStore((s) => (slotId ? s.slots[slotId]?.composerRestore ?? null : null));

  useEffect(() => {
    if (!slotId || restore === null) return;
    if (!latest.current.text) setText(restore);
    useChatStore.getState().updateSlot(slotId, { composerRestore: null });
  }, [slotId, restore, setText, latest]);
}
