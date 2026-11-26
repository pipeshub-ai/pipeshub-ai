import { getConversationRepository } from '../../conversation';
import type { HarnessRun } from '../../../../modules/agent-harness/domain/execution';
import type { ConversationReader } from '../../../../modules/agent-harness/ports/conversation-reader';
import { attachPopulatedCitations } from '../../../../modules/enterprise_search/utils/utils';
import { orgUnit } from '../pg-documents/pg';

const ANSWER_TYPES = new Set(['bot_response', 'error']);

/**
 * The conversation a durable run's final `RUN_FINISHED` carries, as the MongoDB reader builds it, through the
 * conversation repository: the session as the chat API serves it with the newest answer, its citations populated.
 */
export class PgHarnessConversationReader implements ConversationReader {
  finishedConversation(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'actorId' | 'agentKey'>,
  ): Promise<Record<string, unknown> | null> {
    return orgUnit(undefined, run.orgId, { write: false }, async () => {
      const repo = getConversationRepository();
      const conversation = await repo.findById(run.sessionId);
      if (conversation === null || conversation.isDeleted === true) return null;
      const newest = await repo.listMessages(run.sessionId, {
        orgId: run.orgId,
        order: 'desc',
        limit: 20,
      });
      const answer = newest.find((row) =>
        ANSWER_TYPES.has(row.dto.messageType),
      )?.dto;
      return (await attachPopulatedCitations(
        conversation,
        answer !== undefined ? [answer] : [],
        [],
      )) as Record<string, unknown>;
    });
  }
}
