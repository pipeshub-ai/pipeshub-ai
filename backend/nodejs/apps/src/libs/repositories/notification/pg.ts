/**
 * PostgreSQL implementation of the notification repository (`DOC_STORE=postgres`). The table is partitioned by month of
 * `created_at`, so every statement carries the org and the retention cutoff: both prune partitions and keep an expired or
 * deleted row out of every read and change.
 */
import { retentionCutoff } from '../../../modules/notification/utils/notification-api.utils';
import { stripNul } from '../nul';
import { assertOrg, inUnit, newObjectId, oidOf } from '../pg-util';
import type {
  NewNotification,
  NotificationCursor,
  NotificationRepository,
  NotificationRow,
  NotificationScope,
  NotificationStatus,
} from './types';

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
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS = `n.id, n.org_id, n.assigned_to, n.type, n.title, n.message, n.redirect_link, n.severity, n.origin_service, n.status,
  n.payload, n.is_deleted, n.deleted_by, n.version, n.created_at, n.updated_at`;

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
  return o as unknown as NotificationRow;
}

/** The conditions every read and change shares; `$1` org, `$2` user, `$3` retention cutoff. */
const LIVE = `n.org_id = $1 AND n.assigned_to = $2 AND NOT n.is_deleted AND n.created_at >= $3`;

export class PgNotificationRepository implements NotificationRepository {
  async insert(row: NewNotification): Promise<NotificationRow> {
    return (await this.insertMany([row]))[0] as NotificationRow;
  }

  async insertMany(rows: NewNotification[]): Promise<NotificationRow[]> {
    if (rows.length === 0) return [];
    return inUnit(undefined, { write: true }, async (k) => {
      const docs = rows.map((r) => stripNul(r, 'notification', 'row'));
      for (const d of docs) assertOrg(k, d.orgId);
      const ids = docs.map(() => newObjectId());
      const text = (f: (d: NewNotification) => unknown): (string | null)[] =>
        docs.map((d) => {
          const v = f(d);
          if (v === undefined || v === null) return null;
          return typeof v === 'object'
            ? JSON.stringify(v)
            : (v as string | number | boolean).toString();
        });
      const { rows: out } = await k.query<Row>(
        `/* pipeshub:platform.notification_insert */ INSERT INTO platform.notifications AS n
           (org_id, id, created_at, type, title, message, redirect_link, severity, status, origin_service, assigned_to, payload, is_deleted, deleted_by, updated_at)
         SELECT $1::text, u.id, $2::timestamptz, u.type, u.title, u.message, u.redirect_link, u.severity, COALESCE(u.status, 'unread'), u.origin_service, u.assigned_to,
                u.payload::jsonb, COALESCE(u.is_deleted, false), u.deleted_by, $2::timestamptz
           FROM unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::text[], $8::text[], $9::text[], $10::text[], $11::text[], $12::text[], $13::text[], $14::boolean[])
             AS u(id, type, title, message, redirect_link, severity, status, origin_service, assigned_to, payload, deleted_by, is_deleted)
         RETURNING ${COLS}`,
        [
          k.orgId,
          new Date(),
          ids,
          text((d) => d.type),
          text((d) => d.title),
          text((d) => d.message),
          text((d) => d.redirectLink),
          text((d) => d.severity),
          text((d) => d.status),
          text((d) => d.originService),
          text((d) => d.assignedTo),
          docs.map((d) =>
            d.payload === undefined ? null : JSON.stringify(d.payload),
          ),
          text((d) => d.deletedBy),
          docs.map((d) =>
            d.isDeleted === undefined ? null : Boolean(d.isDeleted),
          ),
        ],
      );
      const byId = new Map(out.map((r) => [r.id, r]));
      return ids.map((id) => toRow(byId.get(id) as Row));
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
      const values: unknown[] = [k.orgId, user, retentionCutoff()];
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
        [k.orgId, user, retentionCutoff()],
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
      `status = $5`,
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
      const values: unknown[] = [
        k.orgId,
        user,
        retentionCutoff(),
        row,
        ...(arg === undefined ? [] : [arg]),
      ];
      const { rows } = await k.query<Row>(
        `/* pipeshub:platform.notification_change */ UPDATE platform.notifications n SET ${set}, updated_at = date_trunc('milliseconds', now())
           WHERE ${LIVE} AND n.id = $4 AND ${guard} RETURNING ${COLS}`,
        values,
      );
      return rows[0] ? toRow(rows[0]) : null;
    });
  }
}
