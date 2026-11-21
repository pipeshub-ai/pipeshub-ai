/**
 * PostgreSQL implementation of the notification repository (`DOC_STORE=postgres`). The table is partitioned by month of
 * `created_at`, so every statement carries the org and the retention cutoff: both prune partitions and keep an expired or
 * deleted row out of every read and change.
 */
import { createHash } from 'node:crypto';
import { retentionCutoff } from '../../../modules/notification/utils/notification-api.utils';
import { registerLockKey } from '../../db';
import { stripNul } from '../strip-nul';
import {
  assertOrg,
  fill,
  inUnit,
  newObjectId,
  oidOf,
  Params,
} from '../pg-util';
import type { KernelTx } from '../pg-util';
import type {
  InsertNewResult,
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
// Nor (assigned_to, dedupe_key) alone; a lock per key does the same for a second concurrent insert of a key
const DEDUPE_LOCK = registerLockKey('notification_dedupe', 7404);

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
  dedupe_key: string | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS = `n.id, n.org_id, n.assigned_to, n.type, n.title, n.message, n.redirect_link, n.severity, n.origin_service, n.status,
  n.payload, n.is_deleted, n.deleted_by, n.visible_at, n.dedupe_key, n.version, n.created_at, n.updated_at`;

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
  if (r.dedupe_key !== null) o.dedupeKey = r.dedupe_key;
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
/** `LIVE`, without chat notices where the scope hides them. */
const liveFor = (scope: NotificationScope): string =>
  scope.hideChatNotices ? `${LIVE} AND n.type NOT LIKE 'chat.%'` : LIVE;

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
      const docs = this.prepare(k, rows);
      // a row that names its own id keeps it, and is idempotent (an existing row of the org with that id is returned, nothing is written)
      const supplied = docs.map((d) => oidOf((d as { _id?: unknown })._id));
      const ids = supplied.map((s) => s ?? newObjectId());
      const have = await this.byIds(
        k,
        ids.filter((_, i) => supplied[i] !== undefined),
      );
      // a new id named twice in one batch is inserted once; both positions get that row, as in MongoDB
      const seen = new Set(have.keys());
      const keep: number[] = [];
      for (const [i, id] of ids.entries()) {
        if (seen.has(id)) continue;
        seen.add(id);
        keep.push(i);
      }
      const stored = await this.write(
        k,
        keep.map((i) => docs[i] as NewNotification),
        keep.map((i) => ids[i] as string),
      );
      const byId = new Map([...have, ...stored]);
      return ids.map((id) => toRow(byId.get(id) as Row));
    });
  }

  async insertNew(rows: readonly NewNotification[]): Promise<InsertNewResult> {
    if (rows.length === 0) return { inserted: [], duplicates: 0, refused: [] };
    return inUnit(undefined, { write: true }, async (k) => {
      const docs = this.prepare(k, rows);
      const supplied = docs.map((d) => oidOf((d as { _id?: unknown })._id));
      const ids = supplied.map((s) => s ?? newObjectId());
      // the recipient is a 24-hex id, so the space cannot occur inside it
      const keyOf = (d: NewNotification): string | undefined =>
        typeof d.dedupeKey === 'string'
          ? `${d.assignedTo} ${d.dedupeKey}`
          : undefined;
      // the id locks before the key locks, as insertMany takes them, so two batches never wait on each other in turn
      const takenIds = new Set(
        (
          await this.byIds(
            k,
            ids.filter((_, i) => supplied[i] !== undefined),
          )
        ).keys(),
      );
      const takenKeys = await this.keysTaken(
        k,
        docs.flatMap((d) => {
          const key = keyOf(d);
          return key === undefined
            ? []
            : [{ key, assignedTo: d.assignedTo, dedupeKey: d.dedupeKey }];
        }),
      );
      const keep: number[] = [];
      for (const [i, d] of docs.entries()) {
        const id = ids[i] as string;
        const key = keyOf(d);
        if (takenIds.has(id) || (key !== undefined && takenKeys.has(key)))
          continue;
        takenIds.add(id);
        if (key !== undefined) takenKeys.add(key);
        keep.push(i);
      }
      const stored = await this.write(
        k,
        keep.map((i) => docs[i] as NewNotification),
        keep.map((i) => ids[i] as string),
      );
      return {
        inserted: keep.map((i) => toRow(stored.get(ids[i] as string) as Row)),
        duplicates: docs.length - keep.length,
        refused: [],
      };
    });
  }

  async deleteByIds(orgId: string, ids: readonly string[]): Promise<number> {
    const rows = ids.map(oidOf).filter((id) => id !== undefined);
    if (rows.length === 0) return 0;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const r = await k.query(
        `/* pipeshub:platform.notification_delete_by_ids */ DELETE FROM platform.notifications n WHERE n.org_id = $1 AND n.id = ANY($2::text[])`,
        [k.orgId, rows],
      );
      return r.rowCount ?? 0;
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
      const p = new Params();
      // $1 to $4: the org, the user, the retention cutoff and the clock that LIVE names
      p.add(k.orgId);
      p.add(user);
      p.add(retentionCutoff());
      p.add(new Date());
      const status = query.status
        ? `AND n.status = ${p.add(query.status)}`
        : `AND n.status <> 'archived'`;
      let after = '';
      if (query.cursor) {
        const at = p.add(query.cursor.createdAt);
        after = `AND (n.created_at < ${at} OR (n.created_at = ${at} AND n.id < ${p.add(cursorId)}))`;
      }
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:platform.notification_list */ SELECT @{COLS} FROM platform.notifications n WHERE @{LIVE} @{STATUS} @{AFTER}
             ORDER BY n.created_at DESC, n.id DESC LIMIT @{LIMIT}`,
          {
            COLS,
            LIVE: liveFor(scope),
            STATUS: status,
            AFTER: after,
            LIMIT: p.add(query.limit + 1),
          },
        ),
        p.values,
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
        fill(
          `/* pipeshub:platform.notification_counts */ SELECT count(*) FILTER (WHERE n.status = 'unread') AS unread,
             count(*) FILTER (WHERE n.status = 'read') AS read, count(*) FILTER (WHERE n.status = 'archived') AS archived
             FROM platform.notifications n WHERE @{LIVE}`,
          { LIVE: liveFor(scope) },
        ),
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
        fill(
          `/* pipeshub:platform.notification_mark_all_read */ UPDATE platform.notifications n SET status = 'read', updated_at = $4
             WHERE @{LIVE} AND n.status = 'unread'`,
          { LIVE: liveFor(scope) },
        ),
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
        fill(
          `/* pipeshub:platform.notification_change */ UPDATE platform.notifications n SET @{SET}, updated_at = $4
             WHERE @{LIVE} AND n.id = $5 AND @{GUARD} RETURNING @{COLS}`,
          { SET: set, LIVE: liveFor(scope), GUARD: guard, COLS },
        ),
        values,
      );
      return rows[0] ? toRow(rows[0]) : null;
    });
  }

  /** The rows as stored: dated, NUL-free, each in the unit's org. */
  private prepare(
    k: KernelTx,
    rows: readonly NewNotification[],
  ): NewNotification[] {
    const docs = rows.map((r) =>
      stripNul(prepareNotification(r), 'notification', 'row'),
    );
    for (const d of docs) assertOrg(k, d.orgId);
    return docs;
  }

  /** Takes the transaction locks of `names` in one lock class, ascending, so two batches that share names take them in the same order. */
  private async lock(
    k: KernelTx,
    key: typeof ID_LOCK,
    names: readonly string[],
  ): Promise<void> {
    const objects = [
      ...new Set(
        names.map((name) =>
          createHash('md5')
            .update(`${k.orgId}/${name}`)
            .digest()
            .readInt32BE(0),
        ),
      ),
    ].sort((a, b) => a - b);
    for (const object of objects) await k.advisoryXactLock(key, object);
  }

  /** The org's rows with these ids, by id, once their locks are held. */
  private async byIds(
    k: KernelTx,
    ids: readonly string[],
  ): Promise<Map<string, Row>> {
    if (ids.length === 0) return new Map();
    await this.lock(k, ID_LOCK, ids);
    const { rows } = await k.query<Row>(
      fill(
        `/* pipeshub:platform.notification_by_ids */ SELECT @{COLS} FROM platform.notifications n WHERE n.org_id = $1 AND n.id = ANY($2::text[])`,
        { COLS },
      ),
      [k.orgId, ids],
    );
    return new Map(rows.map((r) => [r.id, r]));
  }

  /** Of these `(assignedTo, dedupeKey)` keys, the ones a row inside the retention window holds, once their locks are held. */
  private async keysTaken(
    k: KernelTx,
    keyed: readonly { key: string; assignedTo: string; dedupeKey: unknown }[],
  ): Promise<Set<string>> {
    if (keyed.length === 0) return new Set();
    await this.lock(
      k,
      DEDUPE_LOCK,
      keyed.map((d) => d.key),
    );
    const { rows } = await k.query<{ assigned_to: string; dedupe_key: string }>(
      `/* pipeshub:platform.notification_dedupe */ SELECT n.assigned_to, n.dedupe_key FROM platform.notifications n
         JOIN unnest($3::text[], $4::text[]) AS u(assigned_to, dedupe_key) ON n.assigned_to = u.assigned_to AND n.dedupe_key = u.dedupe_key
         WHERE n.org_id = $1 AND n.created_at >= $2`,
      [
        k.orgId,
        retentionCutoff(),
        keyed.map((d) => d.assignedTo),
        keyed.map((d) => d.dedupeKey as string),
      ],
    );
    return new Set(rows.map((r) => `${r.assigned_to} ${r.dedupe_key}`));
  }

  /** Stores `docs` under `ids` in one statement; the stored rows, by id. */
  private async write(
    k: KernelTx,
    docs: readonly NewNotification[],
    ids: readonly string[],
  ): Promise<Map<string, Row>> {
    if (docs.length === 0) return new Map();
    const text = (f: (d: NewNotification) => unknown): (string | null)[] =>
      docs.map((d) => {
        const v = f(d);
        if (v === undefined || v === null) return null;
        return typeof v === 'object'
          ? JSON.stringify(v)
          : (v as string | number | boolean).toString();
      });
    // updated_at: a scheduled row's visible_at, else its created_at, as MongoDB's timestamps stamp a new document
    const { rows: out } = await k.query<Row>(
      fill(
        `/* pipeshub:platform.notification_insert */ INSERT INTO platform.notifications AS n
           (org_id, id, created_at, type, title, message, redirect_link, severity, status, origin_service, assigned_to, payload, is_deleted, deleted_by, visible_at, dedupe_key, updated_at)
         SELECT $1::text, u.id, COALESCE(u.created_at, $2::timestamptz), u.type, u.title, u.message, u.redirect_link, u.severity, COALESCE(u.status, 'unread'), u.origin_service, u.assigned_to,
                u.payload::jsonb, COALESCE(u.is_deleted, false), u.deleted_by, u.visible_at, u.dedupe_key, COALESCE(u.visible_at, u.created_at, $2::timestamptz)
           FROM unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::text[], $8::text[], $9::text[], $10::text[], $11::text[], $12::text[], $13::text[], $14::boolean[], $15::timestamptz[], $16::timestamptz[], $17::text[])
             AS u(id, type, title, message, redirect_link, severity, status, origin_service, assigned_to, payload, deleted_by, is_deleted, created_at, visible_at, dedupe_key)
         RETURNING @{COLS}`,
        { COLS },
      ),
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
        docs.map((d) => (d.createdAt as Date | undefined) ?? null),
        docs.map((d) => (d.visibleAt as Date | undefined) ?? null),
        text((d) => d.dedupeKey),
      ],
    );
    return new Map(out.map((r) => [r.id, r]));
  }
}
