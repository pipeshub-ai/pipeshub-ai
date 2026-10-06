import { useEffect, useRef } from 'react';
import { useChatStore } from '../store';
import { cancelQueuedSend } from '../utils/queued-send';
import { saveDraft } from '../utils/draft-storage';

/**
 * Ties the composer's text to the slot's queued send: typing cancels the queue (the user is rewriting),
 * a cancelled queue can put its text back, and text left in a composer that is torn down because access
 * was lost is kept in draft storage.
 */
export function useQueuedSendComposer(
  slotId: string | null | undefined,
  text: string,
  setText: (text: string) => void,
): void {
  const queuedId = useChatStore((s) => (slotId ? s.slots[slotId]?.queuedSend?.clientMessageId ?? null : null));
  const restore = useChatStore((s) => (slotId ? s.slots[slotId]?.composerRestore ?? null : null));
  const baseline = useRef(text);
  const latest = useRef({ slotId, text });

  useEffect(() => {
    latest.current = { slotId, text };
  });

  useEffect(() => {
    baseline.current = latest.current.text;
  }, [queuedId]);

  useEffect(() => {
    if (slotId && queuedId && text.trim() && text !== baseline.current) cancelQueuedSend(slotId);
  }, [slotId, queuedId, text]);

  useEffect(() => {
    if (!slotId || restore === null) return;
    if (!latest.current.text) setText(restore);
    useChatStore.getState().updateSlot(slotId, { composerRestore: null });
  }, [slotId, restore, setText]);

  useEffect(
    () => () => {
      const { slotId: id, text: draft } = latest.current;
      const slot = id ? useChatStore.getState().slots[id] : undefined;
      if (slot?.accessLost && slot.convId && draft.trim()) saveDraft(slot.convId, draft);
    },
    [],
  );
}
