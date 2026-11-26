import { HarnessRun } from '../../../../modules/agent-harness/domain/execution';
import { ConversationReader } from '../../../../modules/agent-harness/ports/conversation-reader';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import {
  attachPopulatedCitations,
  getMessages,
} from '../../../../modules/enterprise_search/utils/utils';

const ANSWER_TYPES = new Set(['bot_response', 'error']);

/**
 * The conversation a durable run's final `RUN_FINISHED` carries, the way the
 * legacy stream built it after saving (`attachPopulatedCitations` over the
 * saved answer). Runs of one session are serialized, so the newest answer is
 * the run's own.
 */
export class HarnessConversationReader implements ConversationReader {
  async finishedConversation(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'actorId' | 'agentKey'>,
  ): Promise<Record<string, unknown> | null> {
    const conversation = await ChatSession.findOne({
      _id: run.sessionId,
      orgId: run.orgId,
      isDeleted: false,
    });
    if (!conversation) return null;
    const newest = (await getMessages(run.sessionId, {
      orgId: run.orgId,
      sort: -1,
      limit: 20,
    })) as Array<{ messageType?: string; toObject?: () => unknown }>;
    const answer = newest.find((message) =>
      ANSWER_TYPES.has(String(message.messageType)),
    );
    const plain: unknown =
      answer?.toObject !== undefined ? answer.toObject() : answer;
    return (await attachPopulatedCitations(
      conversation.toObject(),
      plain !== undefined ? [plain] : [],
      [],
    )) as Record<string, unknown>;
  }
}
