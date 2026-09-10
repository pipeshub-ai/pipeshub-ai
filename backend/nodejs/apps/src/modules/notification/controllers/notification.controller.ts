import { Response, NextFunction } from 'express';
import mongoose from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Notifications } from '../schema/notification.schema';
import {
  buildCursorFilter,
  buildRetentionFilter,
  clampPageSize,
  decodeCursor,
  InvalidNotificationCursorError,
  paginateResults,
  resolveNotificationAuthContext,
} from '../utils/notification-api.utils';

export async function listNotifications(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;

    const notificationStatus =
      typeof req.query.status === 'string' ? req.query.status : null;
    const limit = clampPageSize(req.query.limit);
    const baseFilter = buildRetentionFilter(
      orgOid,
      userOid,
      notificationStatus,
    );

    let cursorFilter: Record<string, unknown> = {};
    const rawCursor = req.query.cursor;
    if (rawCursor !== undefined && rawCursor !== '') {
      try {
        cursorFilter = buildCursorFilter(decodeCursor(rawCursor as string));
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
}

export async function getNotificationStats(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const base = buildRetentionFilter(orgOid, userOid, null);
    const [unreadCount, readCount, archivedCount] = await Promise.all([
      Notifications.countDocuments({ ...base, status: 'unread' }),
      Notifications.countDocuments({ ...base, status: 'read' }),
      Notifications.countDocuments({ ...base, status: 'archived' }),
    ]);
    res.json({ unreadCount, readCount, archivedCount });
  } catch (err) {
    next(err);
  }
}

export async function markAllRead(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const filter = buildRetentionFilter(orgOid, userOid, 'unread');
    const result = await Notifications.updateMany(filter, { $set: { status: 'read' } });
    res.json({ success: true, modifiedCount: result.modifiedCount });
  } catch (err) {
    next(err);
  }
}

export async function markRead(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const doc = await Notifications.findOneAndUpdate(
      {
        _id: new mongoose.Types.ObjectId(req.params.id),
        ...buildRetentionFilter(orgOid, userOid, null),
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
}

export async function markUnread(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const doc = await Notifications.findOneAndUpdate(
      {
        _id: new mongoose.Types.ObjectId(req.params.id),
        ...buildRetentionFilter(orgOid, userOid, null),
      },
      { $set: { status: 'unread' } },
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
}

export async function archiveNotification(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const doc = await Notifications.findOneAndUpdate(
      {
        _id: new mongoose.Types.ObjectId(req.params.id),
        ...buildRetentionFilter(orgOid, userOid, null),
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
}

export async function unarchiveNotification(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const doc = await Notifications.findOneAndUpdate(
      {
        _id: new mongoose.Types.ObjectId(req.params.id),
        ...buildRetentionFilter(orgOid, userOid, 'archived'),
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
}

export async function deleteNotification(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const authContext = resolveNotificationAuthContext(req.user);
    if (!authContext) {
      res.status(401).json({ message: 'Unauthorized' });
      return;
    }
    const { userOid, orgOid } = authContext;
    const doc = await Notifications.findOneAndUpdate(
      {
        _id: new mongoose.Types.ObjectId(req.params.id),
        ...buildRetentionFilter(orgOid, userOid, null, true),
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
}
