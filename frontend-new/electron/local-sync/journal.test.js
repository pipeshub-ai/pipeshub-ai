const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { LocalSyncJournal } = require('./journal');

function withTempDir(run) {
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
