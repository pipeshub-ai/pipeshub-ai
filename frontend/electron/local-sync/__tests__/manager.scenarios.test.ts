import test from 'node:test';
import * as assert from 'node:assert';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';
import * as os from 'os';
import { LocalSyncManager } from '..';
import type { DispatchFileEventBatchArgs } from '../transport/file-event-dispatcher';
import type { WatchEvent } from '../watcher/replay-event-expander';

interface DispatchedRecord {
  connectorId: string;
  events: WatchEvent[];
  resetBeforeApply: boolean;
  batchId: string;
}

const TOKEN = 'test-token';
// Unreachable; we never pass connectorDisplayType, so scheduleCrawlingManagerJob
// is skipped (manager.start() guards on connectorDisplayType + interval).
const API_BASE = 'http://127.0.0.1:1';
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const ALL_CHANGE_FINAL_PATHS = [
  'created.txt',
  'existing.txt',
  'nested/move-to.txt',
  'rename-to.txt',
].sort();
const ALL_CHANGE_REMOVED_PATHS = [
  'delete-me.txt',
  'move-from.txt',
  'rename-from.txt',
];

function markFullSyncSeen(manager: LocalSyncManager, connectorId: string, rootPath: string): void {
  const signature = JSON.stringify({
    rootPath: path.resolve(String(rootPath || '')),
    apiBaseUrl: API_BASE,
    includeSubfolders: true,
  });
  (manager as unknown as {
    lastReplaceSyncSignature: Map<string, string>;
  }).lastReplaceSyncSignature.set(connectorId, signature);
}

function setup() {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-userdata-'));
  const syncRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-syncroot-'));
  const dispatched: DispatchedRecord[] = [];
  const dispatchFileEventBatch = async (args: DispatchFileEventBatchArgs) => {
    dispatched.push({
      connectorId: args.connectorId,
      events: args.events,
      resetBeforeApply: args.resetBeforeApply === true,
      batchId: args.batchId,
    });
    return null;
  };
  const app = { getPath: () => userData };
  const manager = new LocalSyncManager({ app, dispatchFileEventBatch });
  return { manager, dispatched, syncRoot, userData };
}

function reopen(userData: string) {
  const dispatched: DispatchedRecord[] = [];
  const dispatchFileEventBatch = async (args: DispatchFileEventBatchArgs) => {
    dispatched.push({
      connectorId: args.connectorId,
      events: args.events,
      resetBeforeApply: args.resetBeforeApply === true,
      batchId: args.batchId,
    });
    return null;
  };
  const manager = new LocalSyncManager({
    app: { getPath: () => userData },
    dispatchFileEventBatch,
  });
  return { manager, dispatched };
}

async function seedAllChangeFiles(syncRoot: string): Promise<void> {
  await fsp.writeFile(path.join(syncRoot, 'existing.txt'), 'old');
  await fsp.writeFile(path.join(syncRoot, 'rename-from.txt'), 'rename me');
  await fsp.writeFile(path.join(syncRoot, 'move-from.txt'), 'move me');
  await fsp.writeFile(path.join(syncRoot, 'delete-me.txt'), 'delete me');
}

async function applyAllChangeTypes(syncRoot: string): Promise<void> {
  await fsp.writeFile(path.join(syncRoot, 'created.txt'), 'created');
  await fsp.writeFile(path.join(syncRoot, 'existing.txt'), 'updated content');
  await fsp.rename(path.join(syncRoot, 'rename-from.txt'), path.join(syncRoot, 'rename-to.txt'));
  await fsp.mkdir(path.join(syncRoot, 'nested'), { recursive: true });
  await fsp.rename(path.join(syncRoot, 'move-from.txt'), path.join(syncRoot, 'nested', 'move-to.txt'));
  await fsp.unlink(path.join(syncRoot, 'delete-me.txt'));
}

function flattenEvents(dispatched: DispatchedRecord[], opts?: { resetBeforeApply?: boolean }): WatchEvent[] {
  return dispatched
    .filter((record) => opts?.resetBeforeApply === undefined || record.resetBeforeApply === opts.resetBeforeApply)
    .flatMap((record) => record.events || []);
}

