/**
 * Verifier (migration design section 5). It never calls a repository mapper: the source side is the raw document brought to
 * the mapping's form (`prepareSource`), the PostgreSQL side is rebuilt from raw rows by the mapping (`readDecoded`). Checks:
 * path census, counts per organisation, id sets, document hashes (every document), invariants, isolation probe.
 */
import { createHash } from 'node:crypto';
import { getPath } from './canonical';
import { Census, judgeCensus } from './census';
import { DocDiff, compareDocs } from './compare';
import { Log, sourceDb } from './bulk';
import { MAPPINGS } from './mapping';
import type { CollectionMapping } from './mapping/types';
import { idFilter, readBatches } from './mongo-read';
import { Queryer, countByOrg, readDecoded } from './pg-read';
import type { MigrationRuntime } from './runtime';
import { JobState } from './state';
import { SourceReport, prepareSource } from './source';
import { fill } from '../../libs/repositories/pg-util';
import { counterViolations } from './verify-identity';
import { verifyCredentials, verifyCredentialsFor } from './credentials';

export interface CollectionVerify {
  collection: string;
  sourceDocs: number;
  comparedDocs: number;
  pgRows: number;
  diffs: DocDiff[];
  diffCount: number;
  countMismatches: Array<{ org: string; source: number; pg: number }>;
  /** Where the time went, in milliseconds (reading PostgreSQL, comparing, finding extra rows, other checks). */
  ms: Record<string, number>;
  /** Documents the copy left out and listed (reason per id): not differences. */
  skippedListed: Array<{ id: string; reason: string }>;
  /** Source documents per organisation (after the mapping's skips). */
  perOrg: Record<string, number>;
  unmappedPaths: Array<{ path: string; count: number }>;
  nullInOmitField: Array<{ path: string; count: number }>;
  invariantViolations: Array<{ name: string; rows: unknown[] }>;
  listed: unknown;
}

export interface VerifyReport {
  ok: boolean;
  at: string;
  collections: CollectionVerify[];
  global: Array<{ name: string; ok: boolean; detail?: unknown }>;
  isolation: { probed: number; failures: string[] } | null;
  diffs: number;
}

const MAX_DIFFS_KEPT = 50;
const IDENT = /^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$/;
const ident = (s: string): string => {
  if (!IDENT.test(s)) throw new Error(`bad identifier '${s}'`);
  return s;
};

async function withReader<T>(
  rt: MigrationRuntime,
  fn: (q: Queryer) => Promise<T>,
): Promise<T> {
  return rt.jobs((_tx, k) => fn(k as unknown as Queryer), { write: false });
}

/** Ids of the table inside `(after, upTo]` in byte order, the order a Mongo `_id` sorts in. */
async function pgIdsBetween(
  q: Queryer,
  m: CollectionMapping,
  after: string | null,
  upTo: string | null,
): Promise<string[]> {
  const { rows } = await q.query<{ i: string }>(
    fill(
      '/* pipeshub:verify.ids */ SELECT t.@{I}::text AS i FROM @{T} t WHERE ($1::text IS NULL OR t.@{I}::text COLLATE "C" > $1::text) AND ($2::text IS NULL OR t.@{I}::text COLLATE "C" <= $2::text)',
      { T: ident(m.table), I: ident(m.idCol) },
    ),
    [after, upTo],
  );
  return rows.map((r) => r.i);
}

