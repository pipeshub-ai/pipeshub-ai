/**
 * Slack thread repository: which assistant conversation a Slack thread (per
 * bot and person) continues.
 */
export interface SlackThreadRepository {
  /** The conversation id for this thread, bot and person; null when none. */
  getConversationId(
    threadId: string,
    botId: string,
    email: string,
  ): Promise<string | null>;
  /** Creates or replaces the link. */
  saveConversationId(row: {
    threadId: string;
    conversationId: string;
    botId: string;
    email: string;
  }): Promise<void>;
}
