import test from 'node:test';
import * as assert from 'node:assert';
import * as fsp from 'fs/promises';
import * as path from 'path';
import * as os from 'os';
import { EventCorrelator } from '../watcher/event-correlator';
import type { WatchEvent } from '../watcher/replay-event-expander';

async function withTempDir(run: (dir: string) => Promise<void>): Promise<void> {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'pipeshub-correlator-'));
  try {
    await run(dir);
  } finally {
    await fsp.rm(dir, { recursive: true, force: true });
  }
}

/**
 * A pull can land in the gap between a rename's unlink and its add — the unlink
 * fires immediately while chokidar holds the add back for its stability window.
 * Draining for a pull must leave that unlink pending: emitting it turns the
 * rename into DELETE + CREATE, which costs the record its vertex and every edge
 * hanging off it.
 */
test('draining for a pull leaves a rename half-way through to correlate', async () => {
  await withTempDir(async (dir) => {
    const correlator = new EventCorrelator({ syncRoot: dir });
    const events: WatchEvent[] = [];
    correlator.setListener((evs) => events.push(...evs));

    const from = path.join(dir, 'before.txt');
    const to = path.join(dir, 'after.txt');
    await fsp.writeFile(from, 'payload');
    const statsBeforeUnlink = await fsp.lstat(from);

    await fsp.rename(from, to);
    await correlator.push('unlink', from, statsBeforeUnlink);

    await correlator.drain(false);
    assert.deepEqual(events, [], `a pull-time drain must not emit the pending unlink, got ${JSON.stringify(events)}`);

    await correlator.push('add', to, await fsp.lstat(to));

    assert.equal(events.length, 1, `expected a single correlated event, got ${JSON.stringify(events)}`);
    assert.equal(events[0].type, 'RENAMED');
    assert.equal(events[0].oldPath, 'before.txt');
    assert.equal(events[0].path, 'after.txt');
  });
});

/**
 * The shutdown drain is the opposite case: the watcher is about to be discarded
 * and its state rewritten from disk, so an unlink left pending here is a
 * deletion nothing will ever report.
 */
test('draining for shutdown emits pending unlinks so deletes are never lost', async () => {
  await withTempDir(async (dir) => {
    const correlator = new EventCorrelator({ syncRoot: dir });
    const events: WatchEvent[] = [];
    correlator.setListener((evs) => events.push(...evs));

    const target = path.join(dir, 'gone.txt');
    await fsp.writeFile(target, 'payload');
    const statsBeforeUnlink = await fsp.lstat(target);
    await fsp.unlink(target);
    await correlator.push('unlink', target, statsBeforeUnlink);

    await correlator.drain(true);

    assert.equal(events.length, 1, `expected the delete to be emitted, got ${JSON.stringify(events)}`);
    assert.equal(events[0].type, 'DELETED');
    assert.equal(events[0].path, 'gone.txt');
  });
});