function assertAllLiveChangeEvents(events: WatchEvent[], message: string): void {
  assert.ok(
    events.some((e) => e.type === 'CREATED' && e.path === 'created.txt'),
    `${message}: expected CREATED created.txt, got ${JSON.stringify(events)}`,
  );
  assert.ok(
    events.some((e) => e.type === 'MODIFIED' && e.path === 'existing.txt'),
    `${message}: expected MODIFIED existing.txt, got ${JSON.stringify(events)}`,
  );
  assert.ok(
    events.some((e) => e.type === 'RENAMED' && e.oldPath === 'rename-from.txt' && e.path === 'rename-to.txt'),
    `${message}: expected RENAMED rename-from.txt -> rename-to.txt, got ${JSON.stringify(events)}`,
  );
  assert.ok(
    events.some((e) => e.type === 'MOVED' && e.oldPath === 'move-from.txt' && e.path === 'nested/move-to.txt'),
    `${message}: expected MOVED move-from.txt -> nested/move-to.txt, got ${JSON.stringify(events)}`,
  );
  assert.ok(
    events.some((e) => e.type === 'DELETED' && e.path === 'delete-me.txt'),
    `${message}: expected DELETED delete-me.txt, got ${JSON.stringify(events)}`,
  );
}

function assertNoLiveChangeEvents(dispatched: DispatchedRecord[], message: string): void {
  const events = flattenEvents(dispatched, { resetBeforeApply: false });
  const changedPaths = new Set([...ALL_CHANGE_FINAL_PATHS, ...ALL_CHANGE_REMOVED_PATHS]);
  const leaked = events.filter((event) => changedPaths.has(event.path) || (event.oldPath && changedPaths.has(event.oldPath)));
  assert.deepEqual(leaked, [], `${message}: expected no live change dispatches, got ${JSON.stringify(leaked)}`);
}

function assertFullSyncFinalState(dispatched: DispatchedRecord[], message: string): void {
  const fullSync = dispatched.find((record) => record.resetBeforeApply === true);
  assert.ok(fullSync, `${message}: expected a resetBeforeApply full-sync, got ${JSON.stringify(dispatched)}`);

  const events = fullSync!.events || [];
  const paths = events.map((event) => event.path).sort();
  assert.deepEqual(paths, ALL_CHANGE_FINAL_PATHS, `${message}: full-sync paths should match current disk state`);
  assert.ok(
    events.every((event) => event.type === 'CREATED'),
    `${message}: full-sync should express the replacement snapshot as CREATED events, got ${JSON.stringify(events)}`,
  );
}

