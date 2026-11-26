/**
 * Durable runs, their journal and their payloads (R46-CARRY, R45CARRY-FU-1), carried as one unit: the journal names the payloads
 * and the runs, the runs name their sessions' counters and receipts, and a run resumed after the move needs all of it.
 *
 *  - `agentRuns` -> `harness.runs` (by org and run): the run document in `doc`, read in `_id` order so the table's insertion order
 *    (`admission_order`, the tie-break of "newest first") is MongoDB's;
 *  - `agentEvents` -> `harness.run_events`, `agentEventCounters` -> `harness.session_counters` (by org and session; lists absent
 *    before SS1/SS2 read as empty, as both stores read them), `agentNextRuns` -> `harness.next_runs`;
 *  - `sessionEvents` -> `harness.session_events` (by org, stream and seq), `sessionStreamCounters` -> `harness.stream_counters`;
 *  - `agentJournalPayloads` -> `harness.journal_payloads`: the sealed bytes as they are, by the same `<org>:<hash>` id;
 *  - `agentSessionForks`, `agentSandboxSnapshots`, `agentCommandReceipts`, `agentAdmissionReceipts` (its org is the admitted run's),
 *    `agentAdmissionQuotas` (its org is in its key);
 *  - `agentSandboxReaps` -> `harness.sandbox_reaps`: deployment-wide (no `org_id`); the org MongoDB copies out of the label set is
 *    recomputed from it.
 *
 * Payload names are not rewritten (`payload-store/names.ts`: either document store reads either form for its org; names sit inside
 * sealed data and digested event data). On top of the document-by-document compare, every payload name the carried documents show
 * is resolved on both sides by that one rule, its bytes checked against the hash the name and the claim-check carry, and the two
 * verdicts must be the same: a payload readable on MongoDB is readable on PostgreSQL, and one already missing stays visibly missing.
 */
import { createHash } from 'node:crypto';
import type { Tx as KernelTx } from '../../../libs/db';
import { payloadRowId } from '../../../libs/repositories/agent-harness/payload-store/names';
import { contentRefsIn } from '../../../modules/agent-harness/domain/payload-references';
import { DocTable, docTableCarrier, type DocTableSpec } from './doc-table';
import type { Carrier, CarryMongo, Doc } from './types';

const orgOfQuotaKey = (id: string): string => {
  const parts = JSON.parse(id) as unknown;
  const org = Array.isArray(parts) ? parts[1] : undefined;
  if (typeof org !== 'string' || !org)
    throw new Error(
      `agentAdmissionQuotas ${id}: the key names no organisation`,
    );
  return org;
};

