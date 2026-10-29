import test from 'node:test';
import * as assert from 'node:assert';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { LocalSyncManager } from '..';
import {
  decodeCursor,
  encodeCursor,
  type ServePullPage,
  type ServePullRequest,
  type ServePullResponse,
} from '../pull-responder-types';
import type { WatchEvent } from '../watcher/replay-event-expander';

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function setup() {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-userdata-'));
  const syncRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-syncroot-'));
  return { manager: new LocalSyncManager({ app: { getPath: () => userData } }), syncRoot, userData };
}

function pull(overrides: Partial<ServePullRequest>): ServePullRequest {
  return { connectorId: 'c-1', runId: 'run-1', batchIndex: 0, mode: 'INCREMENTAL', cursor: null, maxEvents: 50,
    timeoutMs: 60_000, ...overrides };
}

function page(response: ServePullResponse, what: string): ServePullPage {
  assert.equal(response.ok, true, `${what}: ${JSON.stringify(response).slice(0, 300)}`);
  return response as ServePullPage;
}

async function drain(manager: LocalSyncManager, runId: string, mode: 'FULL' | 'INCREMENTAL', maxEvents = 50,
  timeoutMs = 60_000): Promise<{ events: WatchEvent[]; pages: ServePullPage[] }> {
  const events: WatchEvent[] = [];
  const pages: ServePullPage[] = [];
  let cursor: string | null = null;
  for (let batchIndex = 0; batchIndex < 2000; batchIndex += 1) {
    const p = page(await manager.servePull(pull({ runId, batchIndex, mode, cursor, maxEvents, timeoutMs })),
      `${runId} page ${batchIndex}`);
    events.push(...p.events);
    pages.push(p);
    cursor = p.cursor;
    if (!p.hasMore) return { events, pages };
  }
  throw new Error('run did not end');
}

async function waitFor(check: () => boolean | Promise<boolean>, what: string, ms = 15_000): Promise<void> {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (await check()) return;
    await sleep(100);
  }
  throw new Error(`timed out waiting for ${what}`);
}

test('a journal batch bigger than a socket message is served in parts, in order', async () => {
  const { manager, syncRoot } = setup();
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  await drain(manager, 'seed', 'INCREMENTAL');
  const long = 'x'.repeat(300);
  const big: WatchEvent[] = Array.from({ length: 3000 }, (_, i) => ({
    type: 'MOVED', path: `new/${long}/${i}.txt`, oldPath: `old/${long}/${i}.txt`, timestamp: 1,
    isDirectory: false, sha256: 'ab'.repeat(32), size: 1,
  }));
  manager.journal.appendBatch('c-1', { batchId: 'big-1', timestamp: 1, events: big, replayEvents: big });
  manager.journal.appendBatch('c-1', { batchId: 'after-1', timestamp: 2, events: [
    { type: 'CREATED', path: 'tail.txt', timestamp: 2, isDirectory: false, sha256: 'cd'.repeat(32), size: 1 },
  ] });

  const { events, pages } = await drain(manager, 'big-run', 'INCREMENTAL', 1000);
  for (const p of pages) {
    assert.ok(Buffer.byteLength(JSON.stringify(p)) < 1_000_000, 'a page must fit in one socket.io message');
  }
  assert.ok(pages.length > 1, 'the big batch must be split');
  assert.deepEqual(events.map((e) => e.path), [...big.map((e) => e.path), 'tail.txt']);
  const statuses = Object.fromEntries(manager.journal.listBatches('c-1').map((b) => [b.batchId, b.status]));
  assert.equal(statuses['big-1'], 'pending', 'only the next run acks the last page');
  await drain(manager, 'next-run', 'INCREMENTAL');
  const after = Object.fromEntries(manager.journal.listBatches('c-1').map((b) => [b.batchId, b.status]));
  assert.equal(after['big-1'], 'synced');
  await manager.shutdown();
});

