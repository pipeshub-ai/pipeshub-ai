/**
 * E7 + T5 org retention and GC, in memory: the no-expiry default, the
 * retention pass over the periods a source gives (RTN-2), the sweeper's GC parts and
 * their windows (GC-1, GC-3..GC-5 orchestration, RT-6). The Mongo side is in
 * session-erasure.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import { InMemoryConversations } from '../../../src/modules/agent-harness/adapters/in-memory-conversations';
import {
  DAY_MS,
  DISCARDED_DRAFT_GRACE_MS,
  PAYLOAD_GC_GRACE_MS,
  RECEIPT_TTL_MS,
  RETENTION_SWEEP_LEASE_NAME,
} from '../../../src/modules/agent-harness/domain/session-retention';
import { RunStatus } from '../../../src/modules/agent-harness/domain/execution';
import { NO_RETENTION } from '../../../src/modules/agent-harness/ports/session-retention';

/** The orgs' periods as a source an edition binds would answer them; `set` changes one. */
const settingsOn = () => {
  const periods = new Map<string, number>();
  return {
    set: (orgId: string, days: number) => void periods.set(orgId, days),
    all: async () => [...periods].map(([orgId, days]) => ({ orgId, days })),
  };
};

describe('E7 org retention default', () => {
  it('no org sets a period: conversations are kept until their owner deletes them', async () => {
    expect(await NO_RETENTION.all()).to.deep.equal([]);
  });

  it('is configuration, never an environment variable (RT-6)', () => {
    const sources = ['domain/session-retention.ts', 'application/retention-sweeper.ts', 'ports/session-retention.ts'].map(
      (file) => readFileSync(join(__dirname, '../../../src/modules/agent-harness', file), 'utf8'),
    );
    for (const source of sources) expect(source).to.not.include('process.env');
  });
});

interface Calls {
  payloads: number[];
  receipts: number[];
  admissions: number[];
  drafts: Array<[string, number]>;
}

function sweeperOn(options: {
  periods?: Record<string, number>;
  idle?: Record<string, string[]>;
  runs?: Record<string, RunStatus[]>;
  conversations?: InMemoryConversations;
  failPayloads?: boolean;
  leader?: boolean;
  release?: (name: string, holder: string) => Promise<void>;
}) {
  const now = 100 * DAY_MS;
  const calls: Calls = { payloads: [], receipts: [], admissions: [], drafts: [] };
  const expired: string[] = [];
  const jobs = new InMemoryErasureJobStore(() => now);
  const settings = settingsOn();
  const sweeper = new RetentionSweeper<unknown>({
    store: {
      now: () => Promise.resolve(now),
      collectPayloads: (before) => {
        calls.payloads.push(before);
        return options.failPayloads ? Promise.reject(new Error('down')) : Promise.resolve(3);
      },
      expireCommandReceipts: (before) => {
        calls.receipts.push(before);
        return Promise.resolve(2);
      },
      expireAdmissionReceipts: (before) => {
        calls.admissions.push(before);
        return Promise.resolve(1);
      },
      harnessOrgs: () => Promise.resolve(['org-1', 'org-2']),
    },
    settings,
    conversations: {
      idleSince: (orgId, before) => {
        expect(before).to.equal(now - (options.periods?.[orgId] ?? 0) * DAY_MS);
        return Promise.resolve(options.idle?.[orgId] ?? []);
      },
      expire: (orgId, sessionId) => {
        expired.push(`${orgId}:${sessionId}`);
        return Promise.resolve(true);
      },
      awaitingErasure: (after, limit) =>
        (options.conversations ?? new InMemoryConversations()).awaitingErasure(after, limit),
    },
    erasures: jobs,
    runs: {
      runsOf: (_orgId, sessionId) =>
        Promise.resolve(
          (options.runs?.[sessionId] ?? []).map((status, index) => ({
            runId: `${sessionId}-r${String(index)}`,
            status,
            revision: 1,
            harnessMajor: 2 as const,
          })),
        ),
    },
    artifacts: {
      eraseConversation: () => Promise.reject(new Error('not used')),
      collectDiscarded: (orgId, before) => {
        calls.drafts.push([orgId, before]);
        return Promise.resolve({ artifacts: 1, blobs: 1, remaining: false });
      },
    },
    transaction: (work) => work(undefined),
    lease: {
      tryAcquire: () => Promise.resolve(options.leader ?? true),
      release: options.release ?? (() => Promise.resolve()),
    },
    holderId: 'h',
  });
  return { sweeper, settings, jobs, calls, expired, now };
}

