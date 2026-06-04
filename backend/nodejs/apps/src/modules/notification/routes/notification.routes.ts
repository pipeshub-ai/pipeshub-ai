import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Notifications } from '../schema/notification.schema';
import {
  buildCursorFilter,
  buildRetentionFilter,
  clampPageSize,
  decodeCursor,
  InvalidNotificationCursorError,
  paginateResults,
} from '../utils/notification-api.utils';

export function createNotificationRouter(
  userManagerContainer: Container,
): Router {
  const router = Router();
  const authMiddleware = userManagerContainer.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        let notificationStatus: string | null = null;
        if (typeof req.query.status === 'string') {
          notificationStatus = req.query.status.toLowerCase();
        }

        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        if (!notificationStatus || !['read', 'unread', 'archived'].includes(notificationStatus)) {
          notificationStatus = null;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const limit = clampPageSize(req.query.limit);
        const baseFilter = buildRetentionFilter(userOid, notificationStatus);

        let cursorFilter: Record<string, unknown> = {};
        const rawCursor = req.query.cursor;
        if (rawCursor !== undefined && rawCursor !== '') {
          if (typeof rawCursor !== 'string') {
            res.status(400).json({ message: 'Invalid cursor' });
            return;
          }
          try {
            cursorFilter = buildCursorFilter(decodeCursor(rawCursor));
          } catch (err) {
            if (err instanceof InvalidNotificationCursorError) {
              res.status(400).json({ message: 'Invalid cursor' });
              return;
            }
            throw err;
          }
        }

        const filter = { ...baseFilter, ...cursorFilter };

        const rows = await Notifications.find(filter)
          .sort({ createdAt: -1, _id: -1 })
          .limit(limit + 1)
          .lean();

        const { notifications, hasMore, cursor } = paginateResults(rows, limit);
        res.json({ notifications, cursor, hasMore });
      } catch (err) {
        next(err);
      }
    },
  );

  router.get(
    '/stats',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const base = buildRetentionFilter(userOid, null);
        const [unreadCount, readCount, archivedCount] = await Promise.all([
          Notifications.countDocuments({ ...base, status: 'unread' }),
          Notifications.countDocuments({ ...base, status: 'read' }),
          Notifications.countDocuments({ ...base, status: 'archived' }),
        ]);
        res.json({ unreadCount, readCount, archivedCount });
      } catch (err) {
        next(err);
      }
    },
  );

  router.patch(
    '/read-all',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const filter = buildRetentionFilter(userOid, 'unread');
        const result = await Notifications.updateMany(filter, { $set: { status: 'read' } });
        res.json({ success: true, modifiedCount: result.modifiedCount });
      } catch (err) {
        next(err);
      }
    },
  );

  router.patch(
    '/:id/read',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        const { id } = req.params;
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' })
          return
        }
        if (!mongoose.isValidObjectId(id)) {
          res.status(400).json({ message: 'Invalid notification id' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const doc = await Notifications.findOneAndUpdate(
          {
            _id: new mongoose.Types.ObjectId(id),
            ...buildRetentionFilter(userOid, null),
          },
          { $set: { status: 'read' } },
          { new: true },
        ).lean();
        if (!doc) {
          res.status(404).json({ message: 'Notification not found' });
          return;
        }
        res.json({ notification: doc });
      } catch (err) {
        next(err);
      }
    },
  );

  router.patch(
    '/:id/archive',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        const { id } = req.params;
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        if (!mongoose.isValidObjectId(id)) {
          res.status(400).json({ message: 'Invalid notification id' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const doc = await Notifications.findOneAndUpdate(
          {
            _id: new mongoose.Types.ObjectId(id),
            ...buildRetentionFilter(userOid, null),
          },
          { $set: { status: 'archived' } },
          { new: true },
        ).lean();
        if (!doc) {
          res.status(404).json({ message: 'Notification not found' });
          return;
        }
        res.json({ notification: doc });
      } catch (err) {
        next(err);
      }
    },
  );

  router.patch(
    '/:id/unarchive',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        const { id } = req.params;
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        if (!mongoose.isValidObjectId(id)) {
          res.status(400).json({ message: 'Invalid notification id' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const doc = await Notifications.findOneAndUpdate(
          {
            _id: new mongoose.Types.ObjectId(id),
            ...buildRetentionFilter(userOid, 'archived'),
          },
          { $set: { status: 'read' } },
          { new: true },
        ).lean();
        if (!doc) {
          res.status(404).json({ message: 'Notification not found' });
          return;
        }
        res.json({ notification: doc });
      } catch (err) {
        next(err);
      }
    },
  );

  router.delete(
    '/:id',
    authMiddleware.authenticate.bind(authMiddleware),
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      try {
        const userId = req.user?.userId;
        const { id } = req.params;
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        if (!mongoose.isValidObjectId(id)) {
          res.status(400).json({ message: 'Invalid request' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const doc = await Notifications.findOneAndUpdate(
          {
            _id: new mongoose.Types.ObjectId(id),
            ...buildRetentionFilter(userOid, null, true),
          },
          { $set: { isDeleted: true, deletedBy: userOid } },
          { new: true },
        ).lean();
        if (!doc) {
          res.status(404).json({ message: 'Notification not found' });
          return;
        }
        res.json({ success: true });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}
