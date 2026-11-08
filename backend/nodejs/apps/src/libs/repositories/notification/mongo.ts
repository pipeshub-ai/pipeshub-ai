import mongoose from 'mongoose';
import { Notifications } from '../../../modules/notification/schema/notification.schema';
import { retentionCutoff } from '../../../modules/notification/utils/notification-api.utils';
import { toPlain } from '../plain';
import type {
  NewNotification,
  NotificationCursor,
  NotificationRepository,
  NotificationRow,
  NotificationScope,
} from './types';

const oid = (id: string): mongoose.Types.ObjectId =>
  new mongoose.Types.ObjectId(id);

function retentionFilter(
  scope: NotificationScope,
  notificationStatus: string | null,
  includeArchived = false,
): Record<string, unknown> {
  return {
    orgId: oid(scope.orgId),
    assignedTo: oid(scope.userId),
    isDeleted: false,
    createdAt: { $gte: retentionCutoff() },
    ...(notificationStatus
      ? { status: notificationStatus }
      : includeArchived
        ? {}
        : { status: { $ne: 'archived' } }),
  };
}

function cursorFilter(cursor: NotificationCursor): Record<string, unknown> {
  return {
    $or: [
      { createdAt: { $lt: cursor.createdAt } },
      { createdAt: cursor.createdAt, _id: { $lt: oid(cursor.id) } },
    ],
  };
}

const rowOf = (doc: unknown): NotificationRow => toPlain<NotificationRow>(doc);

export class MongoNotificationRepository implements NotificationRepository {
  async insert(row: NewNotification): Promise<NotificationRow> {
    return rowOf(await Notifications.create(row));
  }

  async insertMany(rows: NewNotification[]): Promise<NotificationRow[]> {
    const saved = await Notifications.create(rows);
    return saved.map(rowOf);
  }

  async list(
    scope: NotificationScope,
    query: {
      status: string | null;
      cursor?: NotificationCursor;
      limit: number;
    },
  ): Promise<NotificationRow[]> {
    const filter = {
      ...retentionFilter(scope, query.status),
      ...(query.cursor ? cursorFilter(query.cursor) : {}),
    };
    const rows = await Notifications.find(filter)
      .sort({ createdAt: -1, _id: -1 })
      .limit(query.limit + 1)
      .lean();
    return rows.map(rowOf);
  }

  async counts(
    scope: NotificationScope,
  ): Promise<{ unread: number; read: number; archived: number }> {
    const base = retentionFilter(scope, null);
    const [unread, read, archived] = await Promise.all([
      Notifications.countDocuments({ ...base, status: 'unread' }),
      Notifications.countDocuments({ ...base, status: 'read' }),
      Notifications.countDocuments({ ...base, status: 'archived' }),
    ]);
    return { unread, read, archived };
  }

  async markAllRead(scope: NotificationScope): Promise<number> {
    const result = await Notifications.updateMany(
      retentionFilter(scope, 'unread'),
      { $set: { status: 'read' } },
    );
    return result.modifiedCount;
  }

  async setStatus(
    scope: NotificationScope,
    id: string,
    status: 'read' | 'unread' | 'archived',
  ): Promise<NotificationRow | null> {
    const doc = await Notifications.findOneAndUpdate(
      { _id: oid(id), ...retentionFilter(scope, null) },
      { $set: { status } },
      { returnDocument: 'after' },
    ).lean();
    return doc ? rowOf(doc) : null;
  }

  async unarchive(
    scope: NotificationScope,
    id: string,
  ): Promise<NotificationRow | null> {
    const doc = await Notifications.findOneAndUpdate(
      { _id: oid(id), ...retentionFilter(scope, 'archived') },
      { $set: { status: 'read' } },
      { returnDocument: 'after' },
    ).lean();
    return doc ? rowOf(doc) : null;
  }

  async softDelete(
    scope: NotificationScope,
    id: string,
  ): Promise<NotificationRow | null> {
    const doc = await Notifications.findOneAndUpdate(
      { _id: oid(id), ...retentionFilter(scope, null, true) },
      { $set: { isDeleted: true, deletedBy: oid(scope.userId) } },
      { returnDocument: 'after' },
    ).lean();
    return doc ? rowOf(doc) : null;
  }
}
