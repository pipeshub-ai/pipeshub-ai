/**
 * Credentials at any size. A credential belongs to an account (organisation, user) in both stores, but MongoDB may hold several
 * documents for one account: the newest `updatedAt` wins, the later document on a tie, and only an account that is live has a row.
 *
 *  - The copy reads the credentials in `_id` order like every other collection, but first finds, in one light pass (id, user, time),
 *    which document wins for each account; the others are not offered to the importer. A batch then holds at most one document per
 *    account, so "newest wins" holds across batches too, and no batch has to be the whole collection.
 *  - The verify walks the credentials once keeping only the winner of each account, then compares PostgreSQL's rows page by page
 *    (by user id), so neither side is held whole.
 */
import { plainJson, Doc } from './canonical';
import { compareDocs, DocDiff } from './compare';
import { decodeDoc } from './decode';
import { idText } from '../../libs/repositories/import';
import { importCredentialDocs } from '../../libs/repositories/identity/credentials-import';
import { userCredentials } from './mapping/identity';
import { idFilter } from './mongo-read';
import { countByOrg } from './pg-read';
import type { MigrationRuntime } from './runtime';
import { SourceReport, toCanonical, toNeutral } from './source';

const time = (v: unknown): number =>
  typeof v === 'string'
    ? Date.parse(v) || 0
    : v instanceof Date
      ? v.getTime()
      : 0;

/** `orgId/userId` of every live user (of these users when `ids` is given). */
async function liveUsers(
  rt: MigrationRuntime,
  ids?: string[],
): Promise<Set<string>> {
  const out = new Set<string>();
  const cursor = rt
    .db()
    .collection('users')
    .find(ids ? ({ _id: { $in: ids.map(idFilter) } } as never) : {}, {
      projection: { orgId: 1, isDeleted: 1 },
      promoteLongs: false,
    });
  for await (const u of cursor)
    if (u.isDeleted !== true)
      out.add(`${String(u.orgId)}/${String(u._id)}`.toLowerCase());
  return out;
}

/** A user id as the importer stores it (lower-case hex), so source ids and PostgreSQL rows meet under one key. */
const stored = (v: string): string => idText(v) ?? v;

/**
 * The forms a user id may have in MongoDB, for a `$in` (a text id is matched exactly there).
 * shortcut: all-lower and all-upper hex only; a mixed-case id is not found, none is known to exist. Upgrade with a collation query if one does.
 */
const sourceForms = (ids: Iterable<string>): string[] => [
  ...new Set([...ids].flatMap((u) => [u, u.toLowerCase(), u.toUpperCase()])),
];

const accountOf = (c: Record<string, unknown>): string =>
  `${String(c.orgId)}/${String(c.userId)}`.toLowerCase();

/** The ids of the credentials that become rows: the newest of each live account. */
export async function winningCredentials(
  rt: MigrationRuntime,
): Promise<Set<string>> {
  const live = await liveUsers(rt);
  const best = new Map<string, { id: string; at: number }>();
  const cursor = rt
    .db()
    .collection('userCredentials')
    .find(
      {},
      {
        projection: { orgId: 1, userId: 1, updatedAt: 1, isDeleted: 1 },
        sort: { _id: 1 },
        promoteLongs: false,
      },
    );
  for await (const c of cursor) {
    if (c.isDeleted === true || typeof c.userId !== 'string') continue;
    const a = accountOf(c);
    if (!live.has(a)) continue;
    const at = time(c.updatedAt);
    const prev = best.get(a);
    if (!prev || at >= prev.at)
      best.set(a, { id: String(c._id).toLowerCase(), at });
  }
  return new Set([...best.values()].map((b) => b.id));
}

/** The documents the credentials of these users (all users when none are given) should have become, by user id. */
async function expectedRows(
  rt: MigrationRuntime,
  userIds?: string[],
): Promise<Map<string, Doc>> {
  const rep = new SourceReport();
  const live = await liveUsers(rt, userIds);
  const best = new Map<string, Doc>();
  const cursor = rt
    .db()
    .collection('userCredentials')
    .find(userIds ? { userId: { $in: sourceForms(userIds) } } : {}, {
      sort: { _id: 1 },
      promoteLongs: false,
    });
  for await (const raw of cursor) {
    const c = toCanonical(raw, rep, 'userCredentials');
    if (c.isDeleted === true || typeof c.userId !== 'string') continue;
    if (!live.has(accountOf(c))) continue;
    const key = stored(c.userId);
    const prev = best.get(key);
    if (!prev || time(c.updatedAt) >= time(prev.updatedAt)) best.set(key, c);
  }
  const out = new Map<string, Doc>();
  for (const [u, c] of best) {
    const doc: Doc = { _id: u };
    for (const f of userCredentials.fields) {
      let v = c[f.path];
      if (v === undefined && f.readAbsentAs !== undefined) v = f.readAbsentAs;
      if (typeof v === 'string' && f.type === 'oid') v = stored(v);
      if (v !== undefined) doc[f.path] = v;
    }
    out.set(u, plainJson(doc) as Doc);
  }
  return out;
}

const PAGE = 5000;

const decodeRows = (rows: Array<{ r: Record<string, unknown> }>): Doc[] =>
  rows.map((x) => decodeDoc(userCredentials, x.r, {}));

