/**
 * WP-23 (OWD-12, C4.10 "Snapshot TTL / retention"): engine sandbox snapshots
 * are recorded with the event that names them (SNT-5), an org's TTL is the
 * release's 7 days (SNT-6), and the retention
 * sweeper deletes every snapshot older than its org's TTL, the object first
 * (SNT-7, SNT-8).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { InMemorySandboxSnapshotIndex } from '../../../src/modules/agent-harness/adapters/in-memory-sandbox-snapshots';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import { SnapshotRecordingJournalStore } from '../../../src/modules/agent-harness/application/snapshot-recording-journal-store';
import { RELEASE_SNAPSHOT_TTL } from '../../../src/modules/agent-harness/application/release-run-settings';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import type { PreparedJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import {
  DAY_MS,
  RELEASE_SNAPSHOT_TTL_DAYS,
  snapshotExpired,
  snapshotTtlDays,
} from '../../../src/modules/agent-harness/domain/snapshot-ttl';
import type { JournalStore } from '../../../src/modules/agent-harness/ports/journal-store';
import { describeSandboxSnapshotIndexContract } from './support/sandbox-snapshots-contract';

describeSandboxSnapshotIndexContract('in-memory', () => new InMemorySandboxSnapshotIndex());

const NOW = 1000 * DAY_MS;
const TS = new Date(NOW).toISOString();

const snapshotted = (objectId: string) => ({
  type: 'engine.snapshotted',
  critical: false,
  visibility: 'internal',
  data: { snapshot: { objectId, contentHash: 'sha256:x', sizeBytes: 1 }, turnId: 't', reason: 'turn_end' },
});

describe('WP-23 snapshot TTL (OWD-12)', () => {
  it('SNT-6: the release keeps snapshots 7 days; a resolved TTL wins; an older pin keeps the release', () => {
    expect(RELEASE_DEFAULTS.snapshots.ttlDays).to.equal(7);
    expect(RELEASE_SNAPSHOT_TTL_DAYS).to.equal(7);
    expect(snapshotTtlDays(null)).to.equal(7);
    expect(snapshotTtlDays({ schemaVersion: 1, dimensions: {} })).to.equal(7);
    expect(
      snapshotTtlDays({
        schemaVersion: 1,
        dimensions: { 'snapshots.ttlDays': { value: 2, setBy: 'org', ceilings: [] } },
      }),
    ).to.equal(2);
    expect(snapshotExpired(new Date(NOW - 7 * DAY_MS).toISOString(), 7, NOW)).to.equal(false);
    expect(snapshotExpired(new Date(NOW - 7 * DAY_MS - 1).toISOString(), 7, NOW)).to.equal(true);
    expect(snapshotExpired('not a time', 7, NOW)).to.equal(true);
  });

  it("SNT-6: every org's TTL is the release's 7 days", async () => {
    expect(await RELEASE_SNAPSHOT_TTL.days('org-1')).to.equal(RELEASE_SNAPSHOT_TTL_DAYS);
    expect(RELEASE_SNAPSHOT_TTL_DAYS).to.equal(7);
  });

  it('SNT-5: every snapshot an append names is recorded at the append time, in its transaction, with the region', async () => {
    const index = new InMemorySandboxSnapshotIndex();
    const seen: unknown[] = [];
    const inner = {
      append: async () => ({ streamId: 's', firstSeq: 1, lastSeq: 2, ts: TS }),
    } as unknown as JournalStore<string>;
    const store = new SnapshotRecordingJournalStore<string>(
      inner,
      {
        record: (rows, tx) => {
          seen.push(tx);
          return index.record(rows);
        },
        orgs: () => index.orgs(),
        takenBefore: (o, b, l) => index.takenBefore(o, b, l),
        forget: (o, ids, b) => index.forget(o, ids, b),
        forgetSession: (o, s) => index.forgetSession(o, s),
      },
      'eu-west',
    );
    const batch = {
      orgId: 'org-1',
      sessionId: 'ses-1',
      streamId: 'ses-1',
      runId: 'run-1',
      writer: 'worker',
      events: [snapshotted('obj-1'), { type: 'turn.completed', critical: true, visibility: 'internal', data: {} }],
    } as unknown as PreparedJournalAppend;
    await store.append(batch, 'tx-1');
    expect(seen).to.deep.equal(['tx-1']);
    expect([...index.rows.values()]).to.deep.equal([
      { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-1', objectId: 'obj-1', takenAt: NOW, region: 'eu-west' },
    ]);
  });

  function sweeper(index: InMemorySandboxSnapshotIndex, days: Record<string, number>, failing = new Set<string>()) {
    const purged: string[] = [];
    const none = () => Promise.resolve(0);
    const swept = new RetentionSweeper<unknown>({
      store: {
        now: () => Promise.resolve(NOW),
        collectPayloads: none,
        expireCommandReceipts: none,
        expireAdmissionReceipts: none,
        harnessOrgs: () => Promise.resolve([]),
      },
      settings: { all: async () => [] },
      conversations: {
        idleSince: async () => [],
        expire: async () => false,
        awaitingErasure: async () => [],
      },
      erasures: { enqueue: async () => false } as never,
      runs: { runsOf: async () => [] },
      artifacts: null,
      snapshots: {
        index,
        ttl: { days: async (orgId) => days[orgId] ?? 7 },
        objects: {
          purge: async (orgId, objectId) => {
            if (failing.has(objectId)) throw new Error('storage down');
            purged.push(`${orgId}:${objectId}`);
          },
        },
      },
      transaction: (work) => work(undefined),
      lease: { tryAcquire: async () => true, release: async () => undefined },
      holderId: 'h',
    });
    return { swept, purged };
  }

  const at = (orgId: string, objectId: string, ageDays: number, sessionId = 'ses-1') => ({
    orgId,
    sessionId,
    runId: null,
    objectId,
    takenAt: NOW - ageDays * DAY_MS,
    region: 'default',
  });

  it("SNT-7: the sweeper deletes each org's snapshots past its own TTL, the object first, and keeps the rest", async () => {
    const index = new InMemorySandboxSnapshotIndex();
    await index.record([at('org-1', 'old', 8), at('org-1', 'young', 6), at('org-2', 'mid', 4), at('org-2', 'fresh', 2)]);
    const { swept, purged } = sweeper(index, { 'org-2': 3 });
    const report = await swept.tick();
    expect(report?.expiredSnapshots).to.equal(2);
    expect(purged.sort()).to.deep.equal(['org-1:old', 'org-2:mid']);
    expect([...index.rows.keys()].sort()).to.deep.equal(['org-1:young', 'org-2:fresh']);
  });

  it('SNT-8: a snapshot whose object could not be deleted keeps its row and is tried again on the next pass', async () => {
    const index = new InMemorySandboxSnapshotIndex();
    await index.record([at('org-1', 'stuck', 9), at('org-1', 'old', 9)]);
    const failing = new Set(['stuck']);
    const { swept, purged } = sweeper(index, {}, failing);
    expect((await swept.tick())?.expiredSnapshots).to.equal(1);
    expect([...index.rows.keys()]).to.deep.equal(['org-1:stuck']);
    failing.clear();
    expect((await swept.tick())?.expiredSnapshots).to.equal(1);
    expect(purged).to.deep.equal(['org-1:old', 'org-1:stuck']);
    expect(index.rows.size).to.equal(0);
  });

});
