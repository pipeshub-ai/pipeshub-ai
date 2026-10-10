import { Types } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import {
  AgentDraftAlreadyCreatedError,
  ConversationNotFoundError,
} from '../domain/errors';
import { ConversationAccessGrant } from '../http/conversation-context';
import { DRAFT_AGENT_TOOL } from '../feed/draft-redaction';
import { ConversationOperation } from '../domain/types';

export interface DraftRef {
  readonly conversationId: string;
  readonly messageId: string;
}

export interface VerifiedDraftRef extends DraftRef {
  readonly sessionId: Types.ObjectId;
}

export interface CreatedAgentRef {
  readonly agentKey: string;
  readonly handle: string;
}

export interface DraftRow {
  readonly requester: string | null;
  readonly createdAgent?: CreatedAgentRef;
}

export interface IDraftRowLookup {
  /** The draft card in message `messageId` of `sessionId`; `null` when that row is not a draft card. */
  draftOf(sessionId: Types.ObjectId, messageId: string): Promise<DraftRow | null>;
  markCreated(
    sessionId: Types.ObjectId,
    messageId: string,
    agent: CreatedAgentRef,
  ): Promise<void>;
}

const draftRowFilter = (sessionId: Types.ObjectId, messageId: string) => ({
  _id: messageId,
  sessionId,
  messageType: 'tool_call',
  'tools.toolName': DRAFT_AGENT_TOOL,
});

export class MongoDraftRowLookup implements IDraftRowLookup {
  async draftOf(
    sessionId: Types.ObjectId,
    messageId: string,
  ): Promise<DraftRow | null> {
    const row = await ChatSessionMessage.findOne(draftRowFilter(sessionId, messageId))
      .select('requestedBy agentDraftCreated')
      .lean()
      .exec();
    if (!row) return null;
    const created = row.agentDraftCreated;
    return {
      requester: row.requestedBy ? String(row.requestedBy) : null,
      ...(created?.agentKey && {
        createdAgent: { agentKey: created.agentKey, handle: created.handle ?? '' },
      }),
    };
  }

  async markCreated(
    sessionId: Types.ObjectId,
    messageId: string,
    agent: CreatedAgentRef,
  ): Promise<void> {
    await ChatSessionMessage.updateOne(draftRowFilter(sessionId, messageId), {
      $set: {
        agentDraftCreated: {
          agentKey: agent.agentKey,
          handle: agent.handle,
          createdAt: new Date(),
        },
      },
    }).exec();
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
  ): Promise<VerifiedDraftRef> {
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
    const row = await this.rows.draftOf(session._id, ref.messageId);
    if (row === null || row.requester === null || row.requester !== String(userId)) {
      throw new ConversationNotFoundError();
    }
    // A second tab or a reloaded card would otherwise create a duplicate agent.
    if (row.createdAgent) {
      throw new AgentDraftAlreadyCreatedError(row.createdAgent);
    }
    return { ...ref, sessionId: session._id };
  }

  /** Best effort: the agent exists either way, the card only falls back to its draft state. */
  async markCreated(ref: VerifiedDraftRef, agent: CreatedAgentRef): Promise<void> {
    await this.rows.markCreated(ref.sessionId, ref.messageId, agent);
  }
}
