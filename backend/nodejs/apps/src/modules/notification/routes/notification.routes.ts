import { NextFunction, RequestHandler, Response, Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../config';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { requireFlag } from '../../../libs/middlewares/require-flag.middleware';
import { COLLAB_FLAG_KEYS } from '../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { COLLAB_TYPES } from '../../enterprise_search/services/collaboration/collab.types';
import { ConversationGuards } from '../../enterprise_search/services/collaboration/http/conversation-guards';
import { IChatNotificationContext } from '../../enterprise_search/services/collaboration/notify/chat-notification-context';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import { MongoNotificationPreferencesRepository } from '../repository/notification-preferences.repository';
import { resolveNotificationAuthContext } from '../utils/notification-api.utils';
import {
  getNotificationPreferences,
  markTipSeen,
  muteSession,
  unmuteSession,
  updateNotificationPreferences,
} from '../controllers/notification-preferences.controller';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  listNotifications,
  getNotificationStats,
  markAllRead,
  markRead,
  markUnread,
  archiveNotification,
  unarchiveNotification,
  deleteNotification,
} from '../controllers/notification.controller';
import {
  listNotificationsSchema,
  notificationStatsSchema,
  markAllReadSchema,
  markReadSchema,
  markUnreadSchema,
  archiveNotificationSchema,
  unarchiveNotificationSchema,
  deleteNotificationSchema,
  getPreferencesSchema,
  markTipSeenSchema,
  mutedSessionParamsSchema,
  updatePreferencesSchema,
} from '../validators/notification.validators';

/**
 * The read guard locates a conversation by `:conversationId` and, for an agent chat, `:agentKey`.
 * Mute routes carry only the session id, so the kind is looked up first; the guard still decides access.
 */
function authorizeSessionRead(guards: ConversationGuards): RequestHandler {
  const chat = guards.authorize('read', 'chat');
  const agent = guards.authorize('read', 'agent');
  return async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { sessionId } = req.params;
      if (sessionId === undefined) {
        throw new BadRequestError('Session id is required');
      }
      req.params.conversationId = sessionId;
      const orgId = resolveNotificationAuthContext(req.user)?.orgOid;
      const session = orgId
        ? await ChatSession.findOne({ _id: sessionId, orgId })
            .select('sessionType agentKey')
            .lean<{ sessionType?: string; agentKey?: string }>()
        : null;
      if (
        session?.sessionType === 'agent' &&
        session.agentKey !== undefined &&
        session.agentKey !== ''
      ) {
        req.params.agentKey = session.agentKey;
        await agent(req, res, next);
        return;
      }
      await chat(req, res, next);
    } catch (err) {
      next(err);
    }
  };
}

export function createNotificationRouter(
  userManagerContainer: Container,
): Router {
  const router = Router();
  const authMiddleware =
    userManagerContainer.get<AuthMiddleware>('AuthMiddleware');
  const auth = authMiddleware.authenticate.bind(authMiddleware);
  const guards = userManagerContainer.get<ConversationGuards>(
    COLLAB_TYPES.ConversationGuards,
  );
  const preferences = new MongoNotificationPreferencesRepository();
  // Chat notification preferences exist only with collaborative chats; flag off they 404 like the other PH-06 routes.
  const collab = requireFlag(
    userManagerContainer.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags),
    COLLAB_FLAG_KEYS.collaborativeChats,
  );

  const mentions = requireFlag(
    userManagerContainer.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags),
    COLLAB_FLAG_KEYS.chatMentions,
  );

  router.get(
    '/preferences',
    collab,
    auth,
    ValidationMiddleware.validate(getPreferencesSchema),
    getNotificationPreferences(preferences),
  );

  router.patch(
    '/preferences',
    collab,
    auth,
    ValidationMiddleware.validate(updatePreferencesSchema),
    updateNotificationPreferences(preferences),
  );

  router.patch(
    '/preferences/tips',
    collab,
    mentions,
    auth,
    ValidationMiddleware.validate(markTipSeenSchema),
    markTipSeen(preferences),
  );

  router.put(
    '/preferences/muted-sessions/:sessionId',
    collab,
    auth,
    ValidationMiddleware.validate(mutedSessionParamsSchema),
    authorizeSessionRead(guards),
    muteSession(preferences),
  );

  router.delete(
    '/preferences/muted-sessions/:sessionId',
    collab,
    auth,
    ValidationMiddleware.validate(mutedSessionParamsSchema),
    unmuteSession(preferences),
  );

  router.get(
    '/',
    auth,
    ValidationMiddleware.validate(listNotificationsSchema),
    listNotifications(
      userManagerContainer.isBound(COLLAB_TYPES.ChatNotificationContext)
        ? userManagerContainer.get<IChatNotificationContext>(
            COLLAB_TYPES.ChatNotificationContext,
          )
        : undefined,
    ),
  );

  router.get(
    '/stats',
    auth,
    ValidationMiddleware.validate(notificationStatsSchema),
    getNotificationStats,
  );

  router.patch(
    '/read-all',
    auth,
    ValidationMiddleware.validate(markAllReadSchema),
    markAllRead,
  );

  router.patch(
    '/:id/read',
    auth,
    ValidationMiddleware.validate(markReadSchema),
    markRead,
  );

  router.patch(
    '/:id/unread',
    auth,
    ValidationMiddleware.validate(markUnreadSchema),
    markUnread,
  );

  router.patch(
    '/:id/archive',
    auth,
    ValidationMiddleware.validate(archiveNotificationSchema),
    archiveNotification,
  );

  router.patch(
    '/:id/unarchive',
    auth,
    ValidationMiddleware.validate(unarchiveNotificationSchema),
    unarchiveNotification,
  );

  router.delete(
    '/:id',
    auth,
    ValidationMiddleware.validate(deleteNotificationSchema),
    deleteNotification,
  );

  return router;
}
