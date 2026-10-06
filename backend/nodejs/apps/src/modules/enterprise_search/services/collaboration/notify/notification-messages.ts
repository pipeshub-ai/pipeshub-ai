import { NotificationBrokerMessage } from '../../../../notification/utils/notification-payload.resolver';
import { EmailIntent } from '../../../../notification/service/notification-email.dispatcher';
import { AccessLevel, ConversationRef } from '../domain/types';
import { CollaborationEvent } from './events';

/** Mirrors the frontend's chat URL: agent conversations carry `agentId`. */
export const buildChatRedirect = (ref: ConversationRef): string =>
  `/chat?conversationId=${encodeURIComponent(ref.conversationId)}${
    ref.kind === 'agent' ? `&agentId=${encodeURIComponent(ref.agentKey)}` : ''
  }`;

export const coalesceKeyOf = (sessionId: string): string =>
  `chat.activity:${sessionId}`;

type NotifiableEvent = Exclude<CollaborationEvent, { type: 'chat.unshared' }>;

/**
 * English fallbacks; the client renders from `type`. They never carry the chat title or any
 * content (F-12).
 */
const TEXT: Record<
  NotifiableEvent['type'],
  { title: string; message: string }
> = {
  'chat.shared': {
    title: 'Conversation shared with you',
    message: 'A conversation was shared with you.',
  },
  'chat.accessChanged': {
    title: 'Conversation access updated',
    message: 'You can now continue a shared conversation.',
  },
  'chat.ownershipTransferred': {
    title: 'Conversation ownership transferred',
    message: 'Ownership of a conversation was transferred.',
  },
  'chat.deleted': {
    title: 'Conversation deleted',
    message: 'A conversation you contributed to was deleted.',
  },
  'chat.mentioned': {
    title: 'You were mentioned',
    message: 'You were mentioned in a conversation.',
  },
  'chat.activity': {
    title: 'New activity in a shared conversation',
    message: 'There is new activity in a shared conversation.',
  },
};

/**
 * Per recipient the unique index is `{assignedTo, dedupeKey}`. Share, access change and transfer
 * are keyed by the `aclVersion` their write produced, so the same change redelivered is dropped
 * while a share after an unshare is a new occurrence. A mention is one row per recipient keyed by
 * its message, so a redelivered publish adds nothing. A deleted session never recurs. Email for
 * such a loop is held to one a day by the dispatcher (F-12).
 */
function dedupeKeyOf(
  event: NotifiableEvent,
  recipientUserId: string | undefined,
): string | undefined {
  switch (event.type) {
    case 'chat.mentioned':
      return `${event.type}:${event.messageId}:${recipientUserId ?? ''}`;
    case 'chat.shared':
    case 'chat.accessChanged':
    case 'chat.ownershipTransferred':
      return `${event.type}:${event.sessionId}:${String(event.aclVersion)}`;
    case 'chat.deleted':
      return `${event.type}:${event.sessionId}`;
    case 'chat.activity':
      return undefined;
  }
}

function accessLevelOf(event: NotifiableEvent): AccessLevel | undefined {
  switch (event.type) {
    case 'chat.shared':
    case 'chat.accessChanged':
      return event.accessLevel;
    case 'chat.ownershipTransferred':
      return 'write';
    case 'chat.deleted':
    case 'chat.activity':
    case 'chat.mentioned':
      return undefined;
  }
}

export function toNotificationMessage(
  event: NotifiableEvent,
  recipientUserIds: readonly string[],
  emailIntent?: EmailIntent,
): NotificationBrokerMessage {
  const dedupeKey = dedupeKeyOf(event, recipientUserIds[0]);
  const accessLevel = accessLevelOf(event);
  return {
    orgId: event.orgId,
    type: event.type,
    recipientUserIds: [...recipientUserIds],
    ...TEXT[event.type],
    severity: 'info',
    redirectLink: buildChatRedirect(event.ref),
    payload: {
      sessionId: event.sessionId,
      kind: event.ref.kind,
      actorUserId: event.actorUserId,
      ...(accessLevel !== undefined && { accessLevel }),
      ...(event.type === 'chat.shared' &&
        event.note !== undefined && { note: event.note }),
      ...(event.type === 'chat.activity' && { count: 1 }),
      ...(event.type === 'chat.mentioned' && { messageId: event.messageId }),
    },
    ...(dedupeKey !== undefined && { dedupeKey }),
    ...(event.type === 'chat.activity' && {
      coalesceKey: coalesceKeyOf(event.sessionId),
    }),
    ...(emailIntent && { emailIntent }),
  };
}
