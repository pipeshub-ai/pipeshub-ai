// Stub the `electron` module before requiring the manager. Manager imports
// safeStorage at top-level for token encryption; outside the Electron runtime
// that would throw. Keeping it disabled forces the raw token path.
import Module = require('module');
const _origLoad = (Module as unknown as { _load: (req: string, parent: unknown, isMain: unknown) => unknown })._load;
(Module as unknown as { _load: (req: string, parent: unknown, isMain: unknown) => unknown })._load = function patchedLoad(
  request: string,
  parent: unknown,
  isMain: unknown,
): unknown {
  if (request === 'electron') {
    return { safeStorage: { isEncryptionAvailable: () => false } };
  }
  return _origLoad.call(Module, request, parent, isMain);
};

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
  // init's full-sync is fire-and-forget; wait for it to finish.
  await sleep(1500);

  const fullSync = d2.find((d) => d.resetBeforeApply === true);
  assert.ok(
    fullSync,
    `expected a full-sync dispatch with resetBeforeApply=true, got ${JSON.stringify(d2)}`,
  );
  const paths = (fullSync!.events || []).map((e) => e.path);
  assert.ok(paths.includes('b.txt'), 'b.txt should be in full-sync (created while closed)');
  assert.ok(!paths.includes('a.txt'), 'a.txt should NOT be in full-sync (deleted while closed)');
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

  // Reopen — init's full-sync brings backend to current disk state.
  const { manager: m2, dispatched: d2 } = reopen(userData);
  await m2.init();
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

  // Seed the journal directly with two pending batches so replay sees them in
  // order. Batch order matters: a.txt (poison) before b.txt; without quarantine
  // logic the b.txt batch never replays because the loop halts on the first
  // failure. accessToken is needed or _replayInner short-circuits.
  manager.journal.setMeta('c-quar', {
    apiBaseUrl: API_BASE, rootPath: syncRoot, accessToken: TOKEN,
  });
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

  // Seed meta + token + one pending batch so replay has work to do. Without
  // a token both replay() and triggerBackendFullSync short-circuit before
  // dispatch and the test observes nothing.
  manager.journal.setMeta('c-serial', {
    apiBaseUrl: API_BASE, rootPath: syncRoot, accessToken: TOKEN,
  });
  manager.journal.appendBatch('c-serial', {
    batchId: 'live-1',
    timestamp: Date.now(),
    events: [{ type: 'CREATED', path: 'x.txt', timestamp: Date.now(), isDirectory: false }],
  });
  await fsp.writeFile(path.join(syncRoot, 'x.txt'), 'x');

  // Kick off replace and replay simultaneously. The opChain must serialize
  // them — at no point should both be in dispatch at the same time.
  const fs1 = manager.triggerBackendFullSync('c-serial', { mode: 'replace' });
  const r1 = manager.replay('c-serial');
  await Promise.all([fs1, r1]);

  assert.equal(maxObserved, 1, `replay and full-sync ran concurrently (observed=${maxObserved})`);

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

  // The drained event landed in the journal so next session's init() replays
  // (or the REPLACE full-sync from disk re-uploads it).
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
