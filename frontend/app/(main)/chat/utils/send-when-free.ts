import { useChatStore } from '../store';
import { ATTACHMENT_ONLY_STREAM_QUERY, buildStreamChatRequestForSlot } from '../runtime';
import { streamMessageForSlot } from '../streaming';

/**
 * Sends the queued message now. It leaves the queue first, so it goes out once; the baseSeq is read
 * from the rows the sync has merged by this point. Returns whether a send started.
 */
export function sendQueuedMessage(slotId: string): boolean {
  const slot = useChatStore.getState().slots[slotId];
  const queued = slot?.queuedSend;
  if (!slot || !queued) return false;
  if (slot.isStreaming || slot.activeRun || slot.accessLost) return false;

  useChatStore.getState().updateSlot(slotId, { queuedSend: null });
  const apiQuery = queued.query || (queued.attachments?.length ? ATTACHMENT_ONLY_STREAM_QUERY : '');
  if (!apiQuery) return false;
  const request = buildStreamChatRequestForSlot(slotId, apiQuery);
  if (!request) return false;
  if (queued.attachments?.length) request.attachments = queued.attachments;
  if (queued.mentions?.length) request.mentions = queued.mentions;
  request.clientMessageId = queued.clientMessageId;
  void streamMessageForSlot(slotId, queued.query, request);
  return true;
}
