import type { ConversationSeenMarks } from '../ports/run-lists';

/** Seen marks for tests and a deployment without a conversation store: one map per conversation, as the stores keep it. */
export class InMemoryConversationSeenMarks implements ConversationSeenMarks {
  private readonly marks = new Map<string, Map<string, number>>();

  /** `exists`: whether the conversation is the org's; a mark on any other is refused (null), as the stores refuse it. */
  constructor(
    private readonly exists: (
      orgId: string,
      sessionId: string,
    ) => boolean = () => true,
  ) {}

  mark(
    orgId: string,
    sessionId: string,
    userId: string,
    at: number,
  ): Promise<number | null> {
    if (!this.exists(orgId, sessionId)) return Promise.resolve(null);
    const key = `${orgId}\u0000${sessionId}`;
    const marks = this.marks.get(key) ?? new Map<string, number>();
    const held = Math.max(marks.get(userId) ?? at, at);
    marks.set(userId, held);
    this.marks.set(key, marks);
    return Promise.resolve(held);
  }

  read(
    orgId: string,
    userId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, number>> {
    const found = new Map<string, number>();
    for (const sessionId of sessionIds) {
      const mark = this.marks.get(`${orgId}\u0000${sessionId}`)?.get(userId);
      if (mark !== undefined) found.set(sessionId, mark);
    }
    return Promise.resolve(found);
  }
}
