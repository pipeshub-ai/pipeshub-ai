import { useChatStore } from '../store';
import type { AttachmentRef, ChatSlot } from '../types';
import type { MentionRef } from '../components/composer/composer-input.types';
import { collabSendEnabled } from './collab-flag';
import { newClientMessageId } from './collab-send-fields';
import { saveDraft } from './draft-storage';

/** A send while another person's run is active waits instead of going out and failing with a 409. */
export function shouldQueueSend(slot: Pick<ChatSlot, 'activeRun' | 'isStreaming' | 'accessLost' | 'access'>): boolean {
  return (
    collabSendEnabled() &&
    slot.activeRun !== null &&
    !slot.isStreaming &&
    !slot.accessLost &&
    slot.access?.canSend !== false
  );
}

/** Holds the message; `useSendWhenFree` sends it once `activeRun` clears. Replaces anything already queued. */
export function queueSend(slotId: string, message: { query: string; attachments?: AttachmentRef[]; mentions?: MentionRef[] }): void {
  const slot = useChatStore.getState().slots[slotId];
  if (!slot) return;
  if (slot.convId) saveDraft(slot.convId, message.query);
  useChatStore.getState().updateSlot(slotId, {
    queuedSend: {
      query: message.query,
      ...(message.attachments?.length ? { attachments: message.attachments } : {}),
      ...(message.mentions?.length ? { mentions: message.mentions } : {}),
      clientMessageId: newClientMessageId(),
      queuedAt: Date.now(),
    },
  });
}

/** Drops the queued message. Its text stays in draft storage; `restore` also puts it back in the composer. */
export function cancelQueuedSend(slotId: string, options?: { restore?: boolean }): void {
  const slot = useChatStore.getState().slots[slotId];
  if (!slot?.queuedSend) return;
  useChatStore.getState().updateSlot(slotId, {
    queuedSend: null,
    ...(options?.restore ? { composerRestore: slot.queuedSend.query } : {}),
  });
}
