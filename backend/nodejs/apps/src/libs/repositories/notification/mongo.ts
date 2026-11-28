import mongoose from 'mongoose';
import { Notifications } from '../../../modules/notification/schema/notification.schema';
import { retentionCutoff } from '../../../modules/notification/utils/notification-api.utils';
import { toPlain } from '../plain';
import { prepareNotification } from './dates';
import type {
  InsertNewResult,
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
    // a scheduled notification is invisible to every read and change until its time
    $and: [{ $or: [{ visibleAt: null }, { visibleAt: { $lte: new Date() } }] }],
    ...(notificationStatus
      ? { status: notificationStatus }
      : includeArchived
        ? {}
        : { status: { $ne: 'archived' } }),
    ...(scope.hideChatNotices && { type: { $not: /^chat\./ } }),
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

const DUPLICATE_KEY_CODE = 11000;
const HEX_ID = /^[0-9a-f]{24}$/i;

interface WriteFailure {
  code?: unknown;
  errmsg?: unknown;
  err?: { code?: unknown; errmsg?: unknown };
}

interface InsertManyFailure extends WriteFailure {
  message?: unknown;
  writeErrors?: WriteFailure[];
  insertedDocs?: unknown[];
}

// Mongoose copies `insertMany` write errors as plain objects, losing the
// driver's `code` getter, so the code is only on the nested `err`.
const isDuplicateKey = (e: WriteFailure): boolean =>
  (e.code ?? e.err?.code) === DUPLICATE_KEY_CODE;

const messageOf = (e: InsertManyFailure): string =>
  [e.err?.errmsg, e.errmsg, e.message].find(
    (m): m is string => typeof m === 'string',
  ) ?? 'write error';

/**
 * The row to store, with both timestamps set here: the schema's `timestamps: true` would stamp `updatedAt` with the time of the
 * insert (or with `createdAt`), and a scheduled row's `updatedAt` is its `visibleAt`.
 */
function stamped(row: NewNotification, now: Date): NewNotification {
  const r = prepareNotification(row) as Record<string, unknown>;
  const createdAt = (r.createdAt as Date | undefined) ?? now;
  return {
    ...r,
    createdAt,
    updatedAt: r.updatedAt ?? createdAt,
  } as unknown as NewNotification;
}

export class MongoNotificationRepository implements NotificationRepository {
  async insert(row: NewNotification): Promise<NotificationRow> {
    const doc = prepareNotification(row);
    // a row with no time and no id of its own is stored exactly as it always was (one with an id is idempotent by it, in insertMany): the schema stamps both times
    if (
      doc._id === undefined &&
      doc.visibleAt === undefined &&
      doc.createdAt === undefined
    )
      return rowOf(await Notifications.create(doc));
    return (await this.insertMany([row]))[0] as NotificationRow;
  }

  async insertMany(rows: NewNotification[]): Promise<NotificationRow[]> {
    const now = new Date();
    // every row is checked before any is written, so a refused row leaves the others unstored
    const docs = rows.map((r) => stamped(r, now));
    // the schema's own checks (required fields, enums) run on every document here: create() would otherwise store the first row of a batch and refuse a later one
    for (const d of docs) await new Notifications(d).validate();
    const out = new Array<NotificationRow>(rows.length);
    const fresh: number[] = [];
    for (const [i, r] of rows.entries()) {
      if (r._id === undefined) {
        fresh.push(i);
        continue;
      }
      // a row with its own id is idempotent: the duplicate key of a republish returns the stored row
      try {
        out[i] = rowOf(
          (
            await Notifications.create([docs[i] as NewNotification], {
              timestamps: false,
            })
          )[0],
        );
      } catch (e) {
        if ((e as { code?: number }).code !== 11000) throw e;
        const existing = await Notifications.findOne({
          _id: oid(r._id as string),
          orgId: oid(r.orgId),
        }).lean();
        if (!existing) throw e;
        out[i] = rowOf(existing);
      }
    }
    if (fresh.length > 0) {
      const saved = await Notifications.create(
        fresh.map((i) => docs[i] as NewNotification),
        { timestamps: false },
      );
      for (const [j, i] of fresh.entries()) out[i] = rowOf(saved[j]);
    }
    return out;
  }

  async exists(scope: NotificationScope, id: string): Promise<boolean> {
    if (
      !mongoose.isValidObjectId(id) ||
      !mongoose.isValidObjectId(scope.orgId) ||
      !mongoose.isValidObjectId(scope.userId)
    )
      return false;
    return (
      (await Notifications.countDocuments({
        _id: oid(id),
        orgId: oid(scope.orgId),
        assignedTo: oid(scope.userId),
      })) > 0
    );
  }

  async insertNew(rows: readonly NewNotification[]): Promise<InsertNewResult> {
    if (rows.length === 0) {
      return { inserted: [], duplicates: 0, refused: [] };
    }
    const now = new Date();
    // as in insertMany: every row is stamped and checked before any is written, so a refused row leaves the others unstored
    const docs = rows.map((r) => stamped(r, now));
    for (const d of docs) await new Notifications(d).validate();
    try {
      // a taken `_id` and a taken `(assignedTo, dedupeKey)` both fail with a duplicate key, and are counted as delivered before
      const saved = await Notifications.insertMany(docs, {
        ordered: false,
        timestamps: false,
      });
      return { inserted: saved.map(rowOf), duplicates: 0, refused: [] };
    } catch (error) {
      const failure = error as InsertManyFailure;
      // Mongoose attaches `insertedDocs` to every failure; with no per-document
      // errors (an empty list after an election) the whole write failed and
      // `insertedDocs` lists docs that were never stored, so it is thrown.
      const perDocument = failure.writeErrors ?? [];
      if (
        !Array.isArray(failure.insertedDocs) ||
        (perDocument.length === 0 && !isDuplicateKey(failure))
      ) {
        throw error;
      }
      const writeErrors = perDocument.length > 0 ? perDocument : [failure];
      const refused = writeErrors.filter((e) => !isDuplicateKey(e));
      return {
        inserted: failure.insertedDocs.map(rowOf),
        duplicates: writeErrors.length - refused.length,
        refused: refused.map(messageOf),
      };
    }
  }

  async deleteByIds(orgId: string, ids: readonly string[]): Promise<number> {
    const valid = ids.filter((id) => HEX_ID.test(id));
    if (valid.length === 0 || !HEX_ID.test(orgId)) {
      return 0;
    }
    const result = await Notifications.deleteMany({
      orgId: oid(orgId),
      _id: { $in: valid.map(oid) },
    });
    return result.deletedCount;
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

  async eraseForConversation(
    orgId: string,
    conversationId: string,
  ): Promise<number> {
    if (!mongoose.Types.ObjectId.isValid(orgId)) return 0;
    const deleted = await Notifications.deleteMany(
      { orgId: oid(orgId), 'payload.conversationId': conversationId },
      { writeConcern: { w: 'majority' } },
    );
    return deleted.deletedCount;
  }

  async eraseForOrg(orgId: string, limit: number): Promise<number> {
    if (!mongoose.Types.ObjectId.isValid(orgId) || limit <= 0) return 0;
    const batch = await Notifications.find({ orgId: oid(orgId) }, { _id: 1 })
      .limit(limit)
      .lean<Array<{ _id: mongoose.Types.ObjectId }>>();
    if (batch.length === 0) return 0;
    const deleted = await Notifications.deleteMany(
      { _id: { $in: batch.map((row) => row._id) }, orgId: oid(orgId) },
      { writeConcern: { w: 'majority' } },
    );
    return deleted.deletedCount;
  }
}
