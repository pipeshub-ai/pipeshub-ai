/**
 * PostgreSQL implementation of the notification repository (`DOC_STORE=postgres`). The table is partitioned by month of
 * `created_at`, so every statement carries the org and the retention cutoff: both prune partitions and keep an expired or
 * deleted row out of every read and change.
 */
import { createHash } from 'node:crypto';
import { retentionCutoff } from '../../../modules/notification/utils/notification-api.utils';
import { stripNul } from '../strip-nul';
import {
  assertOrg,
  inUnit,
  newObjectId,
  oidOf,
  registerLockKey,
} from '../pg-util';
import type {
  NewNotification,
  NotificationCursor,
  NotificationRepository,
  NotificationRow,
  NotificationScope,
  NotificationStatus,
} from './types';
import type { Importable, ImportStats, NeutralDoc } from '../import';
import { importNotificationDocs } from './pg-import';
import { prepareNotification } from './dates';
import { kernelTx } from '../pg';
import type { Tx } from '../uow';

// The table is partitioned by created_at, so no unique index can hold an id alone; a lock per (org, id) makes a second
// concurrent insert of the same id wait for the first to commit and then find its row
const ID_LOCK = registerLockKey('notification_id', 7403);

interface Row {
  id: string;
  org_id: string;
  assigned_to: string;
  type: string;
  title: string | null;
  message: string | null;
  redirect_link: string | null;
  severity: string | null;
  origin_service: string | null;
  status: NotificationStatus;
  payload: Record<string, unknown> | null;
  is_deleted: boolean;
  deleted_by: string | null;
  visible_at: Date | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS = `n.id, n.org_id, n.assigned_to, n.type, n.title, n.message, n.redirect_link, n.severity, n.origin_service, n.status,
  n.payload, n.is_deleted, n.deleted_by, n.visible_at, n.version, n.created_at, n.updated_at`;

function toRow(r: Row): NotificationRow {
  const o: Record<string, unknown> = {
    _id: r.id,
    orgId: r.org_id,
    assignedTo: r.assigned_to,
    type: r.type,
    status: r.status,
    isDeleted: r.is_deleted,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  if (r.title !== null) o.title = r.title;
  if (r.message !== null) o.message = r.message;
  if (r.redirect_link !== null) o.redirectLink = r.redirect_link;
  if (r.severity !== null) o.severity = r.severity;
  if (r.origin_service !== null) o.originService = r.origin_service;
  if (r.payload !== null) o.payload = r.payload;
  if (r.deleted_by !== null) o.deletedBy = r.deleted_by;
  if (r.visible_at !== null) o.visibleAt = r.visible_at;
  return o as unknown as NotificationRow;
}

/**
 * The conditions every read and change shares; `$1` org, `$2` user, `$3` retention cutoff, `$4` the operation's clock. A scheduled
 * row (`visible_at` set) is not there until its time. The clock is read by the application once per operation and passed in:
 * `now()` is the start of the surrounding transaction, which a unit reused by a caller may have held open for a long time, while
 * the MongoDB store compares with the time of the call.
 */
const LIVE = `n.org_id = $1 AND n.assigned_to = $2 AND NOT n.is_deleted AND n.created_at >= $3
  AND (n.visible_at IS NULL OR n.visible_at <= $4::timestamptz)`;

export class PgNotificationRepository
  implements NotificationRepository, Importable
{
  /** The migration copy: documents as they were stored (see ./pg-import.ts). */
  importBatch(
    _collection: string,
    docs: NeutralDoc[],
    tx: Tx,
  ): Promise<ImportStats> {
    return importNotificationDocs(kernelTx(tx), docs);
  }
  async insert(row: NewNotification): Promise<NotificationRow> {
    return (await this.insertMany([row]))[0] as NotificationRow;
  }

  async insertMany(rows: NewNotification[]): Promise<NotificationRow[]> {
    if (rows.length === 0) return [];
    return inUnit(undefined, { write: true }, async (k) => {
      const docs = rows.map((r) =>
        stripNul(prepareNotification(r), 'notification', 'row'),
      );
      for (const d of docs) assertOrg(k, d.orgId);
      // a row that names its own id keeps it, and is idempotent (an existing row of the org with that id is returned, nothing is written)
      const supplied = docs.map((d) => oidOf((d as { _id?: unknown })._id));
      const ids = supplied.map((s) => s ?? newObjectId());
      const named = ids.filter((_, i) => supplied[i] !== undefined);
      const have = new Map<string, Row>();
      if (named.length > 0) {
        // ascending, so two batches that share ids take their locks in the same order
        const keys = [
          ...new Set(
            named.map((id) =>
              createHash('md5')
                .update(`${k.orgId}/${id}`)
                .digest()
                .readInt32BE(0),
            ),
          ),
        ].sort((a, b) => a - b);
        for (const key of keys) await k.advisoryXactLock(ID_LOCK, key);
        const { rows: found } = await k.query<Row>(
          `/* pipeshub:platform.notification_by_ids */ SELECT ${COLS} FROM platform.notifications n WHERE n.org_id = $1 AND n.id = ANY($2::text[])`,
          [k.orgId, named],
        );
        for (const r of found) have.set(r.id, r);
      }
      // a new id named twice in one batch is inserted once; both positions get that row, as in MongoDB
      const seen = new Set(have.keys());
      const keep: number[] = [];
      for (const [i, id] of ids.entries()) {
        if (seen.has(id)) continue;
        seen.add(id);
        keep.push(i);
      }
      if (keep.length === 0) return ids.map((id) => toRow(have.get(id) as Row));
      const allDocs = docs;
      const allIds = ids;
      const docs2 = keep.map((i) => allDocs[i] as NewNotification);
      const ids2 = keep.map((i) => allIds[i] as string);
      const text = (f: (d: NewNotification) => unknown): (string | null)[] =>
        docs2.map((d) => {
          const v = f(d);
          if (v === undefined || v === null) return null;
          return typeof v === 'object'
            ? JSON.stringify(v)
            : (v as string | number | boolean).toString();
        });
      // updated_at: a scheduled row's visible_at, else its created_at, as MongoDB's timestamps stamp a new document
      const { rows: out } = await k.query<Row>(
        `/* pipeshub:platform.notification_insert */ INSERT INTO platform.notifications AS n
           (org_id, id, created_at, type, title, message, redirect_link, severity, status, origin_service, assigned_to, payload, is_deleted, deleted_by, visible_at, updated_at)
         SELECT $1::text, u.id, COALESCE(u.created_at, $2::timestamptz), u.type, u.title, u.message, u.redirect_link, u.severity, COALESCE(u.status, 'unread'), u.origin_service, u.assigned_to,
                u.payload::jsonb, COALESCE(u.is_deleted, false), u.deleted_by, u.visible_at, COALESCE(u.visible_at, u.created_at, $2::timestamptz)
           FROM unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::text[], $8::text[], $9::text[], $10::text[], $11::text[], $12::text[], $13::text[], $14::boolean[], $15::timestamptz[], $16::timestamptz[])
             AS u(id, type, title, message, redirect_link, severity, status, origin_service, assigned_to, payload, deleted_by, is_deleted, created_at, visible_at)
         RETURNING ${COLS}`,
        [
          k.orgId,
          new Date(),
          ids2,
          text((d) => d.type),
          text((d) => d.title),
          text((d) => d.message),
          text((d) => d.redirectLink),
          text((d) => d.severity),
          text((d) => d.status),
          text((d) => d.originService),
          text((d) => d.assignedTo),
          docs2.map((d) =>
            d.payload === undefined ? null : JSON.stringify(d.payload),
          ),
          text((d) => d.deletedBy),
          docs2.map((d) =>
            d.isDeleted === undefined ? null : Boolean(d.isDeleted),
          ),
          docs2.map((d) => (d.createdAt as Date | undefined) ?? null),
          docs2.map((d) => (d.visibleAt as Date | undefined) ?? null),
        ],
      );
      const byId = new Map([...have, ...out.map((r) => [r.id, r] as const)]);
      return ids.map((id) => toRow(byId.get(id) as Row));
    });
  }

  async exists(scope: NotificationScope, id: string): Promise<boolean> {
    const [user, row] = [oidOf(scope.userId), oidOf(id)];
    if (!user || !row) return false;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, scope.orgId);
      // ungated by retention, the delete flag and visibleAt; by org, user and id (the by-id index probes each month's partition once)
      const { rows } = await k.query<{ one: number }>(
        `/* pipeshub:platform.notification_exists */ SELECT 1 AS one FROM platform.notifications n WHERE n.org_id = $1 AND n.id = $2 AND n.assigned_to = $3 LIMIT 1`,
        [k.orgId, row, user],
      );
      return rows.length > 0;
    });
  }

  async list(
    scope: NotificationScope,
    query: {
      status: string | null;
      cursor?: NotificationCursor;
      limit: number;
    },
  ): Promise<NotificationRow[]> {
    const user = oidOf(scope.userId);
    const cursorId = query.cursor ? oidOf(query.cursor.id) : undefined;
    if (!user || (query.cursor && !cursorId)) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, scope.orgId);
      const values: unknown[] = [k.orgId, user, retentionCutoff(), new Date()];
      let extra = query.status
        ? ` AND n.status = $${values.push(query.status)}`
        : ` AND n.status <> 'archived'`;
      if (query.cursor) {
        const at = `$${values.push(query.cursor.createdAt)}`;
        extra += ` AND (n.created_at < ${at} OR (n.created_at = ${at} AND n.id < $${values.push(cursorId)}))`;
      }
      const { rows } = await k.query<Row>(
        `/* pipeshub:platform.notification_list */ SELECT ${COLS} FROM platform.notifications n WHERE ${LIVE}${extra}
           ORDER BY n.created_at DESC, n.id DESC LIMIT $${values.push(query.limit + 1)}`,
        values,
      );
      return rows.map(toRow);
    });
  }

  async counts(
    scope: NotificationScope,
  ): Promise<{ unread: number; read: number; archived: number }> {
    const user = oidOf(scope.userId);
    if (!user) return { unread: 0, read: 0, archived: 0 };
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, scope.orgId);
      const { rows } = await k.query<{
        unread: string;
        read: string;
        archived: string;
      }>(
        `/* pipeshub:platform.notification_counts */ SELECT count(*) FILTER (WHERE n.status = 'unread') AS unread,
           count(*) FILTER (WHERE n.status = 'read') AS read, count(*) FILTER (WHERE n.status = 'archived') AS archived
           FROM platform.notifications n WHERE ${LIVE}`,
        [k.orgId, user, retentionCutoff(), new Date()],
      );
      const c = rows[0];
      return {
        unread: Number(c?.unread),
        read: Number(c?.read),
        archived: Number(c?.archived),
      };
    });
  }

  async markAllRead(scope: NotificationScope): Promise<number> {
    const user = oidOf(scope.userId);
    if (!user) return 0;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, scope.orgId);
      const r = await k.query(
        `/* pipeshub:platform.notification_mark_all_read */ UPDATE platform.notifications n SET status = 'read', updated_at = $4
           WHERE ${LIVE} AND n.status = 'unread'`,
        [k.orgId, user, retentionCutoff(), new Date()],
      );
      return r.rowCount ?? 0;
    });
  }

  setStatus(
    scope: NotificationScope,
    id: string,
    status: 'read' | 'unread' | 'archived',
  ): Promise<NotificationRow | null> {
    return this.change(
      scope,
      id,
      `n.status <> 'archived'`,
      `status = $6`,
      status,
    );
  }

  unarchive(
    scope: NotificationScope,
    id: string,
  ): Promise<NotificationRow | null> {
    return this.change(scope, id, `n.status = 'archived'`, `status = 'read'`);
  }

  softDelete(
    scope: NotificationScope,
    id: string,
  ): Promise<NotificationRow | null> {
    return this.change(scope, id, 'true', `is_deleted = true, deleted_by = $2`);
  }

  eraseForConversation(orgId: string, conversationId: string): Promise<number> {
    if (!oidOf(orgId)) return Promise.resolve(0);
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const r = await k.query(
        `/* pipeshub:platform.notification_erase_for_conversation */ DELETE FROM platform.notifications n
           WHERE n.org_id = $1 AND n.payload->>'conversationId' = $2::text`,
        [k.orgId, conversationId],
      );
      return r.rowCount ?? 0;
    });
  }

  eraseForOrg(orgId: string, limit: number): Promise<number> {
    if (!oidOf(orgId) || limit <= 0) return Promise.resolve(0);
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const r = await k.query(
        `/* pipeshub:platform.notification_erase_for_org */ DELETE FROM platform.notifications n
           WHERE (n.org_id, n.created_at, n.id) IN
             (SELECT org_id, created_at, id FROM platform.notifications WHERE org_id = $1 LIMIT $2)`,
        [k.orgId, limit],
      );
      return r.rowCount ?? 0;
    });
  }

  private async change(
    scope: NotificationScope,
    id: string,
    guard: string,
    set: string,
    arg?: string,
  ): Promise<NotificationRow | null> {
    const [user, row] = [oidOf(scope.userId), oidOf(id)];
    if (!user || !row) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, scope.orgId);
      const now = new Date();
      const values: unknown[] = [
        k.orgId,
        user,
        retentionCutoff(),
        now,
        row,
        ...(arg === undefined ? [] : [arg]),
      ];
      const { rows } = await k.query<Row>(
        `/* pipeshub:platform.notification_change */ UPDATE platform.notifications n SET ${set}, updated_at = $4
           WHERE ${LIVE} AND n.id = $5 AND ${guard} RETURNING ${COLS}`,
        values,
      );
      return rows[0] ? toRow(rows[0]) : null;
    });
  }
}