export const DURABLE_RUN_TABLES: DocTableSpec[] = [
  {
    collection: 'agentRuns',
    table: 'harness.runs',
    orgPath: 'orgId',
    key: [{ col: 'run_id', path: 'runId', kind: 'text' }],
    doc: { omit: [] },
    ordered: true,
  },
  {
    collection: 'agentEvents',
    table: 'harness.run_events',
    orgPath: 'orgId',
    key: [
      { col: 'session_id', path: 'sessionId', kind: 'text' },
      { col: 'event_seq', path: 'eventSeq', kind: 'int' },
    ],
    columns: [
      { col: 'run_id', path: 'runId', kind: 'text' },
      { col: 'commit_seq', path: 'commitSeq', kind: 'int' },
    ],
    doc: { omit: [] },
  },
  {
    collection: 'agentEventCounters',
    table: 'harness.session_counters',
    orgPath: 'orgId',
    key: [{ col: 'session_id', path: 'sessionId', kind: 'text' }],
    columns: [
      { col: 'next_event_seq', path: 'nextEventSeq', kind: 'int' },
      // both stores read an absent list as empty (`?? []`)
      {
        col: 'journal_features',
        path: 'journalFeatures',
        kind: 'texts',
        absentAs: [],
      },
      { col: 'actors', path: 'actors', kind: 'texts', absentAs: [] },
      {
        col: 'compact_request',
        path: 'compactRequest',
        kind: 'json',
        nullable: true,
      },
    ],
  },
  {
    collection: 'agentNextRuns',
    table: 'harness.next_runs',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    doc: { omit: [] },
  },
  {
    collection: 'sessionEvents',
    table: 'harness.session_events',
    orgPath: 'orgId',
    key: [
      { col: 'stream_id', path: 'streamId', kind: 'text' },
      { col: 'seq', path: 'seq', kind: 'int' },
    ],
    columns: [
      { col: 'session_id', path: 'sessionId', kind: 'text' },
      { col: 'run_id', path: 'runId', kind: 'text', nullable: true },
      { col: 'ts', path: 'ts', kind: 'date' },
    ],
    doc: { omit: [] },
  },
  {
    collection: 'sessionStreamCounters',
    table: 'harness.stream_counters',
    orgPath: 'orgId',
    key: [{ col: 'stream_id', path: 'streamId', kind: 'text' }],
    columns: [
      { col: 'last_seq', path: 'lastSeq', kind: 'int' },
      { col: 'ts', path: 'ts', kind: 'date' },
    ],
  },
  {
    collection: 'agentJournalPayloads',
    table: 'harness.journal_payloads',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    columns: [
      { col: 'hash', path: 'hash', kind: 'text' },
      { col: 'size', path: 'size', kind: 'int' },
      { col: 'bytes', path: 'bytes', kind: 'bytes' },
      { col: 'created_at', path: 'createdAt', kind: 'date' },
      {
        col: 'owner_session_id',
        path: 'owner.sessionId',
        kind: 'text',
        nullable: true,
      },
      {
        col: 'owner_run_id',
        path: 'owner.runId',
        kind: 'text',
        nullable: true,
      },
      { col: 'referenced', path: 'referenced', kind: 'bool', nullable: true },
    ],
  },
  {
    collection: 'agentSessionForks',
    table: 'harness.session_forks',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    columns: [
      { col: 'source_session_id', path: 'sourceSessionId', kind: 'text' },
      { col: 'actor_id', path: 'actorId', kind: 'text' },
      { col: 'fork_session_id', path: 'forkSessionId', kind: 'text' },
      {
        col: 'result',
        path: 'result',
        kind: 'json',
        nullable: true,
        absentAs: null,
      },
      { col: 'created_at', path: 'createdAt', kind: 'int' },
      {
        col: 'completed_at',
        path: 'completedAt',
        kind: 'int',
        nullable: true,
        absentAs: null,
      },
    ],
  },
  {
    collection: 'agentSandboxReaps',
    table: 'harness.sandbox_reaps',
    deployment: true,
    noRegion: true,
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    columns: [
      { col: 'labels', path: 'labels', kind: 'json' },
      { col: 'failures', path: 'failures', kind: 'int' },
      { col: 'next_attempt_at', path: 'nextAttemptAt', kind: 'float' },
      { col: 'created_at', path: 'createdAt', kind: 'float' },
      {
        col: 'last_error_type',
        path: 'lastErrorType',
        kind: 'text',
        nullable: true,
        absentAs: null,
      },
    ],
    // MongoDB keeps a copy of the label set's org (`labels.orgId ?? ''`, MongoPendingReaps.defer); the table keeps the labels whole
    derived: {
      orgId: (d) => {
        const labels = d.labels as Record<string, unknown> | undefined;
        return typeof labels?.orgId === 'string' ? labels.orgId : '';
      },
    },
  },
  {
    collection: 'agentSandboxSnapshots',
    table: 'harness.sandbox_snapshots',
    orgPath: 'orgId',
    key: [{ col: 'object_id', path: 'objectId', kind: 'text' }],
    columns: [
      { col: 'session_id', path: 'sessionId', kind: 'text' },
      {
        col: 'run_id',
        path: 'runId',
        kind: 'text',
        nullable: true,
        absentAs: null,
      },
      { col: 'taken_at', path: 'takenAt', kind: 'date' },
      // the document's `region` is the snapshot's; the row's own `region` is where the row was written
      { col: 'snapshot_region', path: 'region', kind: 'text' },
    ],
    // the MongoDB store's id: `<org>:<objectId>`
    newId: (doc) => ({
      id: `${String(doc.orgId)}:${String(doc.objectId)}`,
      oid: false,
    }),
  },
  {
    collection: 'agentCommandReceipts',
    table: 'harness.command_receipts',
    orgPath: 'orgId',
    key: [
      { col: 'run_id', path: 'runId', kind: 'text' },
      { col: 'command_id', path: 'commandId', kind: 'text' },
    ],
    columns: [
      { col: 'payload_hash', path: 'payloadHash', kind: 'text' },
      { col: 'receipt', path: 'receipt', kind: 'json' },
      { col: 'created_at', path: 'createdAt', kind: 'float' },
    ],
  },
  {
    collection: 'agentAdmissionReceipts',
    table: 'harness.admission_receipts',
    // a receipt keeps no org of its own: it is the admitted run's (the PostgreSQL store writes the command's org)
    orgPath: 'result.run.orgId',
    key: [{ col: 'scope_key', path: 'scopeKey', kind: 'text' }],
    columns: [
      { col: 'payload_hash', path: 'payloadHash', kind: 'text' },
      { col: 'result', path: 'result', kind: 'json' },
      { col: 'created_at', path: 'createdAt', kind: 'float' },
    ],
  },
  {
    collection: 'agentAdmissionQuotas',
    table: 'harness.admission_quotas',
    // the key is `["org", org]` or `["user", org, actor]` (orgQuotaKey, userQuotaKey)
    orgOf: (src) => orgOfQuotaKey(src.id),
    key: [{ col: 'key', path: '_id', kind: 'text' }],
    idCol: 'key',
    columns: [
      { col: 'window_start', path: 'windowStart', kind: 'float' },
      { col: 'count', path: 'count', kind: 'int' },
      // both ledgers read absent holds as none
      { col: 'holds', path: 'holds', kind: 'json', absentAs: [] },
    ],
  },
];

/** Where payload names show in a collection's documents: the claim-check (`payloadRef`) and `sealed:` content refs anywhere. */
const NAMING = [
  'sessionEvents',
  'agentEvents',
  'agentRuns',
  'agentNextRuns',
  'agentAdmissionReceipts',
];

