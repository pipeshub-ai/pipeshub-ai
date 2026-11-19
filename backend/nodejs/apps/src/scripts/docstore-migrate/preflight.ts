/** Preflight (migration design section 2): read-only, one report, any red item blocks the copy. */
import { statfs } from 'node:fs/promises';
import type { Db, Document } from 'mongodb';
import { Census, CensusFinding, judgeCensus } from './census';
import { MAPPINGS, IGNORED } from './mapping';
import { collectionCounts, idString } from './mongo-read';
import { MONGO_COLLECTIONS } from './mongo-collections';
import type { MigrationRuntime } from './runtime';
import { sourceDb } from './bulk';
import { JobState } from './state';
import { appClients, describeClients } from './fence';
import { outboxTableId } from './mapping/content';
import { schemaRequirements } from '../../libs/repositories/doc-store';
import { schemaProblems } from '../../libs/db/schema-assert';

export interface Finding {
  check: string;
  message: string;
  data?: unknown;
}

export interface PreflightReport {
  ok: boolean;
  at: string;
  red: Finding[];
  yellow: Finding[];
  info: Record<string, unknown>;
}

/** Collections larger than this are sampled for the census. */
const FULL_CENSUS_UP_TO = 200_000;
const SAMPLE = 50_000;
/** The application's requirements, plus the revisions only the migrator uses: the import role's access to the tables and the change log. */
function migratorRequirements(): ReturnType<typeof schemaRequirements> {
  const req = schemaRequirements();
  for (const [branch, epoch] of Object.entries({
    platform_data: 3,
  }))
    req[branch] = {
      requires: Math.max(req[branch]?.requires ?? 0, epoch),
      code: Math.max(req[branch]?.code ?? 0, epoch),
    };
  return req;
}

/** Disk the target needs beyond the source data: indexes (message text index about 36 %), WAL and slack. */
const DISK_FACTOR = 1.4;

/** Collections that hold documents the copy does not move; non-empty means the operator must act first. */
const MUST_BE_EMPTY: Record<string, string> = {
  conversations:
    'legacy conversations still hold embedded messages: start the application on MongoDB once so that its boot migration moves them to chat sessions, then run preflight again',
  agentconversations:
    'legacy agent conversations still hold embedded messages: start the application on MongoDB once so that its boot migration moves them to chat sessions, then run preflight again',
};

/** The import login is shut except during a move, and its password is not in `.env` then: an empty password means "shut". */
/**
 * Up to three `_id` values whose BSON type is not ObjectId, with that type. The type decides, not the text: a 24-hex *string* id
 * looks like an ObjectId but is a different key in MongoDB, and the copy would store it as if it were one.
 */
export async function nonObjectIdSample(
  db: Db,
  collection: string,
): Promise<Array<{ type: string; value: string }>> {
  const docs = await db
    .collection(collection)
    .find({ _id: { $not: { $type: 'objectId' } } })
    .limit(3)
    .toArray();
  return docs.map((d) => ({
    type:
      typeof d._id === 'string'
        ? 'string'
        : ((d._id as { _bsontype?: string } | null)?._bsontype ?? typeof d._id),
    value: idString(d._id).slice(0, 40),
  }));
}

export const importLoginShut = (url: string): boolean => {
  try {
    return new URL(url).password === '';
  } catch {
    return false;
  }
};

