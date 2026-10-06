import { Types } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import { ConversationNotFoundError } from '../domain/errors';
import { ConversationAccessGrant } from '../http/conversation-context';
import { DRAFT_AGENT_TOOL } from '../feed/draft-redaction';
import { ConversationOperation } from '../domain/types';

export interface DraftRef {
  readonly conversationId: string;
  readonly messageId: string;
}

export interface IDraftRowLookup {
  /** Who asked for the draft in message `messageId` of `sessionId`; `null` when that row is not a draft card. */
  requesterOf(
    sessionId: Types.ObjectId,
    messageId: string,
  ): Promise<string | null>;
}

export class MongoDraftRowLookup implements IDraftRowLookup {
  async requesterOf(
    sessionId: Types.ObjectId,
    messageId: string,
  ): Promise<string | null> {
    const row = await ChatSessionMessage.findOne({
      _id: messageId,
      sessionId,
      messageType: 'tool_call',
      'tools.toolName': DRAFT_AGENT_TOOL,
    })
      .select('requestedBy')
      .lean()
      .exec();
    return row?.requestedBy ? String(row.requestedBy) : null;
  }
}

export interface DraftRefGuards {
  authorizeById(
    req: AuthenticatedUserRequest,
    op: ConversationOperation,
    kind: 'chat',
    conversationId: string,
  ): Promise<ConversationAccessGrant>;
}

/**
 * Turns the `draftRef` of an agent create into a verified one: the builder is on, the caller can read
 * the conversation, and the message is a draft card that they themselves asked for. Every failure is
 * the same not-found, so a probe learns nothing about other people's drafts.
 */
export class AgentDraftRefResolver {
  constructor(
    private readonly guards: DraftRefGuards,
    private readonly flags: IFeatureFlags,
    private readonly rows: IDraftRowLookup = new MongoDraftRowLookup(),
  ) {}

  async resolve(
    req: AuthenticatedUserRequest,
    ref: DraftRef,
  ): Promise<DraftRef> {
    if (!(await this.flags.isEnabled(COLLAB_FLAG_KEYS.chatAgentBuilder))) {
      throw new ConversationNotFoundError();
    }
    const userId = req.user?.userId;
    if (!userId || !Types.ObjectId.isValid(ref.messageId)) {
      throw new ConversationNotFoundError();
    }
    const { session } = await this.guards.authorizeById(
      req,
      'read',
      'chat',
      ref.conversationId,
    );
    const requester = await this.rows.requesterOf(session._id, ref.messageId);
    if (requester === null || requester !== String(userId)) {
      throw new ConversationNotFoundError();
    }
    return ref;
  }
}
