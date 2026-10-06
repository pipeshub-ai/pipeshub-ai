import { EmailIntent } from '../../../../notification/service/notification-email.dispatcher';
import { AccessLevel, ConversationRef, Principal } from '../domain/types';

interface CollaborationEventBase {
  orgId: string;
  sessionId: string;
  ref: ConversationRef;
  /** The user who made the change; never a recipient. */
  actorUserId: string;
}

/** The session's `aclVersion` after the write that caused the event: what makes a notification per occurrence rather than per session. */
interface AclStamped {
  aclVersion: number;
}

/** A principal was added. `emailIntent` is set only for a direct user recipient whose email gates are all open. */
export interface ChatSharedEvent extends CollaborationEventBase, AclStamped {
  type: 'chat.shared';
  principal: Principal;
  accessLevel: AccessLevel;
  /** Raw hand-over note, at most 500 characters. */
  note?: string;
  emailIntent?: EmailIntent;
}

/** Upgrade only (`read` to `write`); a downgrade is silent. */
export interface ChatAccessChangedEvent
  extends CollaborationEventBase,
    AclStamped {
  type: 'chat.accessChanged';
  principal: Principal;
  accessLevel: 'write';
}

/** Recipients: the new owner and the previous owner. `emailIntent` is for the new owner only. */
export interface ChatOwnershipTransferredEvent
  extends CollaborationEventBase,
    AclStamped {
  type: 'chat.ownershipTransferred';
  newOwnerUserId: string;
  previousOwnerUserId: string;
  emailIntent?: EmailIntent;
}

/** Internal: nobody is told, but the principal's earlier unread `chat.shared` is archived. */
export interface ChatUnsharedEvent extends CollaborationEventBase {
  type: 'chat.unshared';
  principal: Principal;
}

/** The conversation was deleted; the recipients are its past authors, already resolved. */
export interface ChatDeletedEvent extends CollaborationEventBase {
  type: 'chat.deleted';
  recipientUserIds: readonly string[];
}

/** A turn finished; the recipients are already filtered by read state and preferences. */
export interface ChatActivityEvent extends CollaborationEventBase {
  type: 'chat.activity';
  recipientUserIds: readonly string[];
}

/**
 * A stored `user_query` or `note` mentioned people. `principals` are already restricted to the chat's
 * participants (a team expands to its members at publish time); the actor is never a recipient.
 * `emailIntents` is keyed by directly mentioned user and holds only those whose gates are open.
 */
export interface ChatMentionedEvent extends CollaborationEventBase {
  type: 'chat.mentioned';
  messageId: string;
  principals: readonly Principal[];
  emailIntents?: ReadonlyMap<string, EmailIntent>;
}

export type CollaborationEvent =
  | ChatSharedEvent
  | ChatAccessChangedEvent
  | ChatOwnershipTransferredEvent
  | ChatUnsharedEvent
  | ChatDeletedEvent
  | ChatActivityEvent
  | ChatMentionedEvent;

export type CollaborationEventType = CollaborationEvent['type'];
