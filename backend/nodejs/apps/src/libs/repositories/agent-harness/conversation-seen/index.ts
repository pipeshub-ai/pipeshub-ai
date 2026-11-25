import { getConversationRepository } from '../../conversation';
import type { ConversationSeenMarks } from '../../../../modules/agent-harness/ports/run-lists';

/**
 * ADR-UI-01 D2: the seen marks live on the conversation itself (MongoDB `chatSessions.harnessSeen`, PostgreSQL
 * `conversation.chat_sessions.harness_seen`), so erasing a conversation erases them; the conversation repository
 * picks the store.
 */
export const conversationSeenMarks = (): ConversationSeenMarks => ({
  mark: (orgId, sessionId, userId, at) =>
    getConversationRepository().markHarnessSeen(sessionId, orgId, userId, at),
  read: (orgId, userId, sessionIds) =>
    getConversationRepository().harnessSeenMarks(orgId, userId, sessionIds),
});
