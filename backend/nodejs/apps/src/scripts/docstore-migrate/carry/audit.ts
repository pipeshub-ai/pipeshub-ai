/**
 * The hash-chained audit trail (R45-CARRY group 3), carried as one unit: the records, each org's chain head, the appends not yet
 * linked, and the local anchors. Each is compared document by document like any document table; on top, the chain is re-checked
 * on both sides with the harness's own `verifyAuditChain` (links, hashes, head, anchors), and the two verdicts must be the same:
 * a chain that verifies on MongoDB verifies on PostgreSQL, and a chain that was already broken stays visibly broken, never repaired
 * or hidden by the move.
 *
 *  - `agentAuditRecords` -> `harness.audit_records` (by org and seq): the record in `doc`, as the PostgreSQL linker writes it;
 *  - `agentAuditHeads` -> `harness.audit_heads` (one per org): typed columns;
 *  - `agentAuditPending` -> `harness.audit_pending` (by org and id): the document's id is the row's id, its entries and digests in
 *    `doc`; an append made on PostgreSQL after the move is given a new ObjectId at a rollback;
 *  - `agentAuditAnchors` -> `harness.audit_anchors` (by org and seq): typed columns; the MongoDB sink's id is `<org>:<seq>`.
 */
import {
  verifyAuditChain,
  type AuditAnchor,
  type AuditRecord,
} from '../../../modules/agent-harness/domain/audit';
import type { Tx as KernelTx } from '../../../libs/db';
import { DocTable, docTableCarrier, type DocTableSpec } from './doc-table';
import type { Carrier, CarryMongo, Doc } from './types';

export const AUDIT_TABLES: DocTableSpec[] = [
  {
    collection: 'agentAuditRecords',
    table: 'harness.audit_records',
    orgPath: 'orgId',
    key: [{ col: 'seq', path: 'seq', kind: 'int' }],
    columns: [
      { col: 'kind', path: 'kind', kind: 'text' },
      { col: 'session_id', path: 'sessionId', kind: 'text', nullable: true },
      { col: 'run_id', path: 'runId', kind: 'text', nullable: true },
      { col: 'ts', path: 'ts', kind: 'text' },
    ],
    doc: { omit: [] },
  },
  {
    collection: 'agentAuditHeads',
    table: 'harness.audit_heads',
    orgPath: 'orgId',
    key: [],
    columns: [
      { col: 'seq', path: 'seq', kind: 'int' },
      { col: 'hash', path: 'hash', kind: 'text' },
      { col: 'anchored_seq', path: 'anchoredSeq', kind: 'int' },
    ],
  },
  {
    collection: 'agentAuditPending',
    table: 'harness.audit_pending',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    columns: [
      { col: 'stream_id', path: 'streamId', kind: 'text' },
      { col: 'journal_seq', path: 'journalSeq', kind: 'int' },
      { col: 'ts', path: 'ts', kind: 'text' },
    ],
    doc: { omit: ['orgId', 'streamId', 'journalSeq', 'ts'] },
  },
  {
    collection: 'agentAuditAnchors',
    table: 'harness.audit_anchors',
    orgPath: 'orgId',
    key: [{ col: 'seq', path: 'seq', kind: 'int' }],
    columns: [
      { col: 'hash', path: 'hash', kind: 'text' },
      { col: 'anchored_at', path: 'anchoredAt', kind: 'text' },
    ],
    newId: (doc) => ({
      id: `${String(doc.orgId)}:${String(doc.seq)}`,
      oid: false,
    }),
  },
];

interface Chains {
  records: Map<string, AuditRecord[]>;
  heads: Map<string, { seq: number; hash: string }>;
  anchors: Map<string, AuditAnchor[]>;
}

const push = <T>(m: Map<string, T[]>, k: string, v: T): void => {
  m.set(k, [...(m.get(k) ?? []), v]);
};

/** Each org's verdict, as text: what `verifyAuditChain` says of its records, head and anchors. */
function verdicts(c: Chains): Map<string, string> {
  const out = new Map<string, string>();
  const orgs = new Set([
    ...c.records.keys(),
    ...c.heads.keys(),
    ...c.anchors.keys(),
  ]);
  for (const org of orgs) {
    const records = [...(c.records.get(org) ?? [])].sort(
      (a, b) => a.seq - b.seq,
    );
    const v = verifyAuditChain(records, {
      head: c.heads.get(org) ?? null,
      anchors: [...(c.anchors.get(org) ?? [])].sort((a, b) => a.seq - b.seq),
    });
    out.set(
      org,
      JSON.stringify({
        ok: v.ok,
        records: v.records,
        lastSeq: v.lastSeq,
        problems: v.problems,
      }),
    );
  }
  return out;
}

async function mongoChains(mongo: CarryMongo): Promise<Chains> {
  const c: Chains = {
    records: new Map(),
    heads: new Map(),
    anchors: new Map(),
  };
  for await (const d of mongo.scan('agentAuditRecords'))
    push(c.records, String(d.doc.orgId), d.doc as unknown as AuditRecord);
  for await (const d of mongo.scan('agentAuditHeads'))
    c.heads.set(String(d.doc.orgId), {
      seq: Number(d.doc.seq),
      hash: String(d.doc.hash),
    });
  for await (const d of mongo.scan('agentAuditAnchors'))
    push(c.anchors, String(d.doc.orgId), d.doc as unknown as AuditAnchor);
  return c;
}

async function pgChains(k: KernelTx): Promise<Chains> {
  const c: Chains = {
    records: new Map(),
    heads: new Map(),
    anchors: new Map(),
  };
  const docs = async (collection: string): Promise<Doc[]> => {
    const spec = AUDIT_TABLES.find(
      (s) => s.collection === collection,
    ) as DocTableSpec;
    return [...(await new DocTable(spec).actual(k)).values()].map((v) => {
      const { _id: _unused, ...doc } = v as Doc;
      return doc;
    });
  };
  for (const d of await docs('agentAuditRecords'))
    push(c.records, String(d.orgId), d as unknown as AuditRecord);
  for (const d of await docs('agentAuditHeads'))
    c.heads.set(String(d.orgId), { seq: Number(d.seq), hash: String(d.hash) });
  for (const d of await docs('agentAuditAnchors'))
    push(c.anchors, String(d.orgId), d as unknown as AuditAnchor);
  return c;
}

/** The chain re-checked on both sides: an org whose verdict differs is a problem (named by org and the verdicts, no record content). */
export async function auditChainProblems(
  k: KernelTx,
  mongo: CarryMongo,
): Promise<string[]> {
  const before = verdicts(await mongoChains(mongo));
  const after = verdicts(await pgChains(k));
  const problems: string[] = [];
  for (const org of new Set([...before.keys(), ...after.keys()]))
    if (before.get(org) !== after.get(org))
      problems.push(
        `the audit chain of ${org} verifies differently: MongoDB ${before.get(org) ?? 'none'}, PostgreSQL ${after.get(org) ?? 'none'}`,
      );
  return problems;
}

export const AUDIT_CHAIN: Carrier = {
  ...docTableCarrier(
    'audit-chain',
    'the hash-chained audit trail: records, chain heads, appends not yet linked and anchors, moved together and re-checked on both sides',
    AUDIT_TABLES,
  ),
  check: auditChainProblems,
};
