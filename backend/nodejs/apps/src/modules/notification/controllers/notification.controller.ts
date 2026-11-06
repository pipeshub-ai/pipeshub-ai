import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { getNotificationRepository } from '../../../libs/repositories/notification';
import {
  clampPageSize,
  decodeCursor,
  InvalidNotificationCursorError,
  paginateResults,
  resolveNotificationAuthContext,
} from '../utils/notification-api.utils';

/** Container key of an `INotificationContextResolver`; unbound, rows are returned as stored. */
export const NOTIFICATION_CONTEXT_RESOLVER = Symbol.for(
  'notification.ContextResolver',
);

/** Adds display context to notification rows, keyed by row index. Never throws. */
export interface INotificationContextResolver {
  resolve(
    req: AuthenticatedUserRequest,
    rows: readonly { type: string; payload?: Record<string, unknown> }[],
  ): Promise<ReadonlyMap<number, object>>;
}

/** Without a context resolver the response is exactly the stored rows. */
export const listNotifications =
  (context?: INotificationContextResolver) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const authContext = resolveNotificationAuthContext(req.user);
      if (!authContext) {
        res.status(401).json({ message: 'Unauthorized' });
        return;
      }
      const scope = authContext;

      const notificationStatus =
        typeof req.query.status === 'string' ? req.query.status : null;
      const limit = clampPageSize(req.query.limit);

      let cursor: { createdAt: Date; id: string } | undefined;
      const rawCursor = req.query.cursor;
      if (rawCursor !== undefined && rawCursor !== '') {
        try {
          const decoded = decodeCursor(rawCursor as string);
          cursor = { createdAt: decoded.createdAt, id: decoded._id };
        } catch (err) {
          if (err instanceof InvalidNotificationCursorError) {
            res.status(400).json({ message: 'Invalid cursor' });
            return;
          }
          throw err;
        }
      }

      const rows = await getNotificationRepository().list(scope, {
        status: notificationStatus,
        cursor,
        limit,
      });

      const page = paginateResults(rows, limit);
      const contexts = context
        ? await context.resolve(req, page.notifications)
        : new Map<number, object>();
      const notifications =
        contexts.size === 0
          ? page.notifications
          : page.notifications.map((row, index) => {
              const ctx = contexts.get(index);
              return ctx ? { ...row, context: ctx } : row;
            });
      res.json({
        notifications,
        cursor: page.cursor,
        hasMore: page.hasMore,
      });
    } catch (err) {
      next(err);
    }
  };

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
    const scope = authContext;
    const {
      unread: unreadCount,
      read: readCount,
      archived: archivedCount,
    } = await getNotificationRepository().counts(scope);
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
    const scope = authContext;
    const modifiedCount = await getNotificationRepository().markAllRead(scope);
    res.json({ success: true, modifiedCount });
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
    const scope = authContext;
    const doc = await getNotificationRepository().setStatus(
      scope,
      req.params.id as string,
      'read',
    );
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
    const scope = authContext;
    const doc = await getNotificationRepository().setStatus(
      scope,
      req.params.id as string,
      'unread',
    );
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
    const scope = authContext;
    const doc = await getNotificationRepository().setStatus(
      scope,
      req.params.id as string,
      'archived',
    );
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
    const scope = authContext;
    const doc = await getNotificationRepository().unarchive(
      scope,
      req.params.id as string,
    );
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
    const scope = authContext;
    const doc = await getNotificationRepository().softDelete(
      scope,
      req.params.id as string,
    );
    if (!doc) {
      res.status(404).json({ message: 'Notification not found' });
      return;
    }
    res.json({ success: true });
  } catch (err) {
    next(err);
  }
}