/** The rows of these credentials' accounts against MongoDB (the freeze's check: only what changed). */
export async function verifyCredentialsFor(
  rt: MigrationRuntime,
  credentialIds: string[],
): Promise<DocDiff[]> {
  const creds = await rt
    .db()
    .collection('userCredentials')
    .find({ _id: { $in: credentialIds.map(idFilter) } } as never, {
      projection: { userId: 1 },
      promoteLongs: false,
    })
    .toArray();
  const users = [
    ...new Set(
      creds
        .map((c) => c.userId)
        .filter((u): u is string => typeof u === 'string')
        .map(stored),
    ),
  ];
  if (users.length === 0) return [];
  const want = await expectedRows(rt, users);
  const rows = await rt.jobs(
    async (_t, k) =>
      (
        await k.query<{ r: Record<string, unknown> }>(
          '/* pipeshub:verify.credentials_of */ SELECT to_jsonb(c) AS r FROM identity.user_credentials c WHERE c.user_id = ANY($1::text[])',
          [users],
        )
      ).rows,
    { write: false },
  );
  return compareDocs([...want.values()], decodeRows(rows));
}

/** Compares the rows with the credentials, page by page. Returns the differences and the counts. */
export async function verifyCredentials(rt: MigrationRuntime): Promise<{
  diffs: DocDiff[];
  sourceDocs: number;
  pgRows: number;
  perOrg: Record<string, number>;
  countMismatches: Array<{ org: string; source: number; pg: number }>;
}> {
  const expected = await expectedRows(rt);
  const perOrg: Record<string, number> = {};
  for (const d of expected.values())
    perOrg[String(d.orgId)] = (perOrg[String(d.orgId)] ?? 0) + 1;
  const sourceDocs = await rt
    .db()
    .collection('userCredentials')
    .estimatedDocumentCount();
  const diffs: DocDiff[] = [];
  const seen = new Set<string>();
  let after: string | null = null;
  let pgRows = 0;
  for (;;) {
    const rows: Array<{ r: Record<string, unknown> }> = await rt.jobs(
      async (_t, k) =>
        (
          await k.query<{ r: Record<string, unknown> }>(
            '/* pipeshub:verify.credentials_page */ SELECT to_jsonb(c) AS r FROM identity.user_credentials c WHERE ($1::text IS NULL OR c.user_id > $1) ORDER BY c.user_id LIMIT $2',
            [after, PAGE],
          )
        ).rows,
      { write: false },
    );
    if (rows.length === 0) break;
    const all = decodeRows(rows);
    pgRows += all.length;
    const want: Doc[] = [];
    for (const d of all) {
      const u = String(d._id);
      seen.add(u);
      const w = expected.get(u);
      if (w) want.push(w);
    }
    // a row no credential explains comes back as extra_in_pg
    diffs.push(...compareDocs(want, all));
    after = String(all[all.length - 1]?._id);
  }
  for (const [u] of expected)
    if (!seen.has(u)) diffs.push({ id: u, kind: 'missing_in_pg', detail: [] });
  const pgCounts = await rt.jobs((_t, k) => countByOrg(k, userCredentials), {
    write: false,
  });
  const countMismatches: Array<{ org: string; source: number; pg: number }> =
    [];
  for (const o of new Set([...Object.keys(perOrg), ...pgCounts.keys()]))
    if ((perOrg[o] ?? 0) !== (pgCounts.get(o) ?? 0))
      countMismatches.push({
        org: o,
        source: perOrg[o] ?? 0,
        pg: pgCounts.get(o) ?? 0,
      });
  return { diffs, sourceDocs, pgRows, perOrg, countMismatches };
}

/**
 * ---- keeping credentials level while the application writes (the follower) --------------------------------------------------------
 * An account's row is made of the newest credential it has. A credential changes (a password, a one-time code, an attempt count) or an
 * account is replaced, restored or removed: the rows of the users involved are written again from MongoDB.
 */
export async function syncCredentials(
  rt: MigrationRuntime,
  userIds: Iterable<string>,
): Promise<number> {
  const list = [...new Set([...userIds].map((u) => u.toLowerCase()))].filter(
    Boolean,
  );
  if (list.length === 0) return 0;
  const raws = await rt
    .db()
    .collection('userCredentials')
    .find({ userId: { $in: sourceForms(list) }, isDeleted: { $ne: true } } as never, {
      sort: { _id: 1 },
      promoteLongs: false,
    })
    .toArray();
  const rep = new SourceReport();
  const docs = raws.map((d) => toNeutral(d, rep, 'userCredentials'));
  // one unit: the rows of these users are replaced by what MongoDB says (none for an account that is gone)
  await rt.jobs(async (_t, k) => {
    await k.query(
      '/* pipeshub:follow.credentials_clear */ DELETE FROM identity.user_credentials WHERE user_id = ANY($1::text[])',
      [list],
    );
    await importCredentialDocs(k, 'userCredentials', docs);
  });
  return list.length;
}

/** Every user that has a credential in either store: used after the saved change-stream token was lost, and after a credential was deleted. */
export async function syncAllCredentials(
  rt: MigrationRuntime,
): Promise<number> {
  const all = new Set<string>();
  const cursor = rt
    .db()
    .collection('userCredentials')
    .find({}, { projection: { userId: 1 }, promoteLongs: false });
  for await (const c of cursor)
    if (typeof c.userId === 'string') all.add(c.userId.toLowerCase());
  let after: string | null = null;
  for (;;) {
    const rows: Array<{ user_id: string }> = await rt.jobs(
      async (_t, k) =>
        (
          await k.query<{ user_id: string }>(
            '/* pipeshub:follow.credential_users_page */ SELECT user_id FROM identity.user_credentials WHERE ($1::text IS NULL OR user_id > $1) ORDER BY user_id LIMIT $2',
            [after, PAGE],
          )
        ).rows,
      { write: false },
    );
    if (rows.length === 0) break;
    for (const r of rows) all.add(r.user_id);
    after = rows[rows.length - 1]?.user_id ?? null;
  }
  let n = 0;
  const batch = [...all];
  for (let i = 0; i < batch.length; i += 2000)
    n += await syncCredentials(rt, batch.slice(i, i + 2000));
  return n;
}
