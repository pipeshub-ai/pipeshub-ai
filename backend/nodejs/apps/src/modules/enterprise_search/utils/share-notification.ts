import { Logger } from '../../../libs/services/logger.service';
import { getNotificationRepository } from '../../../libs/repositories/notification';
import { getUserRepository } from '../../../libs/repositories/user';
import { NotificationContainer } from '../../notification/container/notification.container';

const logger = Logger.getInstance({ service: 'Chat Share Notifications' });

export const CHAT_SHARED_NOTIFICATION_TYPE = 'chatShared';

const displayName = (user: {
  fullName?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
}): string => {
  const fullName = user.fullName?.trim();
  if (fullName) return fullName;
  const parts = [user.firstName, user.lastName]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(' ')
    .trim();
  return parts || user.email?.trim() || '';
};

/** Where the recipient opens the chat: an agent chat needs its agent in the URL. */
export const sharedChatLink = (
  conversationId: string,
  agentKey?: string,
): string => {
  const query = new URLSearchParams();
  if (agentKey) query.set('agentId', agentKey);
  query.set('conversationId', conversationId);
  return `/chat/?${query.toString()}`;
};

/**
 * Puts "X shared a chat with you" in the inbox of each user newly given access, and pushes it to
 * any open session. Never throws: the share is already saved, and the chat still shows up under
 * "shared with me" if a notification cannot be written.
 */
export const notifyChatShared = async ({
  orgId,
  ownerId,
  recipientIds,
  conversationId,
  chatTitle,
  agentKey,
}: {
  orgId: string;
  ownerId: string;
  recipientIds: string[];
  conversationId: string;
  chatTitle?: string;
  agentKey?: string;
}): Promise<void> => {
  const targets = [...new Set(recipientIds)].filter((id) => id !== ownerId);
  if (targets.length === 0) return;

  try {
    const owner = await getUserRepository().getAny(orgId, ownerId);
    const sharerName = (owner && displayName(owner)) || undefined;
    const sharer = sharerName || 'A colleague';
    const kind = agentKey ? 'an agent chat' : 'a chat';
    const title = `${sharer} shared ${kind} with you`;
    const message = chatTitle?.trim()
      ? `You can now open "${chatTitle.trim()}".`
      : 'You can now open it from "Shared with me".';
    const redirectLink = sharedChatLink(conversationId, agentKey);

    await Promise.all(
      targets.map(async (recipientId) => {
        try {
          const saved = await getNotificationRepository().insert({
            orgId,
            assignedTo: recipientId,
            type: CHAT_SHARED_NOTIFICATION_TYPE,
            title,
            message,
            severity: 'info',
            status: 'unread',
            originService: 'AI Service',
            redirectLink,
            // The inbox shows these in the viewer's language; title and message above are the English fallback.
            payload: {
              conversationId,
              sharedBy: ownerId,
              ...(sharerName ? { sharerName } : {}),
              ...(chatTitle?.trim() ? { chatTitle: chatTitle.trim() } : {}),
              ...(agentKey ? { agentKey } : {}),
            },
            isDeleted: false,
          });
          NotificationContainer.getNotificationService()?.sendToUser(
            recipientId,
            'newNotification',
            saved,
          );
        } catch (error: unknown) {
          logger.warn('Chat share notification failed', {
            conversationId,
            recipientId,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }),
    );
  } catch (error: unknown) {
    logger.warn('Chat share notification failed', {
      conversationId,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};
