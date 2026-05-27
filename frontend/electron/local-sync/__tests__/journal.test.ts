import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { LocalSyncJournal } from '../persistence/journal';

function withTempDir(run: (dir: string) => void): void {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'local-sync-journal-'));
  try {
    run(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('journal records pending batches and marks them synced', () => {
  withTempDir((dir) => {
    const journal = new LocalSyncJournal(dir);
    const connectorId = 'connector-123';

    journal.appendBatch(connectorId, {
      batchId: 'batch-1',
      timestamp: Date.now(),
      events: [
        {
          type: 'CREATED',
          path: 'docs/readme.md',
          timestamp: Date.now(),
          isDirectory: false,
        },
      ],
    });

    let summary = journal.getSummary(connectorId);
    assert.equal(summary.pendingCount, 1);
    assert.equal(summary.failedCount, 0);
    assert.equal(summary.syncedCount, 0);

    journal.updateBatchStatus(connectorId, 'batch-1', 'synced');
    summary = journal.getSummary(connectorId);
    assert.equal(summary.pendingCount, 0);
    assert.equal(summary.failedCount, 0);
    assert.equal(summary.syncedCount, 1);
    assert.equal(summary.lastBatchId, 'batch-1');
    assert.ok(summary.lastAckAt);
  });
});

test('failed batch becomes replayable and clears once marked synced', () => {
  withTempDir((dir) => {
    const journal = new LocalSyncJournal(dir);
    const connectorId = 'connector-reconnect';

    journal.appendBatch(connectorId, {
      batchId: 'batch-r1',
      timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'a.txt', timestamp: Date.now(), isDirectory: false }],
    });
    journal.updateBatchStatus(connectorId, 'batch-r1', 'failed', { lastError: 'offline' });

    // Simulates the retry loop: getReplayableBatches must still return it.
    let replayable = journal.getReplayableBatches(connectorId);
    assert.equal(replayable.length, 1);
    assert.equal(replayable[0].batchId, 'batch-r1');
    assert.equal(replayable[0].attemptCount, 1);

    // Network returns → dispatcher succeeds → mark synced.
    journal.updateBatchStatus(connectorId, 'batch-r1', 'synced', { lastError: null });
    replayable = journal.getReplayableBatches(connectorId);
    assert.equal(replayable.length, 0);
    assert.equal(journal.readCursor(connectorId).lastAckBatchId, 'batch-r1');
  });
});

test('startup replay sees pending batches left from prior session', () => {
  withTempDir((dir) => {
    const j1 = new LocalSyncJournal(dir);
    const connectorId = 'connector-restart';

    j1.setMeta(connectorId, { apiBaseUrl: 'http://x', rootPath: '/tmp/x' });
    j1.appendBatch(connectorId, {
      batchId: 'batch-s1',
      timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'b.txt', timestamp: Date.now(), isDirectory: false }],
    });

    // Simulate app restart: new journal instance over the same baseDir.
    const j2 = new LocalSyncJournal(dir);
    assert.deepEqual(j2.listConnectorIds(), [connectorId]);
    const pending = j2.getReplayableBatches(connectorId);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].status, 'pending');
  });
});

test('journal tracks failed batches for replay', () => {
  withTempDir((dir) => {
    const journal = new LocalSyncJournal(dir);
    const connectorId = 'connector-xyz';

    journal.appendBatch(connectorId, {
      batchId: 'batch-a',
      timestamp: Date.now(),
      events: [],
    });
    journal.updateBatchStatus(connectorId, 'batch-a', 'failed', {
      lastError: 'network timeout',
    });

    const pendingOrFailed = journal.getPendingOrFailedBatches(connectorId);
    assert.equal(pendingOrFailed.length, 1);
    assert.equal(pendingOrFailed[0].status, 'failed');
    assert.equal(pendingOrFailed[0].lastError, 'network timeout');
  });
});

test('quarantined batches are excluded from replay queues but counted in summary', () => {
  withTempDir((dir) => {
    const journal = new LocalSyncJournal(dir);
    const connectorId = 'connector-q';

    // Three batches: one synced, one pending, one quarantined.
    journal.appendBatch(connectorId, {
      batchId: 'batch-good',
      timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'a.txt', timestamp: Date.now(), isDirectory: false }],
    });
    journal.appendBatch(connectorId, {
      batchId: 'batch-pending',
      timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'b.txt', timestamp: Date.now(), isDirectory: false }],
    });
    journal.appendBatch(connectorId, {
      batchId: 'batch-poison',
      timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'c.txt', timestamp: Date.now(), isDirectory: false }],
    });
    journal.updateBatchStatus(connectorId, 'batch-good', 'synced');
    journal.updateBatchStatus(connectorId, 'batch-poison', 'quarantined', { lastError: '403 forbidden' });

    // Replay queues skip quarantined.
    const replayable = journal.getReplayableBatches(connectorId);
    assert.equal(replayable.length, 1);
    assert.equal(replayable[0].batchId, 'batch-pending');

    const pendingOrFailed = journal.getPendingOrFailedBatches(connectorId);
    assert.equal(pendingOrFailed.length, 1);
    assert.equal(pendingOrFailed[0].batchId, 'batch-pending');

    // Non-synced helper used by REPLACE full-sync includes quarantined so the
    // history is wiped after a backend reset.
    const nonSynced = journal.getNonSyncedBatches(connectorId);
    assert.equal(nonSynced.length, 2);
    const ids = nonSynced.map((b) => b.batchId).sort();
    assert.deepEqual(ids, ['batch-pending', 'batch-poison']);

    // Summary counts each status separately.
    const summary = journal.getSummary(connectorId);
    assert.equal(summary.pendingCount, 1);
    assert.equal(summary.failedCount, 0);
    assert.equal(summary.quarantinedCount, 1);
    assert.equal(summary.syncedCount, 1);
  });
});

test('getReplayableBatches with includeSynced excludes quarantined', () => {
  withTempDir((dir) => {
    const journal = new LocalSyncJournal(dir);
    const connectorId = 'connector-q2';

    journal.appendBatch(connectorId, {
      batchId: 'batch-1', timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'a', timestamp: Date.now(), isDirectory: false }],
    });
    journal.appendBatch(connectorId, {
      batchId: 'batch-2', timestamp: Date.now(),
      events: [{ type: 'CREATED', path: 'b', timestamp: Date.now(), isDirectory: false }],
    });
    journal.updateBatchStatus(connectorId, 'batch-1', 'synced');
    journal.updateBatchStatus(connectorId, 'batch-2', 'quarantined', { lastError: 'poison' });

    const all = journal.getReplayableBatches(connectorId, { includeSynced: true });
    // Synced is included, quarantined is not.
    assert.equal(all.length, 1);
    assert.equal(all[0].batchId, 'batch-1');
  });
});
