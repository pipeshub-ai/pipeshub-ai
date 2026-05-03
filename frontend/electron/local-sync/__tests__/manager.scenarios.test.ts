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