/** Credentials (per user in the source, per address in the target) and slug counters. */
async function verifyCustom(
  rt: MigrationRuntime,
  m: CollectionMapping,
  log: Log,
  scope: VerifyScope = {},
): Promise<CollectionVerify> {
  const db = sourceDb(rt, m);
  const empty: CollectionVerify = {
    collection: m.collection,
    sourceDocs: 0,
    comparedDocs: 0,
    pgRows: 0,
    diffs: [],
    diffCount: 0,
    countMismatches: [],
    perOrg: {},
    ms: {},
    skippedListed: [],
    unmappedPaths: [],
    nullInOmitField: [],
    invariantViolations: [],
    listed: {},
  };
  if (m.collection === 'userCredentials' && scope.ids) {
    const d = await verifyCredentialsFor(rt, scope.ids);
    for (const x of d) scope.onDiff?.(m.collection, x);
    return {
      ...empty,
      sourceDocs: scope.ids.length,
      comparedDocs: scope.ids.length,
      diffs: d.slice(0, MAX_DIFFS_KEPT),
      diffCount: d.length,
    };
  }
  if (m.collection === 'userCredentials') {
    const v = await verifyCredentials(rt);
    log('verified', {
      collection: m.collection,
      docs: v.sourceDocs,
      diffs: v.diffs.length,
    });
    for (const d of v.diffs) scope.onDiff?.(m.collection, d);
    return {
      ...empty,
      sourceDocs: v.sourceDocs,
      comparedDocs: v.sourceDocs,
      pgRows: v.pgRows,
      diffs: v.diffs.slice(0, MAX_DIFFS_KEPT),
      diffCount: v.diffs.length,
      perOrg: v.perOrg,
      countMismatches: v.countMismatches,
    };
  }
  if (m.collection === 'oauthDeviceCodes') {
    // every device authorization that is still good for two more minutes must be in PostgreSQL (the rest expire on their own)
    const live = await db
      .collection('oauthDeviceCodes')
      .find({ expiresAt: { $gt: new Date(Date.now() + 120_000) } } as never, {
        projection: { _id: 1 },
      })
      .toArray();
    const ids = live.map((d) => String(d._id).toLowerCase());
    const have = new Set(
      await rt.jobs(
        async (_t, k) =>
          (
            await k.query<{ id: string }>(
              '/* pipeshub:verify.device_codes */ SELECT id FROM oauth.device_authorizations WHERE id = ANY($1::text[])',
              [ids],
            )
          ).rows.map((r) => r.id),
        { write: false },
      ),
    );
    const missing = ids.filter((i) => !have.has(i));
    const diffs: DocDiff[] = missing.map((id) => ({
      id,
      kind: 'missing_in_pg',
      detail: [],
    }));
    for (const d of diffs) scope.onDiff?.(m.collection, d);
    return {
      ...empty,
      sourceDocs: ids.length,
      comparedDocs: ids.length,
      pgRows: have.size,
      diffs: diffs.slice(0, MAX_DIFFS_KEPT),
      diffCount: diffs.length,
    };
  }
  if (m.collection === 'oauthAgentRegistrations') {
    // Every registration the sweeper has not reached must be in PostgreSQL with the same status: a sign-in revoked there and still
    // consumed here would come back live if a rollback took this as "nothing written to PostgreSQL".
    const live = await db
      .collection('oauthAgentRegistrations')
      .find({ purgeAt: { $gt: new Date(Date.now() + 120_000) } } as never, {
        projection: { _id: 1, status: 1 },
      })
      .toArray();
    const ids = live.map((d) => String(d._id).toLowerCase());
    const pgStatus = new Map(
      await rt.jobs(
        async (_t, k) =>
          (
            await k.query<{ id: string; status: string }>(
              '/* pipeshub:verify.agent_registrations */ SELECT id, status FROM oauth.agent_registrations WHERE id = ANY($1::text[])',
              [ids],
            )
          ).rows.map((r) => [r.id, r.status] as const),
        { write: false },
      ),
    );
    const diffs: DocDiff[] = [];
    for (const d of live) {
      const id = String(d._id).toLowerCase();
      const there = pgStatus.get(id);
      const here = typeof d.status === 'string' ? d.status : 'pending';
      if (there === undefined)
        diffs.push({ id, kind: 'missing_in_pg', detail: [] });
      else if (there !== here)
        diffs.push({
          id,
          kind: 'differs',
          detail: [`status: ${here} in MongoDB, ${there} in PostgreSQL`],
        });
    }
    for (const d of diffs) scope.onDiff?.(m.collection, d);
    return {
      ...empty,
      sourceDocs: ids.length,
      comparedDocs: ids.length,
      pgRows: pgStatus.size,
      diffs: diffs.slice(0, MAX_DIFFS_KEPT),
      diffCount: diffs.length,
    };
  }
  if (m.collection === 'counters') {
    const problems = await counterViolations(
      async (sql) => (await rt.migratePool.query(sql)).rows,
    );
    return {
      ...empty,
      invariantViolations: problems.length
        ? [{ name: 'slug counters', rows: problems }]
        : [],
    };
  }
  throw new Error(`no custom verification for ${m.collection}`);
}

