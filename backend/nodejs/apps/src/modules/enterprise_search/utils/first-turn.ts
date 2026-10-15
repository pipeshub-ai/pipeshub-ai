import mongoose, { Types } from 'mongoose';
import { CONVERSATION_STATUS } from '../constants/constants';
import { IChatSession } from '../types/conversation.interfaces';
import { conversationExtensions } from '../services/conversation/conversation-extensions';
import {
  AppendedTurn,
  FirstTurnInput,
} from '../services/conversation/turn/turn-extension';
import { FollowUpBody } from './follow-up-turn';
import { openConversation } from './non-streaming-chat';
import { buildUserQueryMessage, extractModelInfo } from './utils';

export type { FirstTurnInput } from '../services/conversation/turn/turn-extension';

export const CHAT_TITLE_MAX_LENGTH = 100;

const sessionFields = (
  input: FirstTurnInput,
  authorUserId: Types.ObjectId,
  orgId: Types.ObjectId,
): Partial<IChatSession> => {
  const { target, link } = input;
  const body = input.body as FollowUpBody;
  return {
    orgId,
    userId: authorUserId,
    initiator: authorUserId,
    title: body.query.slice(0, CHAT_TITLE_MAX_LENGTH),
    lastActivityAt: Date.now(),
    status: CONVERSATION_STATUS.INPROGRESS,
    modelInfo: extractModelInfo(body),
    ...(target.kind === 'agent'
      ? {
          agentKey: target.agentKey,
          sessionType: 'agent',
          // The unified chatSessions schema cannot default this: it also backs plain chats.
          conversationSource: 'agent_chat',
        }
      : { sessionType: 'chat' }),
    ...(link.projectId
      ? {
          projectId: new mongoose.Types.ObjectId(link.projectId),
          projectVisibility: link.projectVisibility,
        }
      : {}),
  };
};

/** Creates the conversation and the caller's first `user_query` together. */
export async function createPlainFirstTurn(
  input: FirstTurnInput,
): Promise<AppendedTurn> {
  const body = input.body as FollowUpBody;
  const authorUserId = new Types.ObjectId(String(input.userId));
  const orgId = new Types.ObjectId(String(input.orgId));
  const userMessage = buildUserQueryMessage(
    body.query,
    body.appliedFilters,
    body.chatMode,
    input.attachments,
    { authorUserId },
  );
  const { conversation, userRow } = await openConversation(
    sessionFields(input, authorUserId, orgId),
    userMessage,
  );
  return {
    conversation,
    userRow,
    run: { requestedBy: authorUserId, inReplyTo: userRow._id },
  };
}

/** Creates the conversation with the caller's first message, through the turn extension. */
export const createFirstTurn = (input: FirstTurnInput): Promise<AppendedTurn> =>
  conversationExtensions().turns.createFirstTurn(input);
