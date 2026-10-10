import type { ThreadMessageLike } from '@assistant-ui/react';
import { loadHistoricalMessages, type LoadHistoricalResult } from '../runtime';
import type { ConversationMessage } from '../types';

/** Feed messages as thread rows (and the open question card they carry), stamped with the page's `rev`. */
export function feedToThread(messages: readonly ConversationMessage[], rev: number): LoadHistoricalResult {
  return loadHistoricalMessages([...messages], { rev });
}

export function feedToThreadRows(messages: readonly ConversationMessage[], rev: number): ThreadMessageLike[] {
  return feedToThread(messages, rev).messages;
}