export interface VerifyScope {
  /** Compare only this share of the documents (chosen by a hash of the id, so the same ones every run); counts, id sets and invariants still cover all. */
  sample?: number;
  /** Compare only these documents (changed since the last full verify); nothing else is read. */
  ids?: string[];
  /** Called for every difference, not only the ones kept in the report (the repair uses it). */
  onDiff?: (collection: string, d: DocDiff) => void;
}

/** A stable share of ids: the first 32 bits of the id's SHA-1 against the rate. */
export const inSample = (id: string, rate: number): boolean =>
  rate >= 1 ||
  parseInt(createHash('sha1').update(id).digest('hex').slice(0, 8), 16) /
    0x1_0000_0000 <
    rate;

/** The documents of `ids` (nothing else) against their rows. */
async function verifyIds(
  rt: MigrationRuntime,
  m: CollectionMapping,
  ids: string[],
  scope: VerifyScope,
  listedSkips: Record<string, string>,
): Promise<CollectionVerify> {
  const db = sourceDb(rt, m);
  const report = new SourceReport();
  const diffs: DocDiff[] = [];
  let diffCount = 0;
  let compared = 0;
  for (let i = 0; i < ids.length; i += rt.cfg.batchSize) {
    const part = ids.slice(i, i + rt.cfg.batchSize);
    const raw = await db
      .collection(m.collection)
      .find({ _id: { $in: part.map(idFilter) } } as never, {
        promoteLongs: false,
      })
      .toArray();
    await m.beforeTransforms?.(db, raw);
    const src = raw
      .filter((r) => listedSkips[String(r._id).toLowerCase()] === undefined)
      .map((r) => prepareSource(m, r, report, false))
      .filter((d): d is NonNullable<typeof d> => d !== null);
    // an id the source no longer holds is compared too: a row left in PostgreSQL shows up as extra_in_pg
    const decoded = await withReader(rt, (q) => readDecoded(q, m, part));
    const batch = compareDocs(src, decoded);
    diffCount += batch.length;
    for (const d of batch) {
      scope.onDiff?.(m.collection, d);
      if (diffs.length < MAX_DIFFS_KEPT) diffs.push(d);
    }
    compared += src.length;
  }
  return {
    collection: m.collection,
    sourceDocs: compared,
    comparedDocs: compared,
    pgRows: 0,
    diffs,
    diffCount,
    countMismatches: [],
    perOrg: {},
    ms: {},
    skippedListed: [],
    unmappedPaths: [],
    nullInOmitField: [],
    invariantViolations: [],
    listed: report.toJSON(),
  };
}

/** The listed skips the copy still left out: one the follower applied later (a chat started during the copy) is a normal row now and compared like any other. */
async function openSkips(
  rt: MigrationRuntime,
  m: CollectionMapping,
  skips: Record<string, string>,
): Promise<Record<string, string>> {
  const ids = Object.keys(skips);
  if (!ids.length || m.sourceKey || m.custom) return skips;
  const have = new Set(
    (await withReader(rt, (q) => readDecoded(q, m, ids))).map((d) =>
      String(d._id).toLowerCase(),
    ),
  );
  return Object.fromEntries(
    Object.entries(skips).filter(([id]) => !have.has(id)),
  );
}

