/**
 * Documents of the carried collections as the MongoDB stores write them (R45-CARRY): the data keys and workspaces
 * (`MongoDataKeyStore`, `MongoWorkspaceRepository`, `MongoWorkspaceKeyStore`, the head CAS) and the coding-engine fields of an org,
 * a person and a chat (`MongoCodingEnginePreferences`).
 */
import { ObjectId } from 'mongodb';
import type { Doc } from '../../../src/scripts/docstore-migrate/carry/types';
import { chainAuditRecords, type AuditEntry } from '../../../src/modules/agent-harness/domain/audit';

export const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa';
export const ORG2 = 'bbbbbbbbbbbbbbbbbbbbbbbb';
export const USER = new ObjectId('cccccccccccccccccccccccc');
export const USER2 = new ObjectId('cccccccccccccccccccccccd');
export const SESSION = new ObjectId('dddddddddddddddddddddddd');
export const SESSION2 = new ObjectId('ddddddddddddddddddddddde');

const sealed = (kid: string, ct: string): Doc => ({ v: 1, kid, kv: 1, iv: 'aXY=', tag: 'dGFn', ct });

export const dataKeys = (): Doc[] => [
  { _id: 'kid-A1', orgId: ORG, version: 1, kekId: 'kek-1', wrapped: 'd3JhcHBlZC1rZXktMQ==', createdAt: 1_700_000_000_000, seals: 7 },
  // created before seals were counted: the store reads the count as zero
  { _id: 'Kid-B2', orgId: ORG, version: 2, kekId: 'kek-1', wrapped: 'd3JhcHBlZC1rZXktMg==', createdAt: 1_700_000_100_000 },
  { _id: 'kid-C1', orgId: ORG2, version: 1, kekId: 'kek-2', wrapped: 'd3JhcHBlZC1rZXktMw==', createdAt: 1_700_000_200_000, seals: 0 },
];

export const workspaceHeads = (): Doc[] => [
  { _id: new ObjectId('eeeeeeeeeeeeeeeeeeeeee01'), orgId: ORG, workspaceId: `session:${SESSION.toHexString()}`, revision: 3, runId: 'run-1', updatedAt: 1_700_000_300_000 },
  {
    _id: new ObjectId('eeeeeeeeeeeeeeeeeeeeee02'),
    orgId: ORG,
    workspaceId: `session:${SESSION.toHexString()}:fork-1`,
    revision: 2,
    runId: 'run-2',
    updatedAt: 1_700_000_400_000,
    forkOf: { workspaceId: `session:${SESSION.toHexString()}`, revision: 1 },
    mergeBase: 1,
    lastMerge: { key: 'm1', into: `session:${SESSION.toHexString()}`, base: 1, theirs: 2, ours: 3, revision: null, status: 'conflict' },
  },
];

export const workspaceManifests = (): Doc[] => [
  {
    _id: new ObjectId('eeeeeeeeeeeeeeeeeeeeee11'),
    orgId: ORG,
    workspaceId: `session:${SESSION.toHexString()}`,
    revision: 1,
    parentRevision: null,
    contentHash: 'h1',
    fileCount: 2,
    files: sealed('kid-A1', 'Y3Qx'),
    reason: 'run',
    runId: 'run-1',
    createdAt: 1_700_000_300_000,
  },
  {
    _id: new ObjectId('eeeeeeeeeeeeeeeeeeeeee12'),
    orgId: ORG,
    workspaceId: `session:${SESSION.toHexString()}`,
    revision: 2,
    parentRevision: 1,
    contentHash: 'h2',
    fileCount: 3,
    files: sealed('kid-A1', 'Y3Qy'),
    reason: 'upload',
    runId: 'run-1',
    createdAt: 1_700_000_310_000,
  },
];