describe('T5 retention sweeper (RTN-2, GC)', () => {
  it('collects with the release windows: payloads after a day, receipts after a week, drafts after a day', async () => {
    const { sweeper, calls, now } = sweeperOn({});
    const report = await sweeper.tick();
    expect(calls.payloads).to.deep.equal([now - PAYLOAD_GC_GRACE_MS]);
    expect(calls.receipts).to.deep.equal([now - RECEIPT_TTL_MS]);
    expect(calls.admissions).to.deep.equal([now - RECEIPT_TTL_MS]);
    expect(calls.drafts).to.deep.equal([
      ['org-1', now - DISCARDED_DRAFT_GRACE_MS],
      ['org-2', now - DISCARDED_DRAFT_GRACE_MS],
    ]);
    expect(report).to.include({ payloads: 3, receipts: 2, admissionReceipts: 1, discardedDrafts: 2 });
  });

  it('RTN-2: idle conversations of an org with a period are expired and enqueued for erasure; a live run waits', async () => {
    const fixture = sweeperOn({
      idle: { 'org-1': ['s-idle', 's-running', 's-ended'] },
      runs: { 's-running': ['completed', 'running'], 's-ended': ['completed', 'cancelled'] },
      periods: { 'org-1': 30 },
    });
    fixture.settings.set('org-1', 30);
    const report = await fixture.sweeper.tick();
    expect(report?.expiredSessions).to.equal(2);
    expect(fixture.expired).to.deep.equal(['org-1:s-idle', 'org-1:s-ended']);
    const job = (await fixture.jobs.find('org-1', 's-idle'))!;
    expect(job).to.include({ reason: 'retention', by: null, step: 'cancel_runs' });
    expect(await fixture.jobs.find('org-1', 's-running')).to.equal(null);
  });

  it('RTN-3: the default is no expiry: with no period configured, no conversation expires however long it has been idle', async () => {
    const fixture = sweeperOn({ idle: { 'org-1': ['s-ten-years-idle'], 'org-2': ['s-old'] } });
    expect(await fixture.settings.all()).to.deep.equal([]);
    for (let tick = 0; tick < 3; tick += 1) {
      expect((await fixture.sweeper.tick())?.expiredSessions).to.equal(0);
    }
    expect(fixture.expired).to.deep.equal([]);
    expect(await fixture.jobs.find('org-1', 's-ten-years-idle')).to.equal(null);
  });

  it('RTN-2: an org without a period expires nothing', async () => {
    const fixture = sweeperOn({ idle: { 'org-1': ['s-idle'] } });
    expect((await fixture.sweeper.tick())?.expiredSessions).to.equal(0);
    expect(fixture.expired).to.deep.equal([]);
  });

  it('BF-1: never enqueues a conversation deleted before erasure existed (no erasureRequestedAt), however often it sweeps', async () => {
    const conversations = new InMemoryConversations();
    conversations.put({ orgId: 'org-1', sessionId: 's-old', deleted: true, by: 'p_u', updatedAt: 1, lastActivityAt: 1 });
    const fixture = sweeperOn({ conversations });
    for (let tick = 0; tick < 3; tick += 1) {
      expect((await fixture.sweeper.tick())?.requeued).to.equal(0);
    }
    expect(await fixture.jobs.find('org-1', 's-old')).to.equal(null);
    expect(fixture.sweeper).to.not.have.property('backfill');
  });

  it('BF-2: re-enqueues, once, a deletion of this release whose own enqueue was lost; a live one never', async () => {
    const conversations = new InMemoryConversations();
    conversations.put({ orgId: 'org-1', sessionId: 's-new', deleted: true, erasureRequestedAt: 5, by: 'p_u', updatedAt: 1, lastActivityAt: 1 });
    conversations.put({ orgId: 'org-1', sessionId: 's-expired', deleted: true, erasureRequestedAt: 6, updatedAt: 1, lastActivityAt: 1 });
    conversations.put({ orgId: 'org-1', sessionId: 's-live', deleted: false, updatedAt: 1, lastActivityAt: 1 });
    const fixture = sweeperOn({ conversations });
    expect((await fixture.sweeper.tick())?.requeued).to.equal(2);
    expect(await fixture.jobs.find('org-1', 's-new')).to.include({ reason: 'user_delete', by: 'p_u' });
    expect(await fixture.jobs.find('org-1', 's-expired')).to.include({ reason: 'retention', by: null });
    expect(await fixture.jobs.find('org-1', 's-live')).to.equal(null);
    expect((await fixture.sweeper.tick())?.requeued).to.equal(0);
  });

  it('a failing part does not hold back the others', async () => {
    const { sweeper, calls } = sweeperOn({ failPayloads: true });
    const report = await sweeper.tick();
    expect(report?.payloads).to.equal(0);
    expect(calls.receipts).to.have.length(1);
    expect(report?.discardedDrafts).to.equal(2);
  });

  it('start sweeps on its interval once, however often it is called; stop ends the sweeps and gives up the lease', async () => {
    const released: Array<[string, string]> = [];
    const { sweeper, calls } = sweeperOn({
      release: (name, holder) => {
        released.push([name, holder]);
        return Promise.reject(new Error('lease store down'));
      },
    });
    const timers = sinon.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] });
    try {
      sweeper.start(5);
      sweeper.start(5);
      await timers.tickAsync(40);
      // One timer: each 5 ms interval sweeps once, never twice.
      expect(calls.payloads.length).to.equal(8);
      const stopping = sweeper.stop();
      await timers.runAllAsync();
      await stopping;
      expect(released).to.deep.equal([[RETENTION_SWEEP_LEASE_NAME, 'h']]);
      await timers.tickAsync(50);
      expect(calls.payloads.length).to.equal(8);
      expect(timers.countTimers()).to.equal(0);
    } finally {
      timers.restore();
    }
  });

  it('only the replica holding the retention lease sweeps', async () => {
    const { sweeper, calls } = sweeperOn({ leader: false });
    expect(await sweeper.tick()).to.equal(null);
    expect(calls.payloads).to.deep.equal([]);
  });
});
