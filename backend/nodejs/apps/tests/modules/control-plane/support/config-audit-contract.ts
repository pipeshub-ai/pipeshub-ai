/**
 * CA: what every ConfigAuditLog does (03-interfaces §5.3), over the
 * in-memory chain (`config-audit.test.ts`) and MongoAuditChainStore
 * (`transaction.mongo.itest.ts`).
 */
import { expect } from 'chai';
import {
  ConfigChange,
  ControlPlaneTransaction,
} from '../../../../src/modules/control-plane';
import { canonicalDigest } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import {
  AuditEntry,
  AuditRecord,
  auditRecordHash,
  verifyAuditChain,
} from '../../../../src/modules/agent-harness/domain/audit';

export interface ConfigAuditWorld {
  transaction: ControlPlaneTransaction;
  /** Every record of the org's chain, run and config, in chain order. */
  chain(orgId: string): Promise<AuditRecord[]>;
  /** Appends run-journal entries the way the journal does (in its own transaction). */
  appendRun(orgId: string, entries: AuditEntry[]): Promise<void>;
}

const ORG = 'ca0000000000000000000001';

const runEntry = (seq: number): AuditEntry => ({
  kind: 'run.completed',
  sessionId: 'session-1',
  streamId: 'session-1',
  runId: 'run-1',
  journalSeq: seq,
  writer: 'node',
  attemptId: null,
  fence: null,
  fields: { outcome: 'success' },
  dataDigest: canonicalDigest({ outcome: 'success', seq }),
});

const change = (over: Partial<ConfigChange> = {}): ConfigChange => ({
  scope: `org:${ORG}`,
  object: { type: 'policy-section', id: 'approvals' },
  revisionFrom: 3,
  revisionTo: 4,
  changeDigest: canonicalDigest({ approvals: 'after' }),
  changed: ['orgs.org-ca-1.org', 'orgs.org-ca-1.agents'],
  ...over,
});

const ALLOWED_FIELDS = new Set([
  'scope',
  'objectType',
  'objectId',
  'revisionFrom',
  'revisionTo',
  'changeDigest',
  'actor',
  'approvalRef',
  'headRevision',
  'changed',
  'repaired',
]);

export function configAuditContract(name: string, make: () => Promise<ConfigAuditWorld>): void {
  describe(`ConfigAuditLog contract (CA): ${name}`, function () {
    this.timeout(60_000);
    let world: ConfigAuditWorld;
    beforeEach(async () => {
      world = await make();
    });

    const configRecords = async (): Promise<AuditRecord[]> =>
      (await world.chain(ORG)).filter((record) => record.kind === 'config.changed');

    it('CA-1: the entry carries ids, revisions, digests and paths on the cp stream, nothing else', async () => {
      const committed = await world.transaction.run(
        { orgId: ORG, actor: 'user:u-admin', approvalRef: 'apr-7' },
        async () => ({ result: null, change: change() }),
      );
      const [record] = await configRecords();
      expect(record).to.include({
        streamId: `cp:${ORG}`,
        writer: 'control-plane',
        sessionId: null,
        runId: null,
        attemptId: null,
        fence: null,
        journalSeq: committed.headRevision,
      });
      expect(record!.fields).to.deep.equal({
        scope: `org:${ORG}`,
        objectType: 'policy-section',
        objectId: 'approvals',
        revisionFrom: 3,
        revisionTo: 4,
        changeDigest: change().changeDigest,
        actor: 'user:u-admin',
        approvalRef: 'apr-7',
        headRevision: committed.headRevision,
        changed: 'orgs.org-ca-1.org,orgs.org-ca-1.agents',
      });
      for (const key of Object.keys(record!.fields)) expect(ALLOWED_FIELDS.has(key), key).to.equal(true);
    });

    it('CA-2: a write rolled back after its entry was staged leaves no entry', async () => {
      await world.transaction
        .run({ orgId: ORG, actor: 'user:u-admin' }, async (tx) => {
          tx.external(() => Promise.reject(new Error('store down')));
          return { result: null, change: change() };
        })
        .catch(() => undefined);
      expect(await configRecords()).to.deep.equal([]);
    });

    it('CA-3: one chain verifies over run and config entries interleaved; an edited config entry is tampered', async () => {
      await world.appendRun(ORG, [runEntry(1)]);
      await world.transaction.run({ orgId: ORG, actor: 'user:u-admin' }, async () => ({
        result: null,
        change: change(),
      }));
      await world.appendRun(ORG, [runEntry(2)]);
      const records = await world.chain(ORG);
      expect(records.map((record) => record.kind)).to.deep.equal([
        'run.completed',
        'config.changed',
        'run.completed',
      ]);
      expect(verifyAuditChain(records).ok).to.equal(true);
      const edited = records.map((record) =>
        record.kind === 'config.changed'
          ? { ...record, fields: { ...record.fields, objectId: 'engineProfiles' } }
          : record,
      );
      expect(verifyAuditChain(edited).problems).to.deep.include({ kind: 'tampered', seq: 2 });
      const { hash, ...unhashed } = records[1]!;
      expect(auditRecordHash(unhashed)).to.equal(hash);
    });

    it('CA-5: changeDigest is the given digest; dataDigest is the digest of the committed change', async () => {
      const committed = await world.transaction.run({ orgId: ORG, actor: 'user:u-admin' }, async () => ({
        result: null,
        change: change({ revisionFrom: null, revisionTo: null }),
      }));
      const [record] = await configRecords();
      expect(record!.fields['changeDigest']).to.equal(change().changeDigest);
      expect(record!.fields).to.not.have.property('revisionFrom');
      expect(record!.dataDigest).to.equal(
        canonicalDigest({
          ...change({ revisionFrom: null, revisionTo: null }),
          orgId: ORG,
          actor: 'user:u-admin',
          approvalRef: null,
          headRevision: committed.headRevision,
        }),
      );
    });
  });
}
