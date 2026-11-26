/**
 * PERF-C (PERFB-F1) on a MongoDB replica set (run as
 * execution-store.mongo.itest.ts says): audit appends commit with their
 * journal events and are linked onto the org's chain afterwards. Whatever
 * the interleaving of commits, links, reads and processes that died between
 * a commit and its link, a reader gets one chain that verifies and holds
 * every committed entry.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import fc from 'fast-check';
import mongoose from 'mongoose';
import { AUDIT_LINK_STALE_MS, AuditEntry, AuditRecord, verifyAuditChain } from '../../../src/modules/agent-harness/domain/audit';
import { AuditLinkWatch } from '../../../src/modules/agent-harness/application/audit-link-watch';
import { AuditAnchorService, AuditVerifier } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  AUDIT_LINK_BATCH,
  AUDIT_PENDING,
  AUDIT_PENDING_ORDER_INDEX,
  linkAllPendingAudit,
  linkAuditAppends,
  pendingAuditAppend,
} from '../../../src/libs/repositories/agent-harness/audit-linking/mongo';
import { AUDIT_PENDING_MIGRATION } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  AgentAuditHeadModel,
  AgentAuditPendingModel,
  AgentAuditRecordModel,
} from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import {
  DURABLE_TRANSACTION_OPTIONS,
  inMongoTransaction,
} from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { AUDIT_LINK_DEFER_MS, auditLinkDefer, auditLinksIdle, MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { propertyRuns } from '../../support/harness/property-profile';
import { auditEntry } from './support/audit-chain-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, resetHarnessMongo } from './support/harness-mongo';
import { probeStore } from './support/store-race';

const ORG = 'org-1';
const db = () => mongoose.connection.db!;

type Line = [level: string, message: string, meta: Record<string, unknown>];

/** One append: a stream's next journal seqs, one or two entries, tagged so its records can be told apart. */
const batch = (streamId: string, firstSeq: number, size: number, tag: string): AuditEntry[] =>
  Array.from({ length: size }, (_, index) => auditEntry(firstSeq + index, { streamId, sessionId: streamId, runId: tag }));

const tsOf = (tick: number): string => new Date(Date.UTC(2026, 9, 12) + tick).toISOString();

const commit = (store: MongoAuditChainStore, entries: AuditEntry[], ts: string): Promise<void> =>
  inMongoTransaction((session) => store.append(ORG, entries, ts, session));

/** Commits as a process that dies before its link ran: the append is durable and nothing links it. */
const commitThenDie = async (store: MongoAuditChainStore, entries: AuditEntry[], ts: string): Promise<void> => {
  const session = await mongoose.startSession();
  try {
    await session.withTransaction(() => store.append(ORG, entries, ts, session), DURABLE_TRANSACTION_OPTIONS);
  } finally {
    session.removeAllListeners('ended');
    await session.endSession();
  }
};

/** A commit of the previous release: the head claimed and the records written inside the transaction. */
const commitAsPreviousRelease = (entries: AuditEntry[], ts: string): Promise<void> =>
  inMongoTransaction((session) => linkAuditAppends(db(), ORG, [{ entries, ts }], session));

const position = (record: Pick<AuditRecord, 'streamId' | 'journalSeq'>): string => `${record.streamId}#${String(record.journalSeq)}`;

/** The chain as a reader gets it, checked against everything committed so far. */
async function expectWholeChain(store: MongoAuditChainStore, committed: AuditEntry[][]): Promise<void> {
  const records = await store.read(ORG, 0, 10_000);
  const verification = verifyAuditChain(records, { head: await store.head(ORG) });
  expect(verification.problems, 'the chain verifies against its head').to.deep.equal([]);
  expect(records.map(position).sort(), 'every committed entry, once').to.deep.equal(committed.flat().map(position).sort());
  const lastOfStream = new Map<string, number>();
  for (const record of records) {
    expect(record.journalSeq, `${record.streamId} in journal order`).to.be.greaterThan(lastOfStream.get(record.streamId) ?? 0);
    lastOfStream.set(record.streamId, record.journalSeq);
  }
  for (const entries of committed) {
    const seqs = records.filter((record) => record.runId === entries[0]?.runId).map((record) => record.seq);
    expect(seqs, 'an append is contiguous').to.deep.equal(seqs.map((_, index) => (seqs[0] as number) + index));
  }
}