test('a cursor from before the change still resumes', async () => {
  const token = encodeCursor({ v: 1, mode: 'INCREMENTAL', afterBatchId: 'b-7' });
  assert.deepEqual(decodeCursor(token, 'INCREMENTAL'), { v: 1, mode: 'INCREMENTAL', afterBatchId: 'b-7' });
  const partial = encodeCursor({ v: 1, mode: 'INCREMENTAL', afterBatchId: '', inBatch: { batchId: 'b-1', offset: 40 } });
  assert.deepEqual(decodeCursor(partial, 'INCREMENTAL')?.mode, 'INCREMENTAL');
  const bad = Buffer.from(JSON.stringify({ v: 1, mode: 'INCREMENTAL', afterBatchId: '', inBatch: { batchId: 'b', offset: -1 } }))
    .toString('base64');
  assert.equal(decodeCursor(bad, 'INCREMENTAL'), null);
});

test('a slow full walk answers in time with empty preparing pages, and a newer run takes it over', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  const internals = manager as unknown as { buildFullWalk: (...args: unknown[]) => Promise<WatchEvent[]> };
  const real = internals.buildFullWalk.bind(manager);
  let walks = 0;
  internals.buildFullWalk = async (...args: unknown[]) => {
    walks += 1;
    await sleep(1500);
    return real(...args);
  };
  const started = Date.now();
  const first = page(await manager.servePull(pull({ runId: 'r1', mode: 'FULL', timeoutMs: 1000 })), 'first');
  assert.ok(Date.now() - started < 1000, 'the page must come back before the server gives up');
  assert.deepEqual([first.events.length, first.hasMore, first.preparing, first.cursor], [0, true, true, null]);
  // A newer run of the same folder adopts the walk in progress.
  const { events } = await drain(manager, 'r2', 'FULL', 50, 1000);
  assert.ok(events.some((e) => e.path === 'a.txt'));
  assert.equal(walks, 1, 'the folder must be walked once, not once per run');
  await manager.shutdown();
});

test('after the snapshot is rebuilt from nothing, incremental pulls wait for a full sync', async () => {
  const { manager, syncRoot, userData } = setup();
  await fsp.writeFile(path.join(syncRoot, 'keep.txt'), 'k');
  await fsp.writeFile(path.join(syncRoot, 'gone.txt'), 'g');
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  await drain(manager, 'seed', 'INCREMENTAL');
  await manager.shutdown();
  for (const name of fs.readdirSync(path.join(userData, 'local-sync-journal'))) {
    if (name.startsWith('watcher_state.')) fs.writeFileSync(path.join(userData, 'local-sync-journal', name), '{');
  }
  await fsp.unlink(path.join(syncRoot, 'gone.txt'));

  const m2 = new LocalSyncManager({ app: { getPath: () => userData } });
  await m2.start({ connectorId: 'c-1', rootPath: syncRoot });
  const refused = await m2.servePull(pull({ runId: 'inc', mode: 'INCREMENTAL' }));
  assert.equal(refused.ok, false);
  assert.equal((refused as { error: { code: string } }).error.code, 'CURSOR_UNKNOWN');
  const { events } = await drain(m2, 'full', 'FULL');
  assert.deepEqual(events.filter((e) => !e.isDirectory).map((e) => e.path), ['keep.txt']);
  page(await m2.servePull(pull({ runId: 'inc-2', mode: 'INCREMENTAL' })), 'incremental after the full run');
  await m2.shutdown();
});

