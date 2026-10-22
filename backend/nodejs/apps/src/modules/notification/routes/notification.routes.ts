import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../config';
import { conversationExtensions } from '../../enterprise_search/services/conversation/conversation-extensions';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  INotificationContextResolver,
  listNotifications,
  NOTIFICATION_CONTEXT_RESOLVER,
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
} from '../validators/notification.validators';

export function createNotificationRouter(
  userManagerContainer: Container,
): Router {
  const router = Router();
  const authMiddleware =
    userManagerContainer.get<AuthMiddleware>('AuthMiddleware');
  const auth = authMiddleware.authenticate.bind(authMiddleware);
  conversationExtensions().routes.mountNotificationRoutes(
    router,
    userManagerContainer,
  );

  router.get(
    '/',
    auth,
    ValidationMiddleware.validate(listNotificationsSchema),
    listNotifications(
      userManagerContainer.isBound(NOTIFICATION_CONTEXT_RESOLVER)
        ? userManagerContainer.get<INotificationContextResolver>(
            NOTIFICATION_CONTEXT_RESOLVER,
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
