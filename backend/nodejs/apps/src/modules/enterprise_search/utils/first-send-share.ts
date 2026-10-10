import { Types } from 'mongoose';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { Logger } from '../../../libs/services/logger.service';
import { ChatSession } from '../schema/chat.session.schema';
import { ChatSessionMessage } from '../schema/chat.session.message.schema';
import { UpsertInput } from '../services/collaboration/conversation-collaboration.service';
import { Principal } from '../services/collaboration/domain/types';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import { IChatSessionDocument } from '../types/conversation.interfaces';

const logger = Logger.getInstance({ service: 'First-send share' });

/** The first-send `share` block: the collaborators PUT body. */
export interface FirstSendShareBody {
  collaborators: Array<{
    principalType: 'user' | 'team';
    principalId: string;
    accessLevel: 'read' | 'write';
  }>;
  note?: string;
  confirmOrgWide?: true;
}

const toPrincipal = (type: 'user' | 'team', id: string): Principal =>
  type === 'user' ? { type: 'user', userId: id } : { type: 'team', teamId: id };

const upsertInputOf = (share: FirstSendShareBody): UpsertInput => ({
  collaborators: share.collaborators.map((c) => ({
    principal: toPrincipal(c.principalType, c.principalId),
    accessLevel: c.accessLevel,
  })),
  note: share.note,
  confirmOrgWide: share.confirmOrgWide,
});

/**
 * Before anything is created: with collaborative chats off a `share` is a 404, as the collaborators
 * routes answer; otherwise it must be something `PUT .../collaborators` would accept from an owner.
 */
export async function validateFirstSendShare(
  deps: Pick<ConversationTurnDeps, 'sharing'>,
  req: AuthenticatedUserRequest,
  collab: boolean,
  share: FirstSendShareBody | undefined,
): Promise<UpsertInput | undefined> {
  if (share === undefined) return undefined;
  if (!collab || !deps.sharing) throw new NotFoundError('Not found');
  const identity = callerIdentityOf(req);
  const input = upsertInputOf(share);
  await deps.sharing.validate(
    { userId: identity.userId, orgId: identity.orgId, teamIds: 'unresolved' },
    identity,
    input,
  );
  return input;
}

/**
 * Right after the chat and its first row exist, before the stream starts. If sharing fails the new
 * chat is removed, so a refused share leaves nothing behind.
 */
export async function applyFirstSendShare(
  deps: Pick<ConversationTurnDeps, 'sharing'>,
  req: AuthenticatedUserRequest,
  conversation: IChatSessionDocument,
  input: UpsertInput | undefined,
): Promise<void> {
  if (input === undefined || !deps.sharing) return;
  const identity = callerIdentityOf(req);
  const agentKey = conversation.agentKey;
  try {
    await deps.sharing.apply(
      { userId: identity.userId, orgId: identity.orgId, teamIds: 'unresolved' },
      identity,
      agentKey
        ? { id: String(conversation._id), kind: 'agent', agentKey }
        : { id: String(conversation._id), kind: 'chat' },
      input,
    );
  } catch (error) {
    await discardNewChat(conversation);
    throw error;
  }
}

async function discardNewChat(
  conversation: IChatSessionDocument,
): Promise<void> {
  const filter = {
    sessionId: conversation._id as Types.ObjectId,
    orgId: conversation.orgId,
  };
  try {
    await ChatSessionMessage.deleteMany(filter);
    await ChatSession.deleteOne({ _id: conversation._id, orgId: conversation.orgId });
  } catch (error) {
    logger.error('Failed to remove a chat whose share was refused', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