export async function verifyCollection(
  rt: MigrationRuntime,
  m: CollectionMapping,
  log: Log,
  listedSkips: Record<string, string> = {},
  alreadyHashed = false,
  scope: VerifyScope = {},
): Promise<CollectionVerify> {
  if (m.custom) return verifyCustom(rt, m, log, scope);
  listedSkips = await openSkips(rt, m, listedSkips);
  if (scope.ids && !m.sourceKey)
    return verifyIds(rt, m, scope.ids, scope, listedSkips);
  const db = sourceDb(rt, m);
  const report = new SourceReport();
  const census = new Census();
  const diffs: DocDiff[] = [];
  let diffCount = 0;
  let sourceDocs = 0;
  let compared = 0;
  const srcByOrg = new Map<string, number>();
  let prev: string | null = null;
  const extras: string[] = [];
  const keyOf = (d: Record<string, unknown>): string =>
    m.sourceKey ? m.sourceKey(d) : String(d._id);
  const ms = { read: 0, compare: 0, extras: 0, other: 0 };
  const tAll = Date.now();
  const skippedListed: Array<{ id: string; reason: string }> = [];
  const excused = (id: string): boolean => {
    const why = listedSkips[id];
    if (why === undefined) return false;
    skippedListed.push({ id, reason: why });
    return true;
  };

  for await (const b of readBatches(
    db,
    m.collection,
    null,
    rt.cfg.batchSize,
    rt.cfg.batchBytes,
  )) {
    const src: Array<Record<string, unknown>> = [];
    await m.beforeTransforms?.(db, b.docs);
    for (const raw of b.docs) {
      census.add(raw);
      sourceDocs += 1;
      if (excused(String(raw._id).toLowerCase())) continue;
      const d = prepareSource(m, raw, report, alreadyHashed);
      if (!d) continue;
      if (m.sourceKey && excused(m.sourceKey(d))) continue;
      src.push(m.sourceKey ? { ...d, _id: m.sourceKey(d) } : d);
    }
    const ids = [...new Set(src.map(keyOf))];
    const picked =
      scope.sample !== undefined && scope.sample < 1
        ? src.filter((d) => inSample(keyOf(d), scope.sample as number))
        : src;
    const t1 = Date.now();
    const decoded = await withReader(rt, (q) =>
      readDecoded(q, m, [...new Set(picked.map(keyOf))]),
    );
    const t2 = Date.now();
    const batchDiffs = compareDocs(picked, decoded);
    ms.read += t2 - t1;
    ms.compare += Date.now() - t2;
    diffCount += batchDiffs.length;
    for (const d of batchDiffs) {
      scope.onDiff?.(m.collection, d);
      if (diffs.length < MAX_DIFFS_KEPT) diffs.push(d);
    }
    compared += picked.length;
    for (const d of src) {
      const org = m.orgPath ? String(getPath(d, m.orgPath) ?? '') : '';
      srcByOrg.set(org, (srcByOrg.get(org) ?? 0) + 1);
    }
    if (!m.sourceKey) {
      const t3 = Date.now();
      const inRange = await withReader(rt, (q) =>
        pgIdsBetween(q, m, prev, b.last),
      );
      ms.extras += Date.now() - t3;
      const have = new Set(ids);
      for (const id of inRange) if (!have.has(id)) extras.push(id);
      prev = b.last;
    }
  }
  if (!m.sourceKey) {
    for (const id of await withReader(rt, (q) =>
      pgIdsBetween(q, m, prev, null),
    ))
      extras.push(id);
    for (const id of extras) {
      diffCount += 1;
      scope.onDiff?.(m.collection, { id, kind: 'extra_in_pg', detail: [] });
      if (diffs.length < MAX_DIFFS_KEPT)
        diffs.push({ id, kind: 'extra_in_pg', detail: [] });
    }
  }

  const pgCounts = await withReader(rt, (q) => countByOrg(q, m));
  const countMismatches: CollectionVerify['countMismatches'] = [];
  const orgs = new Set([...srcByOrg.keys(), ...pgCounts.keys()]);
  for (const o of orgs) {
    const s = srcByOrg.get(m.orgPath ? o : '') ?? 0;
    const p = pgCounts.get(m.orgCol ? o : '') ?? 0;
    if (m.orgPath && m.orgCol && s !== p)
      countMismatches.push({ org: o, source: s, pg: p });
  }
  if (!m.orgCol) {
    const s = [...srcByOrg.values()].reduce((a, b) => a + b, 0);
    const p = [...pgCounts.values()].reduce((a, b) => a + b, 0);
    if (s !== p) countMismatches.push({ org: '', source: s, pg: p });
  }

  const findings = judgeCensus(m, census);
  const invariantViolations: CollectionVerify['invariantViolations'] = [];
  for (const c of m.children ?? []) {
    if (!c.derived.includes('org_id') || !m.orgCol) continue;
    const rows = await withReader(
      rt,
      async (q) =>
        (
          await q.query(
            fill(
              '/* pipeshub:verify.child_org */ SELECT c.@{P} AS parent_id FROM @{C} c JOIN @{T} r ON r.@{I} = c.@{P} WHERE c.org_id <> r.@{O} LIMIT 20',
              {
                C: ident(c.table),
                T: ident(m.table),
                I: ident(m.idCol),
                P: ident(c.parentCol),
                O: ident(m.orgCol as string),
              },
            ),
          )
        ).rows,
    );
    if (rows.length)
      invariantViolations.push({
        name: `${c.table} rows carry another organisation than their ${m.table} row`,
        rows,
      });
  }
  for (const inv of m.invariants ?? []) {
    // no LIMIT in SQL: with one the planner prefers a nested loop that scans quadratically when (as expected) nothing matches
    const rows = (
      await withReader(rt, async (q) => (await q.query(inv.sql)).rows)
    ).slice(0, 20);
    if (rows.length) invariantViolations.push({ name: inv.name, rows });
  }
  ms.other = Date.now() - tAll - ms.read - ms.compare - ms.extras;
  log('verified', {
    collection: m.collection,
    docs: sourceDocs,
    diffs: diffCount,
    ms,
  });
  return {
    collection: m.collection,
    sourceDocs,
    comparedDocs: compared,
    pgRows: [...pgCounts.values()].reduce((a, b) => a + b, 0),
    diffs,
    diffCount,
    countMismatches,
    perOrg: Object.fromEntries(srcByOrg),
    ms,
    skippedListed,
    unmappedPaths: findings
      .filter((f) => f.kind === 'unmapped_path')
      .map((f) => ({ path: f.path, count: f.count })),
    nullInOmitField: findings
      .filter((f) => f.kind === 'null_in_omit_field')
      .map((f) => ({ path: f.path, count: f.count })),
    invariantViolations,
    listed: report.toJSON(),
  };
}

