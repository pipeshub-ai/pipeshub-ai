import type { ConversationMessage, RespondingAgent } from '../types';

/** Tolerant of a malformed payload: anything without a string `key` is ignored. */
export function readRespondingAgent(value: unknown): RespondingAgent | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const { key, name, handle } = value as Record<string, unknown>;
  if (typeof key !== 'string' || !key) return undefined;
  return {
    key,
    ...(typeof name === 'string' && name.trim() ? { name: name.trim() } : {}),
    ...(typeof handle === 'string' && handle.trim() ? { handle: handle.trim() } : {}),
  };
}

/** Collaboration fields of a stored message, as they sit in a thread row's `metadata.custom`. `rev` is the feed page's. */
export function collabRowCustom(msg: ConversationMessage, rev?: number): Record<string, unknown> {
  const respondingAgent = readRespondingAgent(msg.respondingAgent);
  return {
    ...(msg.seq !== undefined ? { seq: msg.seq } : {}),
    ...(rev !== undefined ? { rev } : {}),
    ...(msg.clientMessageId ? { clientMessageId: msg.clientMessageId } : {}),
    ...(msg.author !== undefined ? { author: msg.author } : {}),
    ...(msg.requestedBy !== undefined ? { requestedBy: msg.requestedBy } : {}),
    ...(respondingAgent ? { respondingAgent } : {}),
    ...(msg.filesShared !== undefined ? { filesShared: msg.filesShared } : {}),
  };
}
