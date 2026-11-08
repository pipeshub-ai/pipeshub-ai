/**
 * Reverse copy of the collections whose rows are not one document each, or whose key is not the document id: credentials (per
 * account in PostgreSQL, a document of their own in MongoDB), reset-link claims, Slack links and the outbox. See reverse.ts.
 */
import { ObjectId } from 'mongodb';
import { decodeDoc } from './decode';
import { MAPPINGS } from './mapping';
import type { CollectionMapping } from './mapping/types';
import { readDecodedPage } from './pg-read';
import type { MigrationRuntime } from './runtime';
import { sourceDb } from './bulk';

type Row = Record<string, unknown>;
type Counts = { written: number; removed: number };

export const SPECIAL = new Set([
  'counters',
  'userCredentials',
  'usedPasswordResetLinks',
  'slack_conversations',
  'outbox_events',
]);

const mapping = (name: string): CollectionMapping => {
  const m = MAPPINGS.find((x) => x.collection === name);
  if (!m) throw new Error(`no mapping for ${name}`);
  return m;
};

async function credentials(rt: MigrationRuntime): Promise<Counts> {
  const col = rt.db().collection('userCredentials');
  let written = 0;
  const keep: string[] = [];
  const m = mapping('userCredentials');
  let after: string | null = null;
  for (;;) {
    const rows: Row[] = await rt.jobs(
      async (_t, k) =>
        (
          await k.query<{ r: Row }>(
            '/* pipeshub:reverse.credentials */ SELECT to_jsonb(c) AS r FROM identity.user_credentials c WHERE ($1::text IS NULL OR c.user_id > $1) ORDER BY c.user_id LIMIT $2',
            [after, rt.cfg.batchSize],
          )
        ).rows.map((x) => x.r),
      { write: false },
    );
    if (rows.length === 0) break;
    for (const row of rows) {
      const d = decodeDoc(m, row, {});
      const { _id, ...rest } = d;
      for (const k of ['createdAt', 'updatedAt', 'blockExpiresAt'] as const)
        if (typeof rest[k] === 'string') rest[k] = new Date(rest[k]);
      keep.push(String(_id));
      const filter = { userId: String(_id), orgId: rest.orgId };
      const kept = await col.findOneAndReplace(
        filter,
        { ...rest, userId: String(_id), isDeleted: false },
        { upsert: true, returnDocument: 'after', projection: { _id: 1 } },
      );
      // MongoDB may hold several live documents for one account (the copy took the newest): only the one written stays
      if (kept)
        await col.deleteMany({
          ...filter,
          isDeleted: { $ne: true },
          _id: { $ne: kept._id },
        });
      written += 1;
    }
    after = String(rows[rows.length - 1]?.user_id);
  }
  const gone = await col.deleteMany({
    isDeleted: { $ne: true },
    userId: { $nin: keep },
  });
  return { written, removed: gone.deletedCount };
}

async function pagedRows(
  rt: MigrationRuntime,
  name: string,
  each: (d: Record<string, unknown>) => Promise<void>,
): Promise<void> {
  const m = mapping(name);
  let after: string | null = null;
  for (;;) {
    const docs = await rt.jobs(
      (_tx, k) => readDecodedPage(k, m, after, rt.cfg.batchSize),
      { write: false },
    );
    if (docs.length === 0) return;
    for (const d of docs) await each(d);
    after = String(docs[docs.length - 1]?._id);
  }
}

const toDates = (
  d: Record<string, unknown>,
  keys: string[],
): Record<string, unknown> => {
  for (const k of keys) if (typeof d[k] === 'string') d[k] = new Date(d[k]);
  return d;
};

async function resetLinks(rt: MigrationRuntime): Promise<Counts> {
  const col = rt.db().collection('usedPasswordResetLinks');
  let written = 0;
  await pagedRows(rt, 'usedPasswordResetLinks', async (d) => {
    const { _id, ...rest } = d;
    toDates(rest, ['expiresAt', 'createdAt', 'updatedAt']);
    await col.replaceOne(
      { linkHash: String(_id) },
      { ...rest, linkHash: String(_id) },
      { upsert: true },
    );
    written += 1;
  });
  return { written, removed: 0 };
}