test('changes made just before quit are reported at the next start', async () => {
  const { manager, syncRoot, userData } = setup();
  await fsp.writeFile(path.join(syncRoot, 'edit.txt'), 'v1');
  await fsp.writeFile(path.join(syncRoot, 'rename-me.txt'), 'r');
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  await drain(manager, 'seed', 'INCREMENTAL');
  await sleep(500);
  await fsp.writeFile(path.join(syncRoot, 'edit.txt'), 'version two');
  await fsp.writeFile(path.join(syncRoot, 'new.txt'), 'new');
  await fsp.rename(path.join(syncRoot, 'rename-me.txt'), path.join(syncRoot, 'renamed.txt'));
  // Well inside chokidar's 1.5 s write-stability wait: none of these has been reported yet.
  await sleep(200);
  await manager.shutdown();

  const m2 = new LocalSyncManager({ app: { getPath: () => userData } });
  await m2.start({ connectorId: 'c-1', rootPath: syncRoot });
  const { events } = await drain(m2, 'after', 'INCREMENTAL');
  const described = events.map((e) => `${e.type}:${e.oldPath ? `${e.oldPath}->` : ''}${e.path}`);
  assert.ok(described.includes('MODIFIED:edit.txt'), described.join(' '));
  assert.ok(described.includes('CREATED:new.txt'), described.join(' '));
  assert.ok(described.includes('RENAMED:rename-me.txt->renamed.txt'), described.join(' '));
  await m2.shutdown();
});

test('a file that could not be read when it changed is reported once it can be', { skip: process.platform === 'win32' || process.getuid?.() === 0 }, async () => {
  const { manager, syncRoot } = setup();
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  await drain(manager, 'seed', 'INCREMENTAL');
  const file = path.join(syncRoot, 'locked.txt');
  await fsp.writeFile(file, 'secret');
  fs.chmodSync(file, 0);
  await sleep(3500);
  const first = await drain(manager, 'while-locked', 'INCREMENTAL');
  assert.ok(!first.events.some((e) => e.path === 'locked.txt' && e.sha256), 'it cannot have a hash yet');
  fs.chmodSync(file, 0o644);
  await waitFor(async () => {
    const { events } = await drain(manager, `retry-${Date.now()}`, 'INCREMENTAL');
    return events.some((e) => e.path === 'locked.txt' && Boolean(e.sha256));
  }, 'the file to be reported with its hash');
  await manager.shutdown();
});

test('a folder that loses its read permission is not reported as deleted', { skip: process.platform === 'win32' || process.getuid?.() === 0 }, async () => {
  const { manager, syncRoot } = setup();
  await fsp.mkdir(path.join(syncRoot, 'locked', 'deeper'), { recursive: true });
  await fsp.writeFile(path.join(syncRoot, 'locked', 'a.txt'), 'a');
  await fsp.writeFile(path.join(syncRoot, 'locked', 'deeper', 'b.txt'), 'b');
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  await sleep(1000);
  await drain(manager, 'seed', 'INCREMENTAL');
  fs.chmodSync(path.join(syncRoot, 'locked'), 0);
  await sleep(4000);
  const { events } = await drain(manager, 'after-chmod', 'INCREMENTAL');
  fs.chmodSync(path.join(syncRoot, 'locked'), 0o755);
  assert.deepEqual(events.filter((e) => e.type.endsWith('DELETED')).map((e) => e.path), []);
  await manager.shutdown();
});

test('a folder that was unreadable and is then really deleted is reported deleted', { skip: process.platform === 'win32' || process.getuid?.() === 0 }, async () => {
  const { manager, syncRoot } = setup();
  await fsp.mkdir(path.join(syncRoot, 'locked'), { recursive: true });
  await fsp.writeFile(path.join(syncRoot, 'locked', 'a.txt'), 'a');
  await manager.start({ connectorId: 'c-1', rootPath: syncRoot });
  await sleep(1000);
  await drain(manager, 'seed', 'INCREMENTAL');
  fs.chmodSync(path.join(syncRoot, 'locked'), 0);
  await sleep(4000);
  await drain(manager, 'while-locked', 'INCREMENTAL');
  fs.chmodSync(path.join(syncRoot, 'locked'), 0o755);
  fs.rmSync(path.join(syncRoot, 'locked'), { recursive: true });
  await sleep(4000);
  const { events } = await drain(manager, 'after-delete', 'INCREMENTAL');
  assert.ok(events.some((e) => e.type === 'DELETED' && e.path === 'locked/a.txt'), JSON.stringify(events));
  await manager.shutdown();
});