interface Named {
  org: string;
  ref: string;
  size?: number;
  hash?: string;
}

const sha256 = (bytes: Buffer): string =>
  `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

/** The payload names one document shows, with the size and hash a claim-check records. */
function namesIn(collection: string, doc: Doc): Named[] {
  const org =
    collection === 'agentAdmissionReceipts'
      ? (doc.result as { run?: { orgId?: unknown } } | undefined)?.run?.orgId
      : doc.orgId;
  if (typeof org !== 'string') return [];
  const out: Named[] = contentRefsIn(doc).map((ref) => ({ org, ref }));
  const claim = doc.payloadRef as
    { ref?: unknown; size?: unknown; hash?: unknown } | undefined;
  if (claim && typeof claim.ref === 'string')
    out.push({
      org,
      ref: claim.ref,
      ...(typeof claim.size === 'number' ? { size: claim.size } : {}),
      ...(typeof claim.hash === 'string' ? { hash: claim.hash } : {}),
    });
  return out;
}

/**
 * Each name's verdict, as text: `foreign` (not a document-store name of its org: another store's, or inline), `missing`, `corrupt`
 * (the bytes are not what the name or the claim-check says), or `ok`.
 */
function verdicts(
  names: Named[],
  bytesOf: (id: string) => Buffer | undefined,
): Map<string, string> {
  const out = new Map<string, string>();
  for (const n of names) {
    const id = payloadRowId(n.org, n.ref);
    let v: string;
    if (id === null) v = 'foreign';
    else {
      const bytes = bytesOf(id);
      const hash = id.slice(n.org.length + 1);
      if (bytes === undefined) v = 'missing';
      else if (
        (hash.startsWith('sha256:') && sha256(bytes) !== hash) ||
        (n.size !== undefined && bytes.length !== n.size) ||
        (n.hash !== undefined && sha256(bytes) !== n.hash)
      )
        v = 'corrupt';
      else v = 'ok';
    }
    const key = `${n.org}|${n.ref}`;
    // one name shown twice with different claims keeps the worse verdict
    if (out.get(key) !== 'corrupt')
      out.set(key, out.get(key) === 'missing' && v === 'ok' ? 'missing' : v);
  }
  return out;
}

async function mongoVerdicts(mongo: CarryMongo): Promise<Map<string, string>> {
  const names: Named[] = [];
  for (const c of NAMING)
    for await (const d of mongo.scan(c))
      if (!d.refused) names.push(...namesIn(c, d.doc));
  const wanted = new Set(
    names
      .map((n) => payloadRowId(n.org, n.ref))
      .filter((x): x is string => x !== null),
  );
  const bytes = new Map<string, Buffer>();
  for await (const d of mongo.scan('agentJournalPayloads'))
    if (wanted.has(d.id) && Buffer.isBuffer(d.doc.bytes))
      bytes.set(d.id, d.doc.bytes);
  return verdicts(names, (id) => bytes.get(id));
}

async function pgVerdicts(k: KernelTx): Promise<Map<string, string>> {
  const names: Named[] = [];
  for (const c of NAMING) {
    const spec = DURABLE_RUN_TABLES.find(
      (s) => s.collection === c,
    ) as DocTableSpec;
    for (const v of (await new DocTable(spec).actual(k)).values()) {
      const { _id: _unused, ...doc } = v as Doc;
      names.push(...namesIn(c, doc));
    }
  }
  const ids = [
    ...new Set(
      names
        .map((n) => payloadRowId(n.org, n.ref))
        .filter((x): x is string => x !== null),
    ),
  ];
  const bytes = new Map<string, Buffer>();
  for (let i = 0; i < ids.length; i += 500) {
    const { rows } = await k.query<{ id: string; bytes: Buffer }>(
      `/* pipeshub:migrate.carry_payload_bytes */ SELECT id, bytes FROM harness.journal_payloads WHERE id = ANY($1::text[])`,
      [ids.slice(i, i + 500)],
    );
    for (const r of rows) bytes.set(r.id, r.bytes);
  }
  return verdicts(names, (id) => bytes.get(id));
}

/** Every payload name resolved on both sides: a name whose verdict differs is a problem (named by org and name, never by content). */
export async function payloadNameProblems(
  k: KernelTx,
  mongo: CarryMongo,
): Promise<string[]> {
  const before = await mongoVerdicts(mongo);
  const after = await pgVerdicts(k);
  const problems: string[] = [];
  for (const key of new Set([...before.keys(), ...after.keys()]))
    if (before.get(key) !== after.get(key))
      problems.push(
        `the payload ${key.replace('|', ' ')} resolves differently: MongoDB ${before.get(key) ?? 'not named'}, PostgreSQL ${after.get(key) ?? 'not named'}`,
      );
  return problems;
}

export const DURABLE_RUNS: Carrier = {
  ...docTableCarrier(
    'durable-runs',
    'durable runs, their journal, counters, queued work, receipts and claim-checked payloads, moved together; every payload name resolved on both sides',
    DURABLE_RUN_TABLES,
  ),
  check: payloadNameProblems,
};
