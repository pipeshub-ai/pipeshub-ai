import type { ConversationMessage } from '../types';

/** Collaboration fields of a stored message, as they sit in a thread row's `metadata.custom`. `rev` is the feed page's. */
export function collabRowCustom(msg: ConversationMessage, rev?: number): Record<string, unknown> {
  return {
    ...(msg.seq !== undefined ? { seq: msg.seq } : {}),
    ...(rev !== undefined ? { rev } : {}),
    ...(msg.clientMessageId ? { clientMessageId: msg.clientMessageId } : {}),
    ...(msg.author !== undefined ? { author: msg.author } : {}),
    ...(msg.requestedBy !== undefined ? { requestedBy: msg.requestedBy } : {}),
    ...(msg.filesShared !== undefined ? { filesShared: msg.filesShared } : {}),
  };
}