async function slack(rt: MigrationRuntime): Promise<Counts> {
  const col = sourceDb(rt, mapping('slack_conversations')).collection(
    'slack_conversations',
  );
  let written = 0;
  await pagedRows(rt, 'slack_conversations', async (d) => {
    const { _id: _unused, ...rest } = d;
    toDates(rest, ['createdAt', 'updatedAt']);
    await col.replaceOne(
      {
        threadId: rest.threadId,
        botId: rest.botId,
        email: rest.email,
      },
      rest,
      { upsert: true },
    );
    written += 1;
  });
  return { written, removed: 0 };
}

/** The table id the copy derived from an event's ObjectId (same arithmetic as the importer, restated for the way back). */
const derivedId = (hex: string): string => {
  const secs = Math.max(0, parseInt(hex.slice(0, 8), 16) - 1577836800);
  return String(secs * 2097152 + (parseInt(hex.slice(18, 24), 16) % 2097152));
};

async function outbox(rt: MigrationRuntime): Promise<Counts> {
  const m = mapping('outbox_events');
  const col = rt.db().collection('outbox_events');
  const existing = new Map<string, ObjectId>();
  for await (const d of col.find({}, { projection: { _id: 1 } }))
    existing.set(derivedId(String(d._id)), d._id);
  const rows = (
    await rt.jobs(
      async (_t, k) =>
        (
          await k.query<{ r: Row }>(
            '/* pipeshub:reverse.outbox */ SELECT to_jsonb(o) AS r FROM platform.outbox o',
          )
        ).rows,
      { write: false },
    )
  ).map((x) => x.r);
  const seen = new Set<string>();
  let written = 0;
  for (const row of rows) {
    const d = decodeDoc(m, row, {});
    const id = String(d._id);
    seen.add(id);
    delete d._id;
    delete d.__org;
    toDates(d, ['nextAttemptAt', 'createdAt', 'updatedAt']);
    const mongoId = existing.get(id);
    if (mongoId) await col.updateOne({ _id: mongoId }, { $set: d });
    else await col.insertOne({ _id: new ObjectId(), ...d });
    written += 1;
  }
  // events that are no longer in PostgreSQL were delivered there (published rows are removed after a week): settle them here
  const settle = [...existing].filter(([k]) => !seen.has(k)).map(([, v]) => v);
  const r = settle.length
    ? await col.updateMany(
        { _id: { $in: settle }, status: { $ne: 'published' } },
        { $set: { status: 'published', publishedAt: new Date() } },
      )
    : { modifiedCount: 0 };
  return { written, removed: r.modifiedCount };
}

/** MongoDB's counters must stay above every number PostgreSQL has handed out (slug numbers are per organisation there, global here). */
async function counters(rt: MigrationRuntime): Promise<Counts> {
  const col = rt.db().collection('counters');
  const seq = (
    await rt.migratePool.query<{ n: string }>(
      'SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END::text AS n FROM identity.org_slug_seq',
    )
  ).rows[0]?.n;
  const kinds = await rt.jobs(
    async (_t, k) =>
      (
        await k.query<{ kind: string; top: string }>(
          '/* pipeshub:reverse.counters */ SELECT kind, max(next_value)::text AS top FROM identity.slug_counters GROUP BY kind',
        )
      ).rows,
    { write: false },
  );
  let written = 0;
  const raise = async (name: string, value: number): Promise<void> => {
    await col.updateOne(
      { name },
      { $max: { seq: value }, $setOnInsert: { name } },
      { upsert: true },
    );
    written += 1;
  };
  if (seq !== undefined) await raise('Org', Number(seq));
  for (const k of kinds) await raise(k.kind, Number(k.top));
  return { written, removed: 0 };
}

export async function reverseSpecial(
  rt: MigrationRuntime,
  name: string,
): Promise<Counts> {
  switch (name) {
    case 'counters':
      return counters(rt);
    case 'userCredentials':
      return credentials(rt);
    case 'usedPasswordResetLinks':
      return resetLinks(rt);
    case 'slack_conversations':
      return slack(rt);
    case 'outbox_events':
      return outbox(rt);
    default:
      throw new Error(`no special reverse for ${name}`);
  }
}