test('MANUAL: file create dispatches within ~2s', async () => {
  const { manager, dispatched, syncRoot } = setup();
  await manager.start({
    connectorId: 'c-manual',
    connectorName: 'Manual Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  await sleep(700); // chokidar 'ready'

  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'hello');
  await sleep(2500); // correlator (250ms) + awaitWriteFinish (200ms) + dispatcher (1000ms)

  await manager.stop('c-manual');

  const created = dispatched
    .flatMap((d) => d.events || [])
    .filter((e) => e.type === 'CREATED' && e.path === 'a.txt');
  assert.equal(
    created.length,
    1,
    `expected 1 CREATED event for a.txt, got ${created.length} (dispatched=${JSON.stringify(dispatched)})`,
  );
});

test('MANUAL: create, content change, rename, move, and delete dispatch right away', async () => {
  const { manager, dispatched, syncRoot } = setup();
  await seedAllChangeFiles(syncRoot);
  markFullSyncSeen(manager, 'c-manual-all', syncRoot);

  await manager.start({
    connectorId: 'c-manual-all',
    connectorName: 'Manual All Changes',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  await sleep(1000);

  await applyAllChangeTypes(syncRoot);
  await sleep(3500);

  await manager.stop('c-manual-all');

  assertAllLiveChangeEvents(
    flattenEvents(dispatched, { resetBeforeApply: false }),
    'MANUAL all changes',
  );
});

test('SCHEDULED: file create is held until tick fires', async () => {
  const { manager, dispatched, syncRoot } = setup();
  await manager.start({
    connectorId: 'c-sched',
    connectorName: 'Scheduled Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 }, // won't fire during the test
  });
  await sleep(700);

  await fsp.writeFile(path.join(syncRoot, 'b.txt'), 'world');
  await sleep(2500);

  // SCHEDULED gate holds the live batch in the journal as 'pending'.
  // (The initial REPLACE full-sync dispatches an empty batch with
  // resetBeforeApply=true on first start; we only care that no live event for
  // b.txt has been dispatched.)
  const beforeTick = dispatched.flatMap((d) => d.events || []).filter((e) => e.path === 'b.txt');
  assert.equal(
    beforeTick.length,
    0,
    `expected 0 events for b.txt before tick, got ${beforeTick.length}`,
  );

  // Manual tick drains the pending journal batch.
  await manager.runScheduledTick('c-sched');

  await manager.stop('c-sched');

  const created = dispatched
    .flatMap((d) => d.events || [])
    .filter((e) => e.type === 'CREATED' && e.path === 'b.txt');
  assert.ok(
    created.length >= 1,
    `expected ≥1 CREATED event for b.txt after tick, got ${created.length}`,
  );
});

test('SCHEDULED: create, content change, rename, move, and delete are held until tick', async () => {
  const { manager, dispatched, syncRoot } = setup();
  await seedAllChangeFiles(syncRoot);
  markFullSyncSeen(manager, 'c-sched-all', syncRoot);

  await manager.start({
    connectorId: 'c-sched-all',
    connectorName: 'Scheduled All Changes',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 },
  });
  await sleep(1000);

  await applyAllChangeTypes(syncRoot);
  await sleep(3500);

  assertNoLiveChangeEvents(dispatched, 'SCHEDULED before tick');

  await manager.runScheduledTick('c-sched-all');
  await manager.stop('c-sched-all');

  assertAllLiveChangeEvents(
    flattenEvents(dispatched, { resetBeforeApply: false }),
    'SCHEDULED after tick',
  );
});

test('SCHEDULED metadata omits credentials', async () => {
  const { manager, syncRoot } = setup();
  const status = await manager.start({
    connectorId: 'c-clean-meta',
    connectorName: 'Clean Meta',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: {
      intervalMinutes: 60,
      timezone: 'UTC',
    },
  });

  const meta = manager.journal.getMeta('c-clean-meta') as Record<string, unknown>;
  assert.deepEqual(Object.keys(meta).filter((key) => key.toLowerCase().includes('token')), []);
  assert.deepEqual(meta.scheduledConfig, { intervalMinutes: 60, timezone: 'UTC' });
  assert.deepEqual(status.scheduledConfig, { intervalMinutes: 60, timezone: 'UTC' });

  await manager.shutdown();
});

test('Close → reopen: full-sync sends current disk state with resetBeforeApply', async () => {
  const { manager, syncRoot, userData } = setup();
  await manager.start({
    connectorId: 'c-reopen',
    connectorName: 'Reopen Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  await sleep(700);
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await sleep(2500);

  // Close.
  await manager.shutdown();

  // Modify on disk while the manager is "closed".
  await fsp.writeFile(path.join(syncRoot, 'b.txt'), 'b');
  await fsp.unlink(path.join(syncRoot, 'a.txt'));

  // Reopen — same userData carries the journal forward.
  const { manager: m2, dispatched: d2 } = reopen(userData);
  await m2.init();
  await sleep(500);
  assert.equal(d2.length, 0, 'init must not dispatch without a live access token');

  await m2.start({
    connectorId: 'c-reopen',
    connectorName: 'Reopen Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  // start's full-sync is fire-and-forget; wait for it to finish.
  await sleep(1500);

  const fullSync = d2.find((d) => d.resetBeforeApply === true);
  assert.ok(
    fullSync,
    `expected a full-sync dispatch with resetBeforeApply=true, got ${JSON.stringify(d2)}`,
  );
  const paths = (fullSync!.events || []).map((e) => e.path);
  assert.ok(paths.includes('b.txt'), 'b.txt should be in full-sync (created while closed)');
  assert.ok(!paths.includes('a.txt'), 'a.txt should NOT be in full-sync (deleted while closed)');

  await m2.shutdown();
});

test('Close → reopen: full-sync catches create, content change, rename, move, and delete', async () => {
  const { manager, syncRoot, userData } = setup();
  await seedAllChangeFiles(syncRoot);
  markFullSyncSeen(manager, 'c-reopen-all', syncRoot);

  await manager.start({
    connectorId: 'c-reopen-all',
    connectorName: 'Reopen All Changes',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  await sleep(1000);

  await manager.shutdown();

  await applyAllChangeTypes(syncRoot);

  const { manager: m2, dispatched: d2 } = reopen(userData);
  await m2.init();
  await sleep(500);
  assert.equal(d2.length, 0, 'init must not dispatch without a live access token');

  await m2.start({
    connectorId: 'c-reopen-all',
    connectorName: 'Reopen All Changes',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  await sleep(1500);

  assertFullSyncFinalState(d2, 'Close reopen all changes');

  await m2.shutdown();
});

test('Closed during scheduled tick: tick is dropped, reopen full-sync covers it', async () => {
  const { manager, dispatched, syncRoot, userData } = setup();
  await manager.start({
    connectorId: 'c-tickclose',
    connectorName: 'Tick-Close Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 },
  });
  await sleep(700);

  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await sleep(2500);

  // Tick has not fired (60 min interval, test ran <5s). The only allowed
  // dispatch is the initial REPLACE full-sync (empty events when start()
  // ran against an empty syncRoot). The live a.txt event must remain queued.
  const beforeClose = dispatched.flatMap((d) => d.events || []).filter((e) => e.path === 'a.txt');
  assert.equal(
    beforeClose.length,
    0,
    `expected 0 events for a.txt before close, got ${beforeClose.length}`,
  );

  // Close — the scheduled timer is cleared (manager stop() clearInterval).
  await manager.shutdown();

  // Reopen — init waits for a live token; start's full-sync brings backend to current disk state.
  const { manager: m2, dispatched: d2 } = reopen(userData);
  await m2.init();
  await sleep(500);
  assert.equal(d2.length, 0, 'init must not dispatch without a live access token');

  await m2.start({
    connectorId: 'c-tickclose',
    connectorName: 'Tick-Close Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 },
  });
  await sleep(1500);

  const fullSync = d2.find((d) => d.resetBeforeApply === true);
  assert.ok(
    fullSync,
    `expected full-sync after reopen to cover the dropped tick, got ${JSON.stringify(d2)}`,
  );
  const created = (fullSync!.events || []).filter((e) => e.type === 'CREATED' && e.path === 'a.txt');
  assert.equal(
    created.length,
    1,
    `expected a.txt in full-sync CREATED events, got ${created.length}`,
  );

  await m2.shutdown();
});

test('Closed during scheduled tick: full-sync catches create, content change, rename, move, and delete', async () => {
  const { manager, dispatched, syncRoot, userData } = setup();
  await seedAllChangeFiles(syncRoot);
  markFullSyncSeen(manager, 'c-tickclose-all', syncRoot);

  await manager.start({
    connectorId: 'c-tickclose-all',
    connectorName: 'Tick-Close All Changes',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 },
  });
  await sleep(1000);

  await applyAllChangeTypes(syncRoot);
  await sleep(3500);

  assertNoLiveChangeEvents(dispatched, 'SCHEDULED all changes before close');

  await manager.shutdown();

  const { manager: m2, dispatched: d2 } = reopen(userData);
  await m2.init();
  await sleep(500);
  assert.equal(d2.length, 0, 'init must not dispatch without a live access token');

  await m2.start({
    connectorId: 'c-tickclose-all',
    connectorName: 'Tick-Close All Changes',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 },
  });
  await sleep(1500);

  assertFullSyncFinalState(d2, 'Closed during scheduled tick all changes');

  await m2.shutdown();
});

test('Quarantine: a batch that fails repeatedly is set aside and replay drains the rest', async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-userdata-'));
  const syncRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-syncroot-'));
  // Mocked dispatcher: fail every dispatch for 'a.txt' (the poison path) but
  // succeed for everything else. After MAX_BATCH_ATTEMPTS=8 the poison batch
  // should be quarantined and replay should advance to 'b.txt'.
  const dispatched: string[] = [];
  const dispatchFileEventBatch = async (args: DispatchFileEventBatchArgs) => {
    const paths = (args.events || []).map((e: WatchEvent) => e.path);
    if (paths.includes('a.txt')) {
      throw new Error('simulated permanent 403 for a.txt');
    }
    dispatched.push(...paths);
    return null;
  };
  const app = { getPath: () => userData };
  const manager = new LocalSyncManager({ app, dispatchFileEventBatch });

  markFullSyncSeen(manager, 'c-quar', syncRoot);
  await manager.start({
    connectorId: 'c-quar',
    connectorName: 'Quarantine Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });

  // Seed the journal directly with two pending batches so replay sees them in
  // order. Batch order matters: a.txt (poison) before b.txt; without quarantine
  // logic the b.txt batch never replays because the loop halts on the first failure.
  manager.journal.appendBatch('c-quar', {
    batchId: 'poison',
    timestamp: Date.now(),
    events: [{ type: 'CREATED', path: 'a.txt', timestamp: Date.now(), isDirectory: false }],
  });
  manager.journal.appendBatch('c-quar', {
    batchId: 'good',
    timestamp: Date.now(),
    events: [{ type: 'CREATED', path: 'b.txt', timestamp: Date.now(), isDirectory: false }],
  });

  // Re-run replay until the poison is quarantined. Cap attempts at 12 to bound
  // the test; MAX_BATCH_ATTEMPTS is 8 in the production code.
  for (let i = 0; i < 12; i += 1) {
    try { await manager.replay('c-quar'); } catch { /* expected while poison is still 'failed' */ }
    const all = manager.journal.listBatches('c-quar');
    const poison = all.find((b) => b.batchId === 'poison');
    if (poison && poison.status === 'quarantined') break;
  }

  const all = manager.journal.listBatches('c-quar');
  const poison = all.find((b) => b.batchId === 'poison');
  const good = all.find((b) => b.batchId === 'good');

  assert.equal(poison?.status, 'quarantined', `poison should be quarantined, got ${poison?.status}`);
  assert.equal(good?.status, 'synced', `good should drain past quarantined poison, got ${good?.status}`);
  assert.deepEqual(dispatched, ['b.txt'], `expected only b.txt dispatched, got ${JSON.stringify(dispatched)}`);

  await manager.shutdown();
  fs.rmSync(userData, { recursive: true, force: true });
  fs.rmSync(syncRoot, { recursive: true, force: true });
});

test('Single-flight scheduled tick: concurrent calls coalesce', async () => {
  const { manager, syncRoot } = setup();
  await manager.start({
    connectorId: 'c-singleflight',
    connectorName: 'Single-flight',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 60 },
  });
  await sleep(700);

  // Fire two ticks at the same microtask. Implementation contract: same
  // promise returned, only one rescan/replay actually executes.
  const t1 = manager.runScheduledTick('c-singleflight');
  const t2 = manager.runScheduledTick('c-singleflight');
  assert.strictEqual(t1, t2, 'concurrent runScheduledTick calls must return the same in-flight promise');

  await Promise.all([t1, t2]);
  await manager.stop('c-singleflight');
});

test('REPLACE/replay serialization: opChain prevents concurrent execution', async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-userdata-'));
  const syncRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-syncroot-'));
  // Slow dispatch: hold each call for 80ms. Track the in-flight count to
  // detect any concurrent overlap.
  let inFlight = 0;
  let maxObserved = 0;
  const dispatchFileEventBatch = async () => {
    inFlight += 1;
    if (inFlight > maxObserved) maxObserved = inFlight;
    await sleep(80);
    inFlight -= 1;
    return null;
  };
  const manager = new LocalSyncManager({
    app: { getPath: () => userData },
    dispatchFileEventBatch,
  });

  await fsp.writeFile(path.join(syncRoot, 'x.txt'), 'x');
  markFullSyncSeen(manager, 'c-serial', syncRoot);
  await manager.start({
    connectorId: 'c-serial',
    connectorName: 'Serial Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });

  // Seed one pending batch so replay has work to do. The runtime token from
  // start() is required; credentials are not persisted in journal metadata.
  manager.journal.appendBatch('c-serial', {
    batchId: 'live-1',
    timestamp: Date.now(),
    events: [{ type: 'CREATED', path: 'x.txt', timestamp: Date.now(), isDirectory: false }],
  });
  // Kick off replace and replay simultaneously. The opChain must serialize
  // them — at no point should both be in dispatch at the same time.
  const fs1 = manager.triggerBackendFullSync('c-serial');
  const r1 = manager.replay('c-serial');
  await Promise.all([fs1, r1]);

  assert.equal(maxObserved, 1, `replay and full-sync ran concurrently (observed=${maxObserved})`);

  await manager.shutdown();
  fs.rmSync(userData, { recursive: true, force: true });
  fs.rmSync(syncRoot, { recursive: true, force: true });
});

test('Shutdown: live events drained on close are journaled, not dispatched', async () => {
  const { manager, dispatched, syncRoot } = setup();
  await manager.start({
    connectorId: 'c-shutdown',
    connectorName: 'Shutdown Test',
    rootPath: syncRoot,
    apiBaseUrl: API_BASE,
    accessToken: TOKEN,
    syncStrategy: 'MANUAL',
  });
  await sleep(700);

  await fsp.writeFile(path.join(syncRoot, 'late.txt'), 'late');
  // Sleep into the window where chokidar (~1500ms awaitWriteFinish) + the
  // correlator (~250ms) have already pushed the event into the dispatcher
  // buffer, but before the dispatcher's 1000ms flush timer would have fired
  // an actual dispatch. Then shutdown — the drain inside watcher.stop must
  // hit the journal-only fast path, not the network, otherwise app quit
  // would block on a 30s dispatch timeout.
  await sleep(1900);
  await manager.shutdown();

  // Live dispatches have resetBeforeApply=false. REPLACE full-sync (from
  // start) has resetBeforeApply=true and runs against an empty disk so its
  // events are []. Either way, no `late.txt` should appear in any dispatched
  // batch — it should only live in the journal until the next session.
  const liveDispatchedLate = dispatched
    .filter((d) => !d.resetBeforeApply)
    .flatMap((d) => d.events || [])
    .filter((e) => e.path === 'late.txt');
  assert.equal(
    liveDispatchedLate.length,
    0,
    `late.txt must not be live-dispatched on shutdown; got ${JSON.stringify(liveDispatchedLate)}`,
  );

  // The drained event landed in the journal so the next token-backed start()
  // can replay it (or the REPLACE full-sync from disk re-uploads it).
  const journaled = manager.journal.listBatches('c-shutdown');
  const hasLate = journaled.some(
    (b) => (b.events || []).some((e: WatchEvent) => e.path === 'late.txt'),
  );
  assert.ok(hasLate, `late.txt should be persisted in journal, got ${JSON.stringify(journaled)}`);
});

test('Duplicate root path: one local folder is watched by at most one connector', async () => {
  const { manager, syncRoot } = setup();

  const starts = await Promise.allSettled([
    manager.start({
      connectorId: 'c-one',
      connectorName: 'First Local FS',
      rootPath: syncRoot,
      apiBaseUrl: API_BASE,
      accessToken: TOKEN,
      syncStrategy: 'MANUAL',
    }),
    manager.start({
      connectorId: 'c-two',
      connectorName: 'Second Local FS',
      rootPath: path.join(syncRoot, '.'),
      apiBaseUrl: API_BASE,
      accessToken: TOKEN,
      syncStrategy: 'MANUAL',
    }),
  ]);

  assert.equal(starts.filter((result) => result.status === 'fulfilled').length, 1);
  assert.equal(starts.filter((result) => result.status === 'rejected').length, 1);
  const rejected = starts.find((result) => result.status === 'rejected') as PromiseRejectedResult;
  assert.match(String(rejected.reason?.message || ''), /already watched/);

  const statuses = manager.getStatus() as Array<{ watcherState: string }>;
  assert.equal(
    statuses.filter((s) => s.watcherState === 'watching' || s.watcherState === 'starting').length,
    1,
    `expected only one active watcher, got ${JSON.stringify(statuses)}`,
  );

  await manager.shutdown();
});
