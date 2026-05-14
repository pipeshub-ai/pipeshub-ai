import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Notifications } from '../schema/notification.schema';

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
        if (!userId || !mongoose.isValidObjectId(userId)) {
          res.status(401).json({ message: 'Unauthorized' });
          return;
        }
        const oid = new mongoose.Types.ObjectId(userId);
        const notifications = await Notifications.find({
          assignedTo: oid,
          isDeleted: false,
        })
          .sort({ createdAt: -1 })
          .limit(50)
          .lean();
        res.json({ notifications });
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
        if (!userId || !mongoose.isValidObjectId(userId) || !mongoose.isValidObjectId(id)) {
          res.status(400).json({ message: 'Invalid request' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const doc = await Notifications.findOneAndUpdate(
          {
            _id: new mongoose.Types.ObjectId(id),
            assignedTo: userOid,
            isDeleted: false,
          },
          { $set: { status: 'Read' } },
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
        if (!userId || !mongoose.isValidObjectId(userId) || !mongoose.isValidObjectId(id)) {
          res.status(400).json({ message: 'Invalid request' });
          return;
        }
        const userOid = new mongoose.Types.ObjectId(userId);
        const doc = await Notifications.findOneAndUpdate(
          {
            _id: new mongoose.Types.ObjectId(id),
            assignedTo: userOid,
            isDeleted: false,
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
