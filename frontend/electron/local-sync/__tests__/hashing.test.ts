import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import * as crypto from 'crypto';
import { EventCorrelator } from '../watcher/event-correlator';
import { expandWatchEventsForReplay, type WatchEvent, type FileStateMap } from '../watcher/replay-event-expander';
import { WatcherStateStore, scanSyncRoot } from '../persistence/watcher-state-store';

function sha256Of(content: string): string {
  return crypto.createHash('sha256').update(content).digest('hex');
}

async function withTempDir(run: (dir: string) => Promise<void>): Promise<void> {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'local-sync-hashing-'));
  try {
    await run(dir);
  } finally {
    await fsp.rm(dir, { recursive: true, force: true });
  }
}

test('CREATED and RENAMED events carry the full-content sha256, not a 4KB-prefix heuristic', async () => {
  await withTempDir(async (dir) => {
    const correlator = new EventCorrelator({ syncRoot: dir });
    const events: WatchEvent[] = [];
    correlator.setListener((evs) => events.push(...evs));

    // First 4096 bytes are identical to what a prefix-only hash would see;
    // only the tail differs. A correct full hash must reflect the tail.
    const prefix = 'a'.repeat(4096);
    const content = `${prefix}version-1-tail`;
    const expectedHash = sha256Of(content);

    const absPath = path.join(dir, 'big.txt');
    await fsp.writeFile(absPath, content);
    await correlator.push('add', absPath, await fsp.lstat(absPath));
    correlator.flush(true);

    assert.equal(events.length, 1);
    const created = events[0];
    assert.equal(created.type, 'CREATED');
    assert.equal(created.sha256, expectedHash);

    events.length = 0;
    const newPath = path.join(dir, 'renamed.txt');
    const statsBeforeUnlink = await fsp.lstat(absPath);
    await fsp.rename(absPath, newPath);
    await correlator.push('unlink', absPath, statsBeforeUnlink);
    // Same inode as the pre-rename stat (same file, same volume), so this
    // resolves synchronously via the inode-match path in handleAdd rather
    // than waiting for the correlation-window flush.
    await correlator.push('add', newPath, await fsp.lstat(newPath));

    assert.equal(events.length, 1);
    const renameEvent = events[0];
    assert.ok(renameEvent.type === 'RENAMED' || renameEvent.type === 'MOVED');
    assert.equal(renameEvent.sha256, expectedHash);
  });
});

test('MODIFIED events (non-suppressed content change) carry the new full-content sha256', async () => {
  await withTempDir(async (dir) => {
    const absPath = path.join(dir, 'note.txt');
    await fsp.writeFile(absPath, 'v1');

    const correlator = new EventCorrelator({ syncRoot: dir, changeDebounceMs: 0 });
    const events: WatchEvent[] = [];
    correlator.setListener((evs) => events.push(...evs));

    await fsp.writeFile(absPath, 'v2 changed content');
    await correlator.push('change', absPath, await fsp.lstat(absPath));
    await correlator.drain();

    const modified = events.find((e) => e.type === 'MODIFIED');
    assert.ok(modified);
    assert.equal(modified!.sha256, sha256Of('v2 changed content'));
  });
});

test('WatcherStateStore.reconcile() attaches sha256 to CREATED/MODIFIED/RENAMED events', async () => {
  await withTempDir(async (dir) => {
    const baseDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'local-sync-state-'));
    try {
      const store = new WatcherStateStore({ baseDir, syncRoot: dir, connectorInstanceId: 'c-1' });
      store.load();

      await fsp.writeFile(path.join(dir, 'a.txt'), 'hello');
      let scan = await scanSyncRoot(dir, {});
      store.applyScan(scan);

      await fsp.writeFile(path.join(dir, 'a.txt'), 'hello world');
      scan = await scanSyncRoot(dir, {});
      const modifiedEvents = store.reconcile(scan);
      const modified = modifiedEvents.find((e) => e.path === 'a.txt' && e.type === 'MODIFIED');
      assert.ok(modified);
      const expected = sha256Of('hello world');
      assert.equal(modified!.sha256, expected);
      store.applyScan(scan);

      await fsp.rename(path.join(dir, 'a.txt'), path.join(dir, 'b.txt'));
      scan = await scanSyncRoot(dir, {});
      const renameEvents = store.reconcile(scan);
      const renamed = renameEvents.find((e) => e.type === 'RENAMED' || e.type === 'MOVED');
      assert.ok(renamed);
      assert.equal(renamed!.sha256, expected);
    } finally {
      await fsp.rm(baseDir, { recursive: true, force: true }).catch(() => {});
    }
  });
});

test('expandWatchEventsForReplay hashes fresh when the file is reachable, falls back to the cached hash otherwise', async () => {
  await withTempDir(async (dir) => {
    await fsp.mkdir(path.join(dir, 'newdir'), { recursive: true });
    await fsp.writeFile(path.join(dir, 'newdir', 'kept.txt'), 'fresh content');
    // 'gone.txt' is deliberately not created on disk, simulating a stale
    // replay where the directory-move batch is processed after the file was
    // deleted again.

    const event: WatchEvent = {
      type: 'DIR_RENAMED', path: 'newdir', oldPath: 'olddir', timestamp: Date.now(), isDirectory: true,
    };
    const files: FileStateMap = {
      'olddir/kept.txt': { isDirectory: false, sha256: 'stale-cached-kept-hash' },
      'olddir/gone.txt': { isDirectory: false, sha256: 'stale-cached-gone-hash' },
    };

    const expanded = await expandWatchEventsForReplay([event], files, dir);
    const keptEv = expanded.find((e) => e.path === 'newdir/kept.txt');
    const goneEv = expanded.find((e) => e.path === 'newdir/gone.txt');
    assert.ok(keptEv);
    assert.ok(goneEv);
    assert.equal(keptEv!.sha256, sha256Of('fresh content'));
    assert.equal(goneEv!.sha256, 'stale-cached-gone-hash');
  });
});

test('expandWatchEventsForReplay uses the cached hash directly when no root path is supplied', async () => {
  const event: WatchEvent = {
    type: 'DIR_MOVED', path: 'newdir', oldPath: 'olddir', timestamp: 1, isDirectory: true,
  };
  const files: FileStateMap = {
    'olddir/x.txt': { isDirectory: false, sha256: 'cached-x-hash' },
  };
  const expanded = await expandWatchEventsForReplay([event], files);
  const xEv = expanded.find((e) => e.path === 'newdir/x.txt');
  assert.ok(xEv);
  assert.equal(xEv!.sha256, 'cached-x-hash');
});