export async function preflight(
  rt: MigrationRuntime,
  opts: {
    log?: (m: string) => void;
    allowClients?: boolean;
    /** The online path: the application keeps running, so MongoDB must be a replica set (change streams) with an oplog that outlasts the copy. */
    online?: boolean;
  } = {},
): Promise<PreflightReport> {
  const red: Finding[] = [];
  const yellow: Finding[] = [];
  const info: Record<string, unknown> = {};
  const say = opts.log ?? (() => undefined);
  const apiDb = rt.db();

  // topology
  try {
    const hello = await apiDb.admin().command({ hello: 1 });
    info.mongoTopology = hello.setName
      ? { replicaSet: hello.setName, members: (hello.hosts ?? []).length }
      : 'standalone';
    if (hello.setName) {
      try {
        const local = rt.mongo.db('local').collection('oplog.rs');
        const first = await local
          .find({}, { projection: { ts: 1 } })
          .sort({ $natural: 1 })
          .limit(1)
          .toArray();
        const last = await local
          .find({}, { projection: { ts: 1 } })
          .sort({ $natural: -1 })
          .limit(1)
          .toArray();
        const f = (
          first[0]?.ts as { getHighBits(): number } | undefined
        )?.getHighBits();
        const l = (
          last[0]?.ts as { getHighBits(): number } | undefined
        )?.getHighBits();
        if (f !== undefined && l !== undefined) {
          info.oplogWindowHours = Math.round(((l - f) / 3600) * 10) / 10;
          if (opts.online && l - f < 24 * 3600)
            yellow.push({
              check: 'oplog',
              message: `the oplog window is ${String(info.oplogWindowHours)} h: the copy, the verify and 24 h of following must fit in it, or a lost resume token costs a full compare. Enlarge it (replSetResizeOplog) before a large copy`,
            });
        }
      } catch (e) {
        yellow.push({
          check: 'oplog',
          message: `oplog window could not be read: ${(e as Error).message}`,
        });
      }
    } else {
      info.followMode = 'none (standalone): only the offline cut applies';
      if (opts.online)
        red.push({
          check: 'change_streams',
          message:
            'MongoDB is a standalone server: the online path needs a replica set (change streams). Use the offline cut (scripts/migrate-to-postgres.sh)',
        });
    }
  } catch (e) {
    red.push({
      check: 'mongo',
      message: `MongoDB is not reachable: ${(e as Error).message}`,
    });
    return { ok: false, at: new Date().toISOString(), red, yellow, info };
  }

  // a running application would go on writing to the store this move retires
  try {
    const clients = await appClients(rt);
    if (clients.length > 0)
      (opts.allowClients ? yellow : red).push({
        check: 'app_running',
        message: `the application is still connected to MongoDB: ${describeClients(clients)}. Stop it before the copy (scripts/migrate-to-postgres.sh does); a preflight run only reads, but the cut itself refuses while it is connected`,
        data: clients.length,
      });
  } catch (e) {
    yellow.push({
      check: 'app_running',
      message: `connected clients could not be listed (${e instanceof Error ? e.message : String(e)}): make sure the application is stopped`,
    });
  }

  // collections
  const counts = await collectionCounts(apiDb);
  if (rt.cfg.slackDb)
    for (const [n, c] of await collectionCounts(rt.db(rt.cfg.slackDb)))
      if (n === 'slack_conversations') counts.set(n, c);
  const mapped = new Set([
    ...MAPPINGS.map((m) => m.collection),
    ...IGNORED.map((i) => i.collection),
  ]);
  info.collections = Object.fromEntries(counts);
  for (const [name, n] of counts) {
    if (mapped.has(name)) continue;
    if (name === 'docstore_fence' || name === 'docstore_migrate_marker')
      continue;
    if (MONGO_COLLECTIONS.includes(name)) continue;
    if (n > 0)
      red.push({
        check: 'unmapped_collection',
        message: `collection '${name}' holds ${String(n)} documents and no mapping covers it`,
        data: { name, n },
      });
    else
      yellow.push({
        check: 'unmapped_collection',
        message: `empty collection '${name}' has no mapping`,
      });
  }
  for (const [name, why] of Object.entries(MUST_BE_EMPTY))
    if ((counts.get(name) ?? 0) > 0)
      red.push({
        check: 'legacy_conversations',
        message: why,
        data: { name, n: counts.get(name) },
      });
  for (const i of IGNORED) {
    const n = counts.get(i.collection) ?? 0;
    if (n > 0 && !(i.collection in MUST_BE_EMPTY))
      yellow.push({
        check: 'not_copied',
        message: `${i.collection}: ${String(n)} documents are not copied (${i.reason})`,
        data: { collection: i.collection, n },
      });
  }

  // per collection: _id shape, census, org id types
  const findings: CensusFinding[] = [];
  const orgTypes: Record<string, Record<string, number>> = {};
  for (const m of MAPPINGS) {
    const total = counts.get(m.collection) ?? 0;
    if (total === 0) continue;
    say(`census ${m.collection} (${String(total)})`);
    const db = sourceDb(rt, m);
    // a custom mapping (the slug counters) keys its rows by something other than `_id`, so the id's type does not matter there
    const nonObjectIds = m.custom
      ? []
      : await nonObjectIdSample(db, m.collection);
    if (nonObjectIds.length)
      red.push({
        check: 'id_shape',
        message: `${m.collection}: _id values that are not ObjectIds (the copy keeps ids as ObjectId text; a string or number id would be read back as another document)`,
        data: nonObjectIds,
      });
    const c = new Census();
    const cursor =
      total <= FULL_CENSUS_UP_TO
        ? db.collection(m.collection).find({}, { promoteLongs: false })
        : db
            .collection(m.collection)
            .aggregate([{ $sample: { size: SAMPLE } }], { allowDiskUse: true });
    for await (const d of cursor) c.add(d);
    if (total > FULL_CENSUS_UP_TO)
      yellow.push({
        check: 'census_sampled',
        message: `${m.collection}: census sampled ${String(SAMPLE)} of ${String(total)} documents; the verifier repeats it over every document`,
      });
    findings.push(...judgeCensus(m, c));
    if (c.withoutVersion > 0)
      info[`${m.collection}.withoutVersion`] = c.withoutVersion;
    if (m.orgPath && m.orgPath !== '_id') {
      const st = c.paths.get(m.orgPath);
      if (st) orgTypes[m.collection] = st.types;
      if (!m.orgPath.startsWith('__') && (!st || st.count < c.docs))
        yellow.push({
          check: 'org_missing',
          message: `${m.collection}: ${String(c.docs - (st?.count ?? 0))} documents have no ${m.orgPath}; they cannot be stamped with an organisation and are not copied (counted in the report)`,
          data: { collection: m.collection },
        });
    }
  }
  info.orgIdTypes = orgTypes;
  for (const f of findings) {
    if (f.kind === 'unmapped_path')
      red.push({
        check: 'path_census',
        message: `${f.collection}.${f.path} is in the data (${String(f.count)} documents, ${f.detail ?? ''}) and no mapping covers it`,
        data: f,
      });
    else if (f.kind === 'null_in_omit_field')
      red.push({
        check: 'null_census',
        message: `${f.collection}.${f.path}: ${String(f.count)} stored nulls; ${f.detail ?? ''}`,
        data: f,
      });
    else
      yellow.push({
        check: 'nul_census',
        message: `${f.collection}.${f.path}: ${String(f.count)} documents contain U+0000; it is removed in the copy (listed transform)`,
        data: f,
      });
  }

  // duplicates that a new unique key would reject
  if ((counts.get('users') ?? 0) > 0) {
    const dup = await apiDb
      .collection('users')
      .aggregate(
        [
          {
            $group: {
              _id: { o: '$orgId', e: { $toLower: '$email' } },
              n: { $sum: 1 },
            },
          },
          { $match: { n: { $gt: 1 } } },
          { $limit: 5 },
        ],
        { allowDiskUse: true },
      )
      .toArray();
    if (dup.length)
      red.push({
        check: 'duplicate_users',
        message:
          'two or more users share an email address inside one organisation (the new unique key rejects them)',
        data: dup.length,
      });
  }
  if ((counts.get('userGroups') ?? 0) > 0) {
    const dup = await apiDb
      .collection('userGroups')
      .aggregate(
        [
          { $match: { isDeleted: { $ne: true } } },
          { $group: { _id: { o: '$orgId', n: '$name' }, k: { $sum: 1 } } },
          { $match: { k: { $gt: 1 } } },
          { $limit: 5 },
        ],
        { allowDiskUse: true },
      )
      .toArray();
    if (dup.length)
      red.push({
        check: 'duplicate_groups',
        message: 'two or more live groups share a name inside one organisation',
        data: dup.length,
      });
  }
  if ((counts.get('chatSessionMessages') ?? 0) > 0) {
    const dup = await apiDb
      .collection('chatSessionMessages')
      .aggregate(
        [
          { $group: { _id: { s: '$sessionId', q: '$seq' }, n: { $sum: 1 } } },
          { $match: { n: { $gt: 1 } } },
          { $limit: 5 },
        ],
        { allowDiskUse: true },
      )
      .toArray();
    if (dup.length)
      red.push({
        check: 'duplicate_message_seq',
        message: 'two messages of one session share a position',
        data: dup.length,
      });
  }

  if (
    (counts.get('chatSessionMessages') ?? 0) > 0 &&
    (counts.get('chatSessions') ?? 0) > 0
  ) {
    // the session's allocator holds the last position handed out: no message may be beyond it, or the next message would take a used one
    let bad = 0;
    const cur = apiDb
      .collection('chatSessionMessages')
      .aggregate([{ $group: { _id: '$sessionId', top: { $max: '$seq' } } }], {
        allowDiskUse: true,
      });
    let chunk: Array<{ _id: unknown; top: number }> = [];
    const check = async (): Promise<void> => {
      const have = await apiDb
        .collection('chatSessions')
        .find({ _id: { $in: chunk.map((c) => c._id) } } as never, {
          projection: { nextSeq: 1 },
        })
        .toArray();
      const next = new Map(
        have.map((h) => [String(h._id), Number(h.nextSeq ?? 0)]),
      );
      for (const c of chunk)
        if (
          next.has(String(c._id)) &&
          c.top > (next.get(String(c._id)) as number)
        )
          bad += 1;
      chunk = [];
    };
    for await (const g of cur) {
      chunk.push(g as never);
      if (chunk.length >= 1000) await check();
    }
    if (chunk.length) await check();
    if (bad)
      red.push({
        check: 'seq_allocator',
        message: `${String(bad)} chat sessions have a message beyond the positions their allocator has handed out`,
        data: bad,
      });
  }

  if ((counts.get('citations') ?? 0) > 0) {
    // a citation is copied with the organisation of the message or search that cites it; one stamped with another organisation is not copied
    const ref = new Map<string, string>();
    for await (const sr of apiDb
      .collection('searches')
      .find({}, { projection: { orgId: 1, citationIds: 1 } }))
      for (const id of (sr.citationIds ?? []) as unknown[])
        ref.set(String(id), String(sr.orgId));
    for await (const m of apiDb
      .collection('chatSessionMessages')
      .find(
        { 'citations.0': { $exists: true } },
        { projection: { orgId: 1, citations: 1 } },
      ))
      for (const c of (m.citations ?? []) as Array<{ citationId?: unknown }>)
        if (c.citationId) ref.set(String(c.citationId), String(m.orgId));
    let mismatch = 0;
    let unreferenced = 0;
    for await (const c of apiDb
      .collection('citations')
      .find({}, { projection: { 'metadata.orgId': 1 } })) {
      const want = ref.get(String(c._id));
      if (!want) unreferenced += 1;
      else if (
        String((c.metadata as { orgId?: unknown } | undefined)?.orgId ?? '') !==
        want
      )
        mismatch += 1;
    }
    if (mismatch)
      red.push({
        check: 'citation_org',
        message: `${String(mismatch)} citations are stamped with another organisation than the message or search that cites them (or with no valid one); they cannot be stored under the organisation of what cites them, so the answers would lose them: correct their metadata.orgId first`,
        data: mismatch,
      });
    if (unreferenced)
      yellow.push({
        check: 'citation_unreferenced',
        message: `${String(unreferenced)} citations are cited by no message or search (they are copied as they are)`,
      });
  }

  // outbox
  if ((counts.get('outbox_events') ?? 0) > 0) {
    const by = await apiDb
      .collection('outbox_events')
      .aggregate([{ $group: { _id: '$status', n: { $sum: 1 } } }])
      .toArray();
    info.outbox = Object.fromEntries(
      by.map((r: Document) => [String(r._id), r.n]),
    );
    // two events whose ids fold to the same table id cannot both be stored
    const seen = new Map<string, string>();
    const clash: string[] = [];
    for await (const e of apiDb
      .collection('outbox_events')
      .find({ status: { $ne: 'published' } }, { projection: { _id: 1 } })) {
      const id = outboxTableId(idString(e._id));
      const other = seen.get(id);
      if (other) clash.push(`${other} / ${idString(e._id)}`);
      else seen.set(id, idString(e._id));
    }
    if (clash.length)
      red.push({
        check: 'outbox_id_collision',
        message: `${String(clash.length)} pairs of unsent outbox events would get the same table id (${clash.slice(0, 3).join('; ')}): let the application deliver them, or mark one of each pair published, then run preflight again`,
        data: clash.slice(0, 20),
      });
  }

  // sizes and the target's room
  let dataBytes = 0;
  try {
    dataBytes = (await apiDb.stats()).dataSize as number;
    info.mongoDataBytes = dataBytes;
  } catch {
    yellow.push({ check: 'sizes', message: 'database size could not be read' });
  }
  const need = Math.ceil(dataBytes * DISK_FACTOR);
  info.targetNeedBytes = need;
  let free: number | undefined = rt.cfg.pgFreeBytes;
  if (free === undefined && rt.cfg.pgDataDir) {
    try {
      const s = await statfs(rt.cfg.pgDataDir);
      free = s.bavail * s.bsize;
    } catch (e) {
      yellow.push({
        check: 'disk',
        message: `target data directory could not be inspected: ${(e as Error).message}`,
      });
    }
  }
  if (free === undefined)
    yellow.push({
      check: 'disk',
      message: `free space on the target is unknown (set PG_DATA_DIR to a mount of the PostgreSQL data volume); the copy needs about ${String(need)} bytes`,
    });
  else {
    info.targetFreeBytes = free;
    if (free < need)
      red.push({
        check: 'disk',
        message: `the target has ${String(free)} bytes free and the copy needs about ${String(need)} (data x ${String(DISK_FACTOR)} for indexes, text search and WAL)`,
        data: { free, need },
      });
  }

  // target PostgreSQL
  if (importLoginShut(rt.cfg.pgImportUrl))
    yellow.push({
      check: 'target',
      message:
        'the target was not inspected: the import login is disabled (no PG_IMPORT_PASSWORD). scripts/migrate-to-postgres.sh enables it for the length of a move',
    });
  else
    try {
      const cur = await JobState.current(rt);
      info.job = cur ? { id: cur.id, phase: cur.progress.phase } : null;
      const { rows } = await rt.migratePool.query<{ n: string }>(
        "/* pipeshub:preflight.orgs_table */ SELECT count(*)::text AS n FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace WHERE s.nspname = 'identity' AND c.relname = 'orgs'",
      );
      const epochs = await rt.migratePool.query<{
        branch: string;
        epoch: string;
        min_code_epoch: string;
      }>(
        '/* pipeshub:preflight.schema_epochs */ SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch',
      );
      const versionProblems = schemaProblems(
        epochs.rows.map((r) => ({
          branch: r.branch,
          epoch: Number(r.epoch),
          min_code_epoch: Number(r.min_code_epoch),
        })),
        migratorRequirements(),
      );
      for (const p of versionProblems)
        red.push({
          check: 'app_version',
          message: `this migrator build and the PostgreSQL schema do not match: ${p}. Use the migrator image of the same release as the schema (and as the application that wrote the data)`,
        });
      if (rows[0]?.n !== '1')
        red.push({
          check: 'target_schema',
          message:
            'the document-store tables do not exist in PostgreSQL: run the schema step (python -m db.migrate) first',
        });
      else {
        const o = await rt.jobs(
          async (_tx, k) =>
            (
              await k.query<{ n: string }>(
                '/* pipeshub:preflight.orgs */ SELECT count(*)::text AS n FROM identity.orgs',
              )
            ).rows[0]?.n,
          { write: false },
        );
        info.targetOrgs = Number(o);
        if (!cur && Number(o) > 0)
          red.push({
            check: 'target_not_empty',
            message: `PostgreSQL already holds ${String(o)} organisations and no migration is running: this is not an empty target`,
          });
      }
    } catch (e) {
      const msg = (e as Error).message;
      // the import login is shut except during a move; scripts/migrate-to-postgres.sh opens it, so this is not a failure here
      if (/not permitted to log in|password authentication failed/i.test(msg))
        yellow.push({
          check: 'target',
          message: `the target could not be inspected: the import login is disabled (${msg}). scripts/migrate-to-postgres.sh enables it for the length of a move`,
        });
      else
        red.push({
          check: 'target',
          message: `PostgreSQL is not usable by the migrator: ${msg}`,
        });
    }

  return {
    ok: red.length === 0,
    at: new Date().toISOString(),
    red,
    yellow,
    info,
  };
}