export const workspaceKeys = (): Doc[] => [
  { _id: new ObjectId('eeeeeeeeeeeeeeeeeeeeee21'), orgId: ORG, sessionId: SESSION.toHexString(), keyId: 'sk-1', wrapped: sealed('kid-A1', 'c2Vzc2lvbi1rZXk='), createdAt: 1_700_000_300_000, destroyedAt: null },
  // a shredded key's tombstone: no new key may be issued for the session
  { _id: new ObjectId('eeeeeeeeeeeeeeeeeeeeee22'), orgId: ORG, sessionId: SESSION2.toHexString(), keyId: '', wrapped: null, createdAt: 1_700_000_500_000, destroyedAt: 1_700_000_500_000 },
];

export const orgs = (): Doc[] => [
  { _id: new ObjectId(ORG), registeredName: 'One', defaultCodingEngine: 'claude-code', codingEngineAutoRoute: false },
  { _id: new ObjectId(ORG2), registeredName: 'Two' },
];

export const users = (): Doc[] => [
  { _id: USER, orgId: new ObjectId(ORG), email: 'a@x.test', codingEngine: 'codex' },
  { _id: USER2, orgId: new ObjectId(ORG), email: 'b@x.test' },
];

export const chatSessions = (): Doc[] => [
  {
    _id: SESSION,
    orgId: new ObjectId(ORG),
    title: 't',
    codingEngine: 'opencode',
    codingEnginePin: 'opencode@1.2',
    repository: { url: 'https://git.example/r.git', toolsetInstanceId: 'ti-1', baseRef: 'main' },
  },
  { _id: SESSION2, orgId: new ObjectId(ORG), title: 'u' },
];

const entry = (kind: string, journalSeq: number, sessionId: string | null): AuditEntry => ({
  kind,
  sessionId,
  streamId: sessionId ? `s:${sessionId}` : `cp:${ORG}`,
  runId: sessionId ? 'run-1' : null,
  journalSeq,
  writer: sessionId ? 'node' : 'control-plane',
  attemptId: null,
  fence: null,
  fields: { tool: 'search', ok: true, n: journalSeq },
  dataDigest: `sha256:${String(journalSeq).padStart(64, '0')}`,
} as AuditEntry);

/** A linked chain of three records (as the linker writes them: valid hashes and links), its head and an anchor at seq 2. */
export const auditChain = (): { records: Doc[]; heads: Doc[]; pending: Doc[]; anchors: Doc[] } => {
  const first = chainAuditRecords(ORG, null, [entry('run.started', 1, SESSION.toHexString()), entry('tool.called', 2, SESSION.toHexString())], '2026-11-01T10:00:00.000Z');
  const second = chainAuditRecords(ORG, first[1]!, [entry('policy.changed', 7, null)], '2026-11-01T10:05:00.000Z');
  const records = [...first, ...second];
  const last = records[2]!;
  return {
    records: records.map((r) => ({ _id: new ObjectId(), ...r })),
    heads: [{ _id: new ObjectId(), orgId: ORG, seq: last.seq, hash: last.hash, anchoredSeq: 2 }],
    pending: [
      {
        _id: new ObjectId('ffffffffffffffffffffff01'),
        orgId: ORG,
        streamId: `s:${SESSION.toHexString()}`,
        journalSeq: 3,
        ts: '2026-11-01T10:06:00.000Z',
        entries: [entry('run.ended', 3, SESSION.toHexString())],
        digests: ['sha256:pending'],
      },
    ],
    anchors: [{ _id: `${ORG}:2`, orgId: ORG, seq: 2, hash: records[1]!.hash, anchoredAt: '2026-11-01T10:15:00.000Z' }],
  };
};

/** Every carried collection with its documents. */
export const seed = (): Record<string, Doc[]> => ({
  ...(() => {
    const a = auditChain();
    return { agentAuditRecords: a.records, agentAuditHeads: a.heads, agentAuditPending: a.pending, agentAuditAnchors: a.anchors };
  })(),
  agentHarnessDataKeys: dataKeys(),
  agentWorkspaceHeads: workspaceHeads(),
  agentWorkspaceManifests: workspaceManifests(),
  agentWorkspaceKeys: workspaceKeys(),
  org: orgs(),
  users: users(),
  chatSessions: chatSessions(),
});