/** Install-wide rules that belong to no single collection. */
async function globalChecks(
  rt: MigrationRuntime,
  full: boolean,
): Promise<VerifyReport['global']> {
  const out: VerifyReport['global'] = [];
  const check = async (
    name: string,
    sql: string,
    bad: (rows: Array<Record<string, unknown>>) => boolean,
  ): Promise<void> => {
    try {
      const { rows } = await rt.migratePool.query(sql);
      out.push({ name, ok: !bad(rows), detail: rows.slice(0, 5) });
    } catch (e) {
      out.push({ name, ok: false, detail: (e as Error).message });
    }
  };
  // (about 12 s: the catalog does not change with the data, so a pass over changed documents leaves it to the full verify)
  if (full)
    await check(
      'catalog rls_violations is empty',
      'SELECT count(*)::int AS n FROM kernel.rls_violations',
      (r) => Number(r[0]?.n) !== 0,
    );
  await check(
    'org slug sequence is above every generated slug',
    `SELECT COALESCE(max(substring(slug from '^org-([0-9]+)$')::bigint), 0) AS top, (SELECT last_value FROM identity.org_slug_seq) AS seq FROM identity.orgs`,
    (r) => Number(r[0]?.seq) < Number(r[0]?.top),
  );
  return out;
}

/** For up to 20 organisations, as the application login: the visible rows equal the source count, and another organisation's rows are invisible. */
async function isolationProbe(
  rt: MigrationRuntime,
  results: CollectionVerify[],
): Promise<VerifyReport['isolation']> {
  if (!rt.cfg.pgNodeUrl) return null;
  const orgs = (
    await rt.jobs(
      async (_t, k) =>
        (
          await k.query<{ o: string }>(
            '/* pipeshub:verify.probe_orgs */ SELECT org_id AS o FROM identity.orgs ORDER BY random() LIMIT 20',
          )
        ).rows,
      { write: false },
    )
  ).map((r) => r.o);
  const failures: string[] = [];
  // tables the application login may not read (the outbox is insert-only for it) cannot be probed as it
  const readable = new Set(
    (
      await rt.migratePool.query<{ t: string }>(
        "SELECT t FROM unnest($1::text[]) AS t WHERE has_table_privilege('pipeshub_node', t, 'SELECT')",
        [MAPPINGS.map((m) => m.table)],
      )
    ).rows.map((r) => r.t),
  );
  for (const org of orgs) {
    for (const m of MAPPINGS) {
      if (!m.orgCol || m.collection === 'org' || !readable.has(m.table))
        continue;
      const want = results.find((r) => r.collection === m.collection);
      if (!want) continue;
      const other = orgs.find((o) => o !== org);
      const [seen, foreign] = await rt.asOrg(
        org,
        async (_tx, k) => {
          const a = await k.query<{ n: string }>(
            fill(
              '/* pipeshub:verify.probe_own */ SELECT count(*)::text AS n FROM @{T}',
              { T: ident(m.table) },
            ),
          );
          const b = other
            ? await k.query<{ n: string }>(
                fill(
                  '/* pipeshub:verify.probe_foreign */ SELECT count(*)::text AS n FROM @{T} WHERE @{O} = $1',
                  { T: ident(m.table), O: ident(m.orgCol as string) },
                ),
                [other],
              )
            : { rows: [{ n: '0' }] };
          return [Number(a.rows[0]?.n), Number(b.rows[0]?.n)] as const;
        },
        { write: false },
      );
      if (foreign !== 0)
        failures.push(
          `${m.table}: organisation ${org} can see ${String(foreign)} rows of another organisation`,
        );
      const expect = want.perOrg[org] ?? 0;
      if (m.orgPath && seen !== expect)
        failures.push(
          `${m.table}: organisation ${org} sees ${String(seen)} rows, the source has ${String(expect)}`,
        );
    }
  }
  return { probed: orgs.length, failures };
}

