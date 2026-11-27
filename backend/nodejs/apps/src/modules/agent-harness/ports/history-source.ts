import {
  AccountedConversation,
  ConversationHistory,
} from '../domain/session-import';
import { ConversationRef } from './conversation-writer';

/**
 * The conversation as history (P1b T3), read inside the admission
 * transaction; browser-supplied history is never used.
 */
export interface HistorySource<Tx = unknown> {
  /**
   * Each message's history entries, and what the harness has accounted for:
   * `accounted` when the transaction already holds it (the session's bind),
   * else read from the session.
   */
  read(
    ref: ConversationRef,
    tx: Tx,
    accounted?: AccountedConversation,
  ): Promise<ConversationHistory>;
}