type Step =
  | { kind: 'commits'; streams: number[]; sizes: number[]; dies: boolean[] }
  | { kind: 'read' }
  | { kind: 'two-nodes-link' };

const steps = fc.array(
  fc.oneof(
    {
      arbitrary: fc.record({
        kind: fc.constant<'commits'>('commits'),
        streams: fc.uniqueArray(fc.integer({ min: 0, max: 3 }), { minLength: 1, maxLength: 4 }),
        sizes: fc.array(fc.integer({ min: 1, max: 2 }), { minLength: 4, maxLength: 4 }),
        dies: fc.array(fc.boolean(), { minLength: 4, maxLength: 4 }),
      }),
      weight: 4,
    },
    { arbitrary: fc.constant<Step>({ kind: 'read' }), weight: 2 },
    { arbitrary: fc.constant<Step>({ kind: 'two-nodes-link' }), weight: 1 },
  ),
  { minLength: 3, maxLength: 12 },
);

describe('PERF-C: the audit chain linked after the commit (MongoDB replica set)', function () {
  this.timeout(600_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('PERFC-24: any interleaving of commits, links, reads and processes dying after a commit leaves one whole chain', async () => {
    await fc.assert(
      fc.asyncProperty(steps, async (schedule) => {
        await resetHarnessMongo();
        const store = new MongoAuditChainStore();
        const committed: AuditEntry[][] = [];
        const nextSeq = [1, 1, 1, 1];
        let tick = 0;
        for (const step of schedule) {
          if (step.kind === 'read') {
            await expectWholeChain(store, committed);
          } else if (step.kind === 'two-nodes-link') {
            // A second Node links the same org at the same moment: its link meets this one's at the head.
            await Promise.all([store.settle(ORG), linkAllPendingAudit(db(), ORG)]);
          } else {
            tick += 1;
            await Promise.all(
              step.streams.map((stream, index) => {
                const size = step.sizes[index] as number;
                const entries = batch(`ses-${String(stream)}`, nextSeq[stream] as number, size, `append-${String(committed.length)}`);
                nextSeq[stream] = (nextSeq[stream] as number) + size;
                committed.push(entries);
                return (step.dies[index] === true ? commitThenDie : commit)(store, entries, tsOf(tick));
              }),
            );
          }
        }
        await expectWholeChain(store, committed);
        expect(await AgentAuditPendingModel.countDocuments({}), 'a read leaves nothing pending').to.equal(0);
      }),
      propertyRuns(25),
    );
  });

  describe('PERF-E3: the link waits for the org\'s next commits', () => {
    afterEach(() => {
      auditLinkDefer.ms = AUDIT_LINK_DEFER_MS;
    });

    it('PERFE3-2: commits of one moment wait together and are linked with no read and no flush', async () => {
      auditLinkDefer.ms = 300;
      const store = new MongoAuditChainStore();
      const committed = [batch('ses-a', 1, 1, 'first'), batch('ses-b', 1, 2, 'second'), batch('ses-a', 2, 1, 'third')];
      for (const entries of committed) await commit(store, entries, tsOf(1));
      expect(await AgentAuditPendingModel.countDocuments({}), 'waiting').to.equal(3);
      const until = Date.now() + 30_000;
      while ((await AgentAuditPendingModel.countDocuments({})) > 0 && Date.now() < until) {
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      expect(await AgentAuditPendingModel.countDocuments({}), 'linked on its own').to.equal(0);
      expect(await AgentAuditRecordModel.countDocuments({ orgId: ORG })).to.equal(4);
      await expectWholeChain(store, committed);
    });

    it('PERFE3-3: a read while the link waits links at once and gets every committed entry', async () => {
      auditLinkDefer.ms = 600_000;
      const store = new MongoAuditChainStore();
      const committed = [batch('ses-a', 1, 2, 'waiting')];
      await commit(store, committed[0] as AuditEntry[], tsOf(1));
      expect(await AgentAuditPendingModel.countDocuments({})).to.equal(1);
      await expectWholeChain(store, committed);
      expect(await AgentAuditPendingModel.countDocuments({})).to.equal(0);
    });

    it('PERFE3-4: a process that dies while its link waits leaves the appends committed; the link watch links them', async () => {
      auditLinkDefer.ms = 600_000;
      const store = new MongoAuditChainStore();
      await commit(store, batch('ses-a', 1, 1, 'waiting'), tsOf(1));
      await commit(store, batch('ses-b', 1, 1, 'waiting-too'), tsOf(1));
      // A second process's watch, which shares nothing with this one but the store.
      expect(await new AuditLinkWatch({ chain: new MongoAuditChainStore(), metrics: { unlinked: () => undefined } }).tick()).to.deep.equal([]);
      expect(await AgentAuditRecordModel.countDocuments({ orgId: ORG })).to.equal(2);
      expect(await AgentAuditPendingModel.countDocuments({})).to.equal(0);
    });
  });

  it('PERFC-26: an append whose process died before the link is linked by the next read', async () => {
    const store = new MongoAuditChainStore();
    await commitThenDie(store, batch('ses-a', 1, 2, 'died'), tsOf(1));
    await auditLinksIdle();
    expect(await AgentAuditPendingModel.countDocuments({})).to.equal(1);
    expect(await AgentAuditRecordModel.countDocuments({})).to.equal(0);
    expect((await store.read(ORG, 0, 10)).map((record) => record.seq)).to.deep.equal([1, 2]);
    expect(await AgentAuditPendingModel.countDocuments({})).to.equal(0);
  });

  it('PERFC-26: the anchor tick links first, so an anchor covers every entry committed before it', async () => {
    const store = new MongoAuditChainStore();
    await commit(store, batch('ses-a', 1, 1, 'linked'), tsOf(1));
    await auditLinksIdle();
    await commitThenDie(store, batch('ses-a', 2, 1, 'died'), tsOf(2));
    await commitThenDie(store, batch('ses-b', 1, 1, 'died-too'), tsOf(2));
    const sink = new MongoAuditAnchorSink();
    const anchoring = new AuditAnchorService({ chain: store, sink, lease: new MongoLeaderLease(), holderId: 'itest-anchorer' });
    expect(await anchoring.tick()).to.equal(1);
    expect((await sink.list(ORG)).map((anchor) => anchor.seq)).to.deep.equal([3]);
    expect((await new AuditVerifier(store, sink).verify(ORG)).ok).to.equal(true);
  });

  it('PERFC-27: a link that fails after the commit is logged and the commit stands; the next read links', async () => {
    const lines: Line[] = [];
    const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
    const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
    const store = new MongoAuditChainStore(logger);
    const remove = probeStore((op, phase) => {
      if (phase === 'before' && op.collection === AUDIT_PENDING && !op.inTransaction && op.method === 'findOne') {
        throw new Error('store away');
      }
    });
    try {
      await commit(store, batch('ses-a', 1, 1, 'kept'), tsOf(1));
      await auditLinksIdle();
    } finally {
      remove();
    }
    expect(lines).to.deep.equal([
      ['warn', 'audit.link_failed', { event: 'audit.link_failed', orgId: ORG, errorType: 'Error', choice: 'next_read_or_anchor' }],
    ]);
    expect(await AgentAuditRecordModel.countDocuments({})).to.equal(0);
    expect((await store.read(ORG, 0, 10)).map((record) => record.runId)).to.deep.equal(['kept']);
  });

  describe('an append that cannot be linked', () => {
    const recording = () => {
      const lines: Line[] = [];
      const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
        lines.push([level, message, meta]);
      };
      const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
      return { lines, logger };
    };
    const anchorer = (store: MongoAuditChainStore, logger: HarnessLogger, now: () => number) => {
      const sink = new MongoAuditAnchorSink();
      return {
        sink,
        anchoring: new AuditAnchorService({ chain: store, sink, lease: new MongoLeaderLease(), holderId: 'itest-anchorer', logger, now }),
      };
    };

    it('PERFC-37: an entry changed between its commit and its link is not linked; the verifier reports it unlinked, not broken', async () => {
      const { lines, logger } = recording();
      const store = new MongoAuditChainStore(logger);
      await commit(store, batch('ses-a', 1, 1, 'whole'), tsOf(1));
      await auditLinksIdle();
      await commitThenDie(store, batch('ses-a', 2, 1, 'changed-after-commit'), tsOf(2));
      await db()
        .collection(AUDIT_PENDING)
        .updateOne({ orgId: ORG }, { $set: { 'entries.0.fields.principalRef': 'p_someone-else' } });

      const verification = await new AuditVerifier(store, new MongoAuditAnchorSink()).verify(ORG);
      expect(verification).to.deep.include({ ok: true, records: 1, lastSeq: 1, unlinked: 1 });
      expect((await store.read(ORG, 0, 10)).map((record) => record.runId)).to.deep.equal(['whole']);
      expect(await store.unlinked(ORG)).to.deep.equal([{ orgId: ORG, appends: 1, oldestTs: tsOf(2) }]);
      expect(lines.filter(([, message]) => message === 'audit.link_failed')[0]).to.deep.equal([
        'error',
        'audit.link_failed',
        { event: 'audit.link_failed', orgId: ORG, errorType: 'AuditPendingMalformedError', choice: 'reported_unlinked' },
      ]);
    });

    it('PERFC-35: an append that holds no entries stops its org only; other orgs are linked and anchored', async () => {
      const { lines, logger } = recording();
      const store = new MongoAuditChainStore(logger);
      await db().collection(AUDIT_PENDING).insertOne({ orgId: ORG, streamId: 'ses-a', journalSeq: 1, ts: tsOf(1), entries: [], digests: [] });
      await inMongoTransaction((session) => store.append('org-2', batch('ses-b', 1, 1, 'other-org'), tsOf(1), session));
      await auditLinksIdle();
      const { sink, anchoring } = anchorer(store, logger, () => Date.parse(tsOf(2)));
      expect(await anchoring.tick()).to.equal(1);
      expect((await sink.list('org-2')).map((anchor) => anchor.seq)).to.deep.equal([1]);
      expect(await store.read(ORG, 0, 10)).to.deep.equal([]);
      expect((await store.unlinked()).map((unlinked) => unlinked.orgId)).to.deep.equal([ORG]);
      expect(lines.some(([level, message, meta]) => level === 'error' && message === 'audit.link_failed' && meta['orgId'] === ORG)).to.equal(true);
    });

    it('PERFC-38: the anchorer refuses an org whose entries stayed unlinked past the threshold, and says why; it anchors once they are linked', async () => {
      const { lines, logger } = recording();
      const store = new MongoAuditChainStore(logger);
      await commit(store, batch('ses-a', 1, 1, 'linked'), tsOf(0));
      await auditLinksIdle();
      const stuck = await db()
        .collection(AUDIT_PENDING)
        .insertOne({ orgId: ORG, streamId: 'ses-a', journalSeq: 2, ts: tsOf(1_000), entries: [], digests: [] });
      let now = Date.parse(tsOf(1_000)) + AUDIT_LINK_STALE_MS - 1;
      const { sink, anchoring } = anchorer(store, logger, () => now);
      expect(await anchoring.tick(), 'younger than the threshold: a link may simply be on its way').to.equal(1);
      // The head grows past its anchor while the entry stays stuck (here through a Node that links in its commit).
      await commitAsPreviousRelease(batch('ses-b', 1, 1, 'later'), tsOf(2_000));
      now += 1;
      expect(await anchoring.tick()).to.equal(0);
      expect((await sink.list(ORG)).map((anchor) => anchor.seq)).to.deep.equal([1]);
      expect(lines.filter(([, message]) => message === 'audit.anchor_refused')).to.deep.equal([
        [
          'error',
          'audit.anchor_refused',
          { event: 'audit.anchor_refused', orgId: ORG, seq: 2, reason: 'unlinked_entries', appends: 1, oldestAgeS: AUDIT_LINK_STALE_MS / 1000 },
        ],
      ]);
      await db().collection(AUDIT_PENDING).deleteOne({ _id: stuck.insertedId });
      expect(await anchoring.tick()).to.equal(1);
      expect((await sink.list(ORG)).map((anchor) => anchor.seq)).to.deep.equal([1, 2]);
    });
  });

  it('PERFC-39: the link watch links what a dead process left, with no read and no operator, and reports nothing unlinked', async () => {
    const store = new MongoAuditChainStore();
    await commitThenDie(store, batch('ses-a', 1, 2, 'died'), tsOf(1));
    await commitThenDie(store, batch('ses-b', 1, 1, 'died-too'), tsOf(1));
    const seen: Array<[number, number]> = [];
    const watch = new AuditLinkWatch({ chain: store, metrics: { unlinked: (appends, age) => seen.push([appends, age]) } });
    expect(await watch.tick()).to.deep.equal([]);
    expect(seen).to.deep.equal([[0, 0]]);
    expect(await AgentAuditPendingModel.countDocuments({})).to.equal(0);
    expect(await AgentAuditRecordModel.countDocuments({ orgId: ORG })).to.equal(3);
  });

  it('PERFC-40: more appends than one link takes, their times against their journal order: every stream is still linked in order', async () => {
    const store = new MongoAuditChainStore();
    const committed: AuditEntry[][] = [];
    const documents = [];
    for (let seq = 1; seq <= 90; seq += 1) {
      for (const stream of ['ses-a', 'ses-b', 'ses-c', 'ses-d', 'ses-e']) {
        const entries = batch(stream, seq, 1, `${stream}-${String(seq)}`) as [AuditEntry, ...AuditEntry[]];
        committed.push(entries);
        // Clocks running backwards: a later append of a stream carries an earlier time.
        documents.push(pendingAuditAppend(ORG, entries, tsOf(1_000 - seq)));
      }
    }
    expect(documents.length).to.be.greaterThan(2 * AUDIT_LINK_BATCH);
    await db().collection(AUDIT_PENDING).insertMany(documents);
    await expectWholeChain(store, committed);
  });

  it('PERFC-41: a read waits for what the primary held when it began (the link that just committed is in it)', async () => {
    const store = new MongoAuditChainStore();
    await commit(store, batch('ses-a', 1, 1, 'first'), tsOf(1));
    await auditLinksIdle();
    await db().command({ profile: 0 });
    await db().collection('system.profile').drop().catch(() => undefined);
    await db().command({ profile: 2 });
    try {
      await store.read(ORG, 0, 10);
      await store.head(ORG);
      await store.unanchored(10);
    } finally {
      await db().command({ profile: 0 });
    }
    const reads = await db()
      .collection('system.profile')
      .find({ op: 'query', ns: { $in: [AgentAuditRecordModel.collection.name, AgentAuditHeadModel.collection.name].map((name) => `${db().databaseName}.${name}`) } })
      .toArray();
    await db().collection('system.profile').drop().catch(() => undefined);
    expect(reads).to.have.length(3);
    for (const read of reads) {
      const concern = (read['command'] as { readConcern?: { level?: string; afterClusterTime?: unknown } }).readConcern;
      expect(concern?.level, String(read['ns'])).to.equal('majority');
      expect(concern?.afterClusterTime, `${String(read['ns'])} names the time it waits for`).to.not.equal(undefined);
    }
  });

  it('PERFC-28: beside a Node of the previous release, which links inside its commit: one chain, every entry once', async () => {
    const store = new MongoAuditChainStore();
    const committed: AuditEntry[][] = [];
    for (let round = 0; round < 4; round += 1) {
      const fromOld = batch('ses-old', 2 * round + 1, 2, `old-${String(round)}`);
      const fromNew = [0, 1, 2].map((stream) => batch(`ses-new-${String(stream)}`, round + 1, 1, `new-${String(round)}-${String(stream)}`));
      committed.push(fromOld, ...fromNew);
      await Promise.all([
        commitAsPreviousRelease(fromOld, tsOf(round)),
        ...fromNew.map((entries) => commit(store, entries, tsOf(round))),
      ]);
    }
    await expectWholeChain(store, committed);
    // What that release reads once nothing is pending: the collection itself, whole.
    const raw = await AgentAuditRecordModel.find({ orgId: ORG }).sort({ seq: 1 }).lean();
    const head = await AgentAuditHeadModel.findOne({ orgId: ORG }).lean();
    expect(verifyAuditChain(raw as unknown as AuditRecord[], { head }).ok).to.equal(true);
    expect(raw).to.have.length(committed.flat().length);
  });

  it('PERFC-29: an append with no transaction (standalone MongoDB) is linked at once', async () => {
    const store = new MongoAuditChainStore();
    await store.append(ORG, batch('cp:org-1', 1, 1, 'config'), tsOf(1), undefined as never);
    expect(await AgentAuditPendingModel.countDocuments({})).to.equal(0);
    expect(await AgentAuditRecordModel.countDocuments({ orgId: ORG })).to.equal(1);
    await store.append(ORG, batch('cp:org-1', 2, 1, 'config-2'), tsOf(2), undefined as never);
    await expectWholeChain(store, [batch('cp:org-1', 1, 1, 'config'), batch('cp:org-1', 2, 1, 'config-2')]);
  });

  it('PERFC-30: the migration goes down by linking what is pending, keeps every document, and goes up again', async () => {
    const store = new MongoAuditChainStore();
    const index = `${AUDIT_PENDING}.${AUDIT_PENDING_ORDER_INDEX}`;
    expect((await AUDIT_PENDING_MIGRATION.up(db())).created, 'built at startup').to.deep.equal([]);
    await commit(store, batch('ses-a', 1, 1, 'linked'), tsOf(1));
    await auditLinksIdle();
    await commitThenDie(store, batch('ses-a', 2, 2, 'pending-at-rollback'), tsOf(2));
    try {
      expect((await AUDIT_PENDING_MIGRATION.down(db())).dropped).to.deep.equal([index]);
      expect(await AgentAuditPendingModel.countDocuments({}), 'nothing is left outside the chain').to.equal(0);
      const raw = await AgentAuditRecordModel.find({ orgId: ORG }).sort({ seq: 1 }).lean();
      expect(raw.map((record) => record.runId)).to.deep.equal(['linked', 'pending-at-rollback', 'pending-at-rollback']);
      expect(
        verifyAuditChain(raw as unknown as AuditRecord[], { head: await AgentAuditHeadModel.findOne({ orgId: ORG }).lean() }).ok,
      ).to.equal(true);
      expect((await AUDIT_PENDING_MIGRATION.down(db())).dropped, 'down again finds nothing').to.deep.equal([]);
      // Without the index (a Node of this release before its migration ran) appends still link.
      await commit(store, batch('ses-a', 4, 1, 'no-index'), tsOf(3));
      expect((await store.read(ORG, 0, 10)).map((record) => record.seq)).to.deep.equal([1, 2, 3, 4]);
    } finally {
      expect((await AUDIT_PENDING_MIGRATION.up(db())).created).to.deep.equal([index]);
    }
  });
});