export async function verifyAll(
  rt: MigrationRuntime,
  opts: {
    only?: string[];
    log: Log;
    afterReverse?: boolean;
    sample?: number;
    /** Changed ids per collection: only those documents are compared; a collection not listed is skipped. */
    ids?: Record<string, string[]>;
    onDiff?: (collection: string, d: DocDiff) => void;
  },
): Promise<VerifyReport> {
  const collections: CollectionVerify[] = [];
  const job = await JobState.latest(rt);
  for (const m of MAPPINGS) {
    if (opts.only && !opts.only.includes(m.collection)) continue;
    const changed = opts.ids?.[m.collection];
    if (opts.ids && !changed?.length) continue;
    collections.push(
      await verifyCollection(
        rt,
        m,
        opts.log,
        job?.progress.collections[m.collection]?.skippedIds ?? {},
        opts.afterReverse === true,
        {
          ...(opts.sample !== undefined ? { sample: opts.sample } : {}),
          ...(changed ? { ids: changed } : {}),
          ...(opts.onDiff ? { onDiff: opts.onDiff } : {}),
        },
      ),
    );
  }
  const global = await globalChecks(rt, !opts.ids);
  // the probe compares per-organisation counts of a full read; a changed-ids run has none
  const isolation = opts.ids ? null : await isolationProbe(rt, collections);
  const diffs = collections.reduce(
    (a, c) =>
      a +
      c.diffCount +
      c.countMismatches.length +
      c.unmappedPaths.length +
      c.nullInOmitField.length +
      c.invariantViolations.length,
    0,
  );
  const ok =
    diffs === 0 &&
    global.every((g) => g.ok) &&
    (isolation?.failures.length ?? 0) === 0;
  return {
    ok,
    at: new Date().toISOString(),
    collections,
    global,
    isolation,
    diffs,
  };
}
