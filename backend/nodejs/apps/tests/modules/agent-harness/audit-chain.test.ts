/**
 * O-2: the per-org audit chain, its verifier and its anchors (REPORT 6.16).
 * The replica-set half is `audit-chain.mongo.itest.ts`; what every store and
 * anchor sink shares is support/audit-chain-store-contract.ts and
 * support/audit-anchor-sink-contract.ts.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  InMemoryAuditAnchorSink,
  InMemoryAuditChainStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  AuditAnchorService,
  AuditVerifier,
} from '../../../src/modules/agent-harness/application/audit-anchoring';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import {
  AUDIT_ANCHOR_INTERVAL_MS,
  AUDIT_ANCHOR_LEASE_MS,
  AUDIT_ANCHOR_LEASE_NAME,
  AUDIT_EVENT_FIELDS,
  AUDIT_GENESIS_HASH,
  AuditRecord,
  auditProjection,
  verifyAuditChain,
} from '../../../src/modules/agent-harness/domain/audit';
import {
  JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
  JournalEventInput,
} from '../../../src/modules/agent-harness/domain/journal';
import { JOURNAL_EVENT_WRITERS } from '../../../src/modules/agent-harness/contracts/event-catalog';
import { testCipher } from './support/data-protection';
import { position, turnStarted, workerEvent } from './support/journal-fixtures';
import { uncheckedJournalHistory } from '../../support/harness/journal-history';

const SENTINEL = 'SENTINEL-audit-91be-customer-text';

const recording = (): { logger: HarnessLogger; lines: unknown[][] } => {
  const lines: unknown[][] = [];
  const at =
    (level: string) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
  return {
    lines,
    logger: {
      debug: at('debug'),
      info: at('info'),
      warn: at('warn'),
      error: at('error'),
    },
  };
};

const nodeEvents: JournalEventInput[] = [
  workerEvent('run.admitted', { runKind: 'interactive', principalRef: 'p_user-1', via: 'service' }),
  workerEvent('run.claimed', { attempt: 1, fence: 1, leaseMs: 60_000 }),
  workerEvent('run.cancel_requested', {
    by: 'p_user-1',
    reason: `please stop ${SENTINEL}`,
  }),
  workerEvent('interaction.resolved', {
    interactionId: 'int-1',
    decision: 'answered',
    by: 'p_user-1',
    channel: 'web',
    answerRef: {
      ref: `inline:${SENTINEL}`,
      hash: `sha256:${'a'.repeat(64)}`,
      size: 3,
    },
  }),
  workerEvent('run.failed', {
    errorType: 'tool_error',
    message: `boom ${SENTINEL}`,
  }),
];

describe('O-2 audit chain', () => {
  // The chain is tested over hand-made envelopes (a run.admitted under a fence, events after an end).
  beforeEach(() => uncheckedJournalHistory('hand-made envelopes for the audit chain'));
  const setup = () => {
    const audit = new InMemoryAuditChainStore();
    const raw = new InMemoryJournalStore();
    const journal = new AuditedJournalStore(
      new SealedJournalStore(raw, testCipher()),
      audit,
    );
    const service = new JournalService(
      new InMemoryPayloadStore(),
      testCipher(),
    );
    const append = async (
      events: JournalEventInput[],
      writer: 'node' | 'worker' = 'node',
      orgId = 'org-1',
    ) =>
      journal.append(
        await service.prepare({ ...position({ orgId }), writer, events }),
        undefined,
      );
    return { audit, raw, journal, service, append };
  };

  describe('O-2a chaining', () => {
    it('chains only audit-relevant events, per org, linked from genesis', async () => {
      const h = setup();
      await h.append([nodeEvents[0]!, nodeEvents[1]!]);
      await h.append([turnStarted(1)], 'worker');
      await h.append([nodeEvents[2]!]);
      await h.append([nodeEvents[0]!], 'node', 'org-2');
      const records = await h.audit.read('org-1', 0, 100);
      expect(
        records.map((record) => [record.seq, record.kind, record.journalSeq]),
      ).to.deep.equal([
        [1, 'run.admitted', 1],
        [2, 'run.claimed', 2],
        [3, 'run.cancel_requested', 4],
      ]);
      expect(records[0]?.prevHash).to.equal(AUDIT_GENESIS_HASH);
      expect(records[1]?.prevHash).to.equal(records[0]?.hash);
      expect(records[2]?.prevHash).to.equal(records[1]?.hash);
      expect(records[0]).to.include({
        orgId: 'org-1',
        sessionId: 'session-1',
        streamId: 'session-1',
        runId: 'run-1',
        attemptId: 'attempt-1',
        fence: 1,
        writer: 'node',
      });
      // VA-4: how the principal reached the run is audited with it.
      expect(records[0]?.fields).to.deep.equal({
        runKind: 'interactive',
        principalRef: 'p_user-1',
        via: 'service',
      });
      expect(records[1]?.fields).to.deep.equal({
        attempt: 1,
        fence: 1,
        leaseMs: 60_000,
      });
      const other = await h.audit.read('org-2', 0, 100);
      expect(other.map((record) => record.seq)).to.deep.equal([1]);
      expect(other[0]?.prevHash).to.equal(AUDIT_GENESIS_HASH);
      expect(
        verifyAuditChain(records, { head: await h.audit.head('org-1') }).ok,
      ).to.equal(true);
    });

    it('audits a claim-checked event from the metadata taken before its data left', async () => {
      const h = setup();
      const bulky = workerEvent('run.failed', {
        errorType: 'tool_error',
        message: 'x'.repeat(JOURNAL_INLINE_PAYLOAD_MAX_BYTES),
      });
      await h.append([bulky]);
      const [record] = await h.audit.read('org-1', 0, 10);
      expect(record?.fields).to.deep.equal({ errorType: 'tool_error' });
      const [stored] = await h.raw.readStream('org-1', 'session-1', 0, 1);
      expect(stored?.payloadRef).to.not.equal(undefined);
      expect(JSON.stringify(stored)).not.to.contain('"audit"');
    });

    it('covers node-written authority events and only catalog types', () => {
      for (const type of Object.keys(AUDIT_EVENT_FIELDS)) {
        expect(JOURNAL_EVENT_WRITERS[type], type).to.not.equal(undefined);
      }
      for (const type of [
        'run.admitted',
        'run.claimed',
        'run.spec_pinned',
        'run.cancel_requested',
        'interaction.resolved',
        'label.declassified',
        'tool.denied',
      ]) {
        expect(AUDIT_EVENT_FIELDS[type], type).to.not.equal(undefined);
      }
    });
    it('AU-2 (CPD-1): a conversation-scope approval chains the decision and the grant, and verifies', async () => {
      const h = setup();
      await h.append([
        workerEvent('interaction.resolved', {
          interactionId: 'int-9',
          decision: 'approved',
          by: 'p_user-1',
          channel: 'web',
        }),
        { ...workerEvent('interaction.granted', { interactionId: 'int-9', scope: 'conversation' }), critical: false },
      ]);
      const records = await h.audit.read('org-1', 0, 10);
      expect(records.map((record) => [record.kind, record.fields])).to.deep.equal([
        ['interaction.resolved', { interactionId: 'int-9', decision: 'approved', by: 'p_user-1', channel: 'web' }],
        ['interaction.granted', { interactionId: 'int-9', scope: 'conversation' }],
      ]);
      expect(verifyAuditChain(records).ok).to.equal(true);
    });

    it('OB-03: every tool call is chained by its intent and outcome (a digest of its arguments, never them), and every published artifact by its ids', async () => {
      const h = setup();
      const argsDigest = `sha256:${'a'.repeat(64)}`;
      await h.append([
        workerEvent('tool.intent', {
          invocationId: 't1.0',
          toolId: 'crm@inst-1/create',
          argsDigest,
          effect: 'write',
          sink: { egress: 'external', argsLeaveBoundary: true },
        }),
        workerEvent('tool.outcome', { invocationId: 't1.0', status: 'ok', labels: { integrity: 'internal' } }),
        workerEvent('x.pipeshub.artifact.published', { intentId: 'pub-1', artifactId: 'art-1', publishedVersion: 2 }),
      ], 'worker');
      const records = await h.audit.read('org-1', 0, 10);
      expect(records.map((record) => [record.kind, record.fields])).to.deep.equal([
        ['tool.intent', { invocationId: 't1.0', toolId: 'crm@inst-1/create', effect: 'write', argsDigest }],
        ['tool.outcome', { invocationId: 't1.0', status: 'ok' }],
        ['x.pipeshub.artifact.published', { intentId: 'pub-1', artifactId: 'art-1', publishedVersion: 2 }],
      ]);
      expect(verifyAuditChain(records).ok).to.equal(true);
    });

    it('asks for audit only on the catalog types, never content-bearing ones', () => {
      expect(auditProjection('run.admitted', {})).to.not.equal(null);
      expect(auditProjection('interaction.resolved', {})).to.not.equal(null);
      expect(auditProjection('turn.started', { text: 'content' })).to.equal(null);
      expect(auditProjection('model.response', { text: 'content' })).to.equal(null);
      expect(auditProjection('constructor', { text: 'content' })).to.equal(null);
    });

    it('reads straight through to the journal and audits nothing on a read', async () => {
      const h = setup();
      await h.append([nodeEvents[0]!, nodeEvents[1]!]);
      await h.append([turnStarted(1)], 'worker');
      const before = await h.audit.read('org-1', 0, 100);
      expect(await h.journal.lastSeq('org-1', ['session-1', 'other'])).to.deep.equal(
        await h.raw.lastSeq('org-1', ['session-1', 'other']),
      );
      const stream = await h.journal.readStream('org-1', 'session-1', 1, 10);
      expect(stream.map((event) => [event.seq, event.type])).to.deep.equal([
        [2, 'run.claimed'],
        [3, 'turn.started'],
      ]);
      const run = await h.journal.readRun('org-1', 'run-1', 0, 2);
      expect(run.map((event) => event.seq)).to.deep.equal([1, 2]);
      expect(await h.audit.read('org-1', 0, 100)).to.deep.equal(before);
    });
  });

  describe('O-2b audit/content split', () => {
    it('keeps content out of every audit record', async () => {
      const h = setup();
      await h.append(nodeEvents);
      await h.append(
        [
          workerEvent('label.declassified', {
            items: ['inv-1', 'inv-2'],
            by: 'interaction',
            interactionId: 'int-1',
            reason: `because ${SENTINEL}`,
          }),
        ],
        'worker',
      );
      const records = await h.audit.read('org-1', 0, 100);
      expect(records).to.have.length(nodeEvents.length + 1);
      expect(JSON.stringify(records)).not.to.contain(SENTINEL);
      expect(records.at(-1)?.fields).to.deep.equal({
        by: 'interaction',
        interactionId: 'int-1',
        items: 2,
      });
      for (const record of records) {
        for (const value of Object.values(record.fields)) {
          expect(['string', 'number', 'boolean']).to.include(typeof value);
        }
        expect(record.dataDigest).to.match(/^sha256:[0-9a-f]{64}$/);
      }
    });
  });

  describe('O-2 verifier', () => {
    const chain = async (): Promise<{
      records: AuditRecord[];
      head: { seq: number; hash: string };
    }> => {
      const h = setup();
      for (let index = 0; index < 5; index += 1)
        await h.append([nodeEvents[1]!]);
      const head = (await h.audit.head('org-1'))!;
      return { records: await h.audit.read('org-1', 0, 100), head };
    };
    const kinds = (verification: ReturnType<typeof verifyAuditChain>) =>
      verification.problems.map((problem) => problem.kind);

    it('accepts an intact chain', async () => {
      const { records, head } = await chain();
      expect(verifyAuditChain(records, { head })).to.deep.include({
        ok: true,
        records: 5,
        lastSeq: 5,
      });
    });

    it('detects a changed field (tampering)', async () => {
      const { records, head } = await chain();
      records[2] = {
        ...records[2]!,
        fields: { ...records[2]!.fields, fence: 99 },
      };
      expect(kinds(verifyAuditChain(records, { head }))).to.deep.equal([
        'tampered',
      ]);
    });

    it('detects a changed field whose hash was recomputed (the next link breaks)', async () => {
      const { records, head } = await chain();
      const { hash: _hash, ...unhashed } = {
        ...records[2]!,
        fields: { fence: 99 },
      };
      _hash;
      const { auditRecordHash } = await import(
        '../../../src/modules/agent-harness/domain/audit'
      );
      records[2] = { ...unhashed, hash: auditRecordHash(unhashed) };
      expect(kinds(verifyAuditChain(records, { head }))).to.deep.equal([
        'broken_link',
      ]);
    });

    it('detects a deleted record in the middle and at the tail', async () => {
      const { records, head } = await chain();
      const middle = records.filter((record) => record.seq !== 3);
      expect(kinds(verifyAuditChain(middle, { head }))).to.deep.equal([
        'gap',
        'broken_link',
      ]);
      const tail = records.slice(0, 4);
      expect(kinds(verifyAuditChain(tail, { head }))).to.deep.equal([
        'head_mismatch',
      ]);
    });

    it('detects reordered records', async () => {
      const { records, head } = await chain();
      const swapped = [
        records[0]!,
        records[2]!,
        records[1]!,
        records[3]!,
        records[4]!,
      ];
      const found = kinds(verifyAuditChain(swapped, { head }));
      expect(found).to.include('reordered');
      expect(found).to.include('broken_link');
    });

    it('catches a consistently rewritten tail (and head) only through an anchor', async () => {
      const { records } = await chain();
      const anchor = {
        orgId: 'org-1',
        seq: 4,
        hash: records[3]!.hash,
        anchoredAt: 'x',
      };
      const { chainAuditRecords } = await import(
        '../../../src/modules/agent-harness/domain/audit'
      );
      const forged = [
        ...records.slice(0, 3),
        ...chainAuditRecords(
          'org-1',
          { seq: 3, hash: records[2]!.hash },
          records
            .slice(3)
            .map((record) => ({ ...record, fields: { forged: true } })),
          records[3]!.ts,
        ),
      ];
      const forgedHead = { seq: 5, hash: forged[4]!.hash };
      expect(verifyAuditChain(forged, { head: forgedHead }).ok).to.equal(true);
      expect(
        kinds(
          verifyAuditChain(forged, { head: forgedHead, anchors: [anchor] }),
        ),
      ).to.deep.equal(['anchor_mismatch']);
      expect(
        kinds(verifyAuditChain(records.slice(0, 3), { anchors: [anchor] })),
      ).to.deep.equal(['anchor_missing']);
    });
  });

  describe('O-2c anchors', () => {
    const lease = (leader = true) => ({
      tryAcquire: () => Promise.resolve(leader),
      release: () => Promise.resolve(),
    });

    it('anchors each grown head once, only on the leader, and logs it', async () => {
      const h = setup();
      const sink = new InMemoryAuditAnchorSink();
      const { logger, lines } = recording();
      const anchoring = new AuditAnchorService({
        chain: h.audit,
        sink,
        lease: lease(),
        holderId: 'a',
        logger,
        now: () => 0,
      });
      await h.append([nodeEvents[0]!, nodeEvents[1]!]);
      await h.append([nodeEvents[0]!], 'node', 'org-2');
      expect(
        await new AuditAnchorService({ chain: h.audit, sink, lease: lease(false), holderId: 'b' }).tick(),
      ).to.equal(0);
      expect(await anchoring.tick()).to.equal(2);
      expect(await anchoring.tick()).to.equal(0);
      await h.append([nodeEvents[2]!]);
      expect(await anchoring.tick()).to.equal(1);
      expect(
        (await sink.list('org-1')).map((anchor) => anchor.seq),
      ).to.deep.equal([2, 3]);
      const verifier = new AuditVerifier(h.audit, sink, logger);
      expect((await verifier.verify('org-1')).ok).to.equal(true);
      expect(
        lines.filter(([, message]) => message === 'audit.anchored'),
      ).to.have.length(3);
      expect(lines.at(-1)?.[1]).to.equal('audit.verified');
    });

    it('keeps a head unanchored when the sink fails, and retries it next tick', async () => {
      const h = setup();
      const sink = new InMemoryAuditAnchorSink();
      let fail = true;
      const flaky = {
        name: 'flaky',
        write: (anchor: Parameters<typeof sink.write>[0]) =>
          fail ? Promise.reject(new Error('worm down')) : sink.write(anchor),
        list: (orgId: string) => sink.list(orgId),
      };
      const { logger, lines } = recording();
      const anchoring = new AuditAnchorService({
        chain: h.audit,
        sink: flaky,
        lease: lease(),
        holderId: 'a',
        logger,
      });
      await h.append([nodeEvents[0]!]);
      expect(await anchoring.tick()).to.equal(0);
      expect(lines.map(([, message]) => message)).to.deep.equal([
        'audit.anchor_failed',
      ]);
      fail = false;
      expect(await anchoring.tick()).to.equal(1);
    });

    it('a started service anchors on its interval; stop ends it and gives up the lease', async () => {
      const h = setup();
      const sink = new InMemoryAuditAnchorSink();
      const released: string[] = [];
      const anchoring = new AuditAnchorService({
        chain: h.audit,
        sink,
        lease: {
        tryAcquire: () => Promise.resolve(true),
        release: (_name: string, holder: string) => {
          released.push(holder);
          return Promise.resolve();
        },
      },
        holderId: 'replica-a',
      });
      await h.append([nodeEvents[0]!]);
      anchoring.start(5);
      anchoring.start(5);
      for (let i = 0; i < 100 && (await sink.list('org-1')).length === 0; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      await anchoring.stop();
      expect((await sink.list('org-1')).map((anchor) => anchor.seq)).to.deep.equal([1]);
      expect(released).to.deep.equal(['replica-a']);
      await h.append([nodeEvents[1]!]);
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(await sink.list('org-1')).to.have.length(1);
    });

    describe('sealing after a restart', () => {
      /** A lease on the fake clock with the store's takeover rule: ours, absent, or lapsed. */
      class ClockLease {
        held: { holder: string; expiresAt: number } | null = null;
        tryAcquire(_name: string, holder: string, ttlMs: number): Promise<boolean> {
          const now = Date.now();
          if (this.held === null || this.held.holder === holder || this.held.expiresAt <= now) {
            this.held = { holder, expiresAt: now + ttlMs };
          }
          return Promise.resolve(this.held.holder === holder);
        }
        release(_name: string, holder: string): Promise<void> {
          if (this.held?.holder === holder) this.held = null;
          return Promise.resolve();
        }
      }
      const MINUTE = 60_000;
      let clock: sinon.SinonFakeTimers;
      beforeEach(() => {
        clock = sinon.useFakeTimers({ now: Date.UTC(2026, 9, 20, 13, 0), toFake: ['setInterval', 'clearInterval', 'setTimeout', 'Date'] });
      });
      afterEach(() => clock.restore());

      const anchoredSeqs = async (sink: InMemoryAuditAnchorSink) =>
        (await sink.list('org-1')).map((anchor) => anchor.seq);

      it('a replaced process that never released its lease delays the new one by at most a few minutes', async () => {
        const h = setup();
        const sink = new InMemoryAuditAnchorSink();
        const lease = new ClockLease();
        // The replaced process renewed just before it was killed and never ran stop().
        await clock.tickAsync(14 * MINUTE);
        await lease.tryAcquire(AUDIT_ANCHOR_LEASE_NAME, 'old-process', AUDIT_ANCHOR_LEASE_MS);
        await clock.tickAsync(1 * MINUTE);
        const fresh = new AuditAnchorService({ chain: h.audit, sink, lease, holderId: 'new-process' });
        fresh.start();
        await clock.tickAsync(7 * MINUTE);
        await h.append([nodeEvents[0]!]);
        await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS);
        expect(await anchoredSeqs(sink)).to.deep.equal([1]);
        await fresh.stop();
      });

      it('a started process anchors records made soon after it started within one interval', async () => {
        const h = setup();
        const sink = new InMemoryAuditAnchorSink();
        const service = new AuditAnchorService({ chain: h.audit, sink, lease: new ClockLease(), holderId: 'only' });
        service.start();
        await clock.tickAsync(2 * MINUTE);
        await h.append([nodeEvents[0]!]);
        await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS);
        expect(await anchoredSeqs(sink)).to.deep.equal([1]);
        await service.stop();
      });

      it('with two replicas only the lease holder anchors, and the other takes over when it dies', async () => {
        const h = setup();
        const sink = new InMemoryAuditAnchorSink();
        const lease = new ClockLease();
        const writes: string[] = [];
        const sinkOf = (holder: string) => ({
          name: 'memory',
          write: (anchor: Parameters<typeof sink.write>[0]) => {
            writes.push(holder);
            return sink.write(anchor);
          },
          list: (orgId: string) => sink.list(orgId),
        });
        const a = new AuditAnchorService({ chain: h.audit, sink: sinkOf('a'), lease, holderId: 'a' });
        const b = new AuditAnchorService({ chain: h.audit, sink: sinkOf('b'), lease, holderId: 'b' });
        a.start();
        await clock.tickAsync(1);
        b.start();
        await h.append([nodeEvents[0]!]);
        await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS + MINUTE);
        expect(writes).to.deep.equal(['a']);
        // a dies without releasing: its timer stops, the lease is left to lapse.
        clock.clearInterval((a as unknown as { timer: NodeJS.Timeout }).timer);
        await h.append([nodeEvents[1]!]);
        await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS);
        expect(writes).to.deep.equal(['a', 'b']);
        expect(await anchoredSeqs(sink)).to.deep.equal([1, 2]);
        await b.stop();
      });
    });

    it('a lease that cannot be released on stop is logged, not thrown', async () => {
      const { logger, lines } = recording();
      const anchoring = new AuditAnchorService({
        chain: new InMemoryAuditChainStore(),
        sink: new InMemoryAuditAnchorSink(),
        lease: {
          tryAcquire: () => Promise.resolve(false),
          release: () => Promise.reject(new TypeError('mongo down')),
        },
        holderId: 'replica-a',
        logger,
      });
      await anchoring.stop();
      expect(lines).to.have.length(1);
      expect(lines[0]?.[1]).to.equal('audit.anchor_lease_release_failed');
      expect(lines[0]?.[2]).to.deep.include({ errorType: 'TypeError' });
    });

    it('the anchorer elects one leader under a fixed lease name', () => {
      expect(AUDIT_ANCHOR_LEASE_NAME).to.equal('agent-harness-audit-anchor');
    });

    it('the verifier logs a failure with problem codes only', async () => {
      const h = setup();
      await h.append(nodeEvents);
      const records = h.audit.records.get('org-1')!;
      records.splice(1, 1);
      const { logger, lines } = recording();
      const result = await new AuditVerifier(
        h.audit,
        new InMemoryAuditAnchorSink(),
        logger,
      ).verify('org-1');
      expect(result.ok).to.equal(false);
      expect(lines).to.have.length(1);
      expect(lines[0]?.[0]).to.equal('error');
      expect((lines[0]?.[2] as { problems: string[] }).problems).to.deep.equal([
        'gap@2',
        'broken_link@3',
      ]);
      expect(JSON.stringify(lines)).not.to.contain(SENTINEL);
    });
  });
});
