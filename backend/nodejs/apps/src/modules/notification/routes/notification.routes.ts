import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
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
} from '../validators/notification.validators';

export function createNotificationRouter(
  userManagerContainer: Container,
): Router {
  const router = Router();
  const authMiddleware = userManagerContainer.get<AuthMiddleware>('AuthMiddleware');
  const auth = authMiddleware.authenticate.bind(authMiddleware);

  router.get(
    '/',
    auth,
    ValidationMiddleware.validate(listNotificationsSchema),
    listNotifications,
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
  )

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
