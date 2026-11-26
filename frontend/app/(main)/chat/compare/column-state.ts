import { findToolApproval } from '../tool-approval';
import { getThreadMessagePlainText } from '../runtime';
import type { ChatSlot, MessagePart } from '../types';
import type { ColumnStatus } from './types';

/**
 * Where one compared slot stands. Only `done` may be chosen: a streaming run,
 * a card or approval still waiting for the person, a failed run and an empty
 * reply are all things nobody should keep as the conversation's answer.
 */
export function columnStatus(slot: ChatSlot | undefined): ColumnStatus {
  if (!slot) return 'starting';
  // A run the page did not start (a reload while it was thinking) is still running on the server.
  if (slot.isStreaming || slot.remoteRunInProgress) return 'streaming';
  if (slot.pendingAskUserQuestion?.status === 'pending') return 'waiting';
  const last = slot.messages[slot.messages.length - 1];
  if (!last) return 'starting';
  if (last.role !== 'assistant') return 'error';

  const custom = (last.metadata?.custom ?? {}) as {
    persistedParts?: MessagePart[];
    messageId?: string;
    status?: string;
    streamError?: unknown;
  };
  if (findToolApproval(custom.persistedParts)) return 'waiting';
  // A failed run leaves its error text in a row the server never saved, so it has no message id.
  if (custom.streamError || (!custom.messageId && custom.status !== 'stopped')) return 'error';
  return getThreadMessagePlainText(last).trim() ? 'done' : 'empty';
}

/** A run that is going but has produced no answer text yet: shown as "Thinking...". */
export function columnIsThinking(slot: ChatSlot | undefined): boolean {
  if (!slot || columnStatus(slot) !== 'streaming' || slot.streamingContent?.trim()) return false;
  const last = slot.messages[slot.messages.length - 1];
  return !(last?.role === 'assistant' && getThreadMessagePlainText(last).trim());
}
