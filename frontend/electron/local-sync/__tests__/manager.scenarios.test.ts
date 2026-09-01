import test from 'node:test';
import * as assert from 'node:assert';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';
import * as os from 'os';
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
const ALL_CHANGE_FINAL_PATHS = [
  'created.txt',
  'existing.txt',
  'nested/move-to.txt',
  'rename-to.txt',
].sort();

function setup() {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-userdata-'));
  const syncRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-syncroot-'));
  const manager = new LocalSyncManager({ app: { getPath: () => userData } });
  return { manager, syncRoot, userData };
}

function reopen(userData: string) {
  return new LocalSyncManager({ app: { getPath: () => userData } });
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

function pullArgs(overrides: Partial<ServePullRequest> = {}): ServePullRequest {
  return {
    connectorId: 'c-1',
    runId: 'run-1',
    batchIndex: 0,
    mode: 'INCREMENTAL',
    cursor: null,
    maxEvents: 50,
    ...overrides,
  };
}

function countEvents(events: WatchEvent[], match: (e: WatchEvent) => boolean): number {
  return events.filter(match).length;
}

/** A move must not also be described as a delete of the old path or a create of
 * the new one — that pair is what mints a second record server-side. */
function assertNoStrayChildEvents(events: WatchEvent[], oldPath: string, newPath: string): void {
  assert.equal(
    countEvents(events, (e) => e.type === 'DELETED' && e.path === oldPath),
    0,
    `moved child ${oldPath} must not also be reported as DELETED, got ${JSON.stringify(events)}`,
  );
  assert.equal(
    countEvents(events, (e) => e.type === 'CREATED' && e.path === newPath),
    0,
    `moved child ${newPath} must not also be reported as CREATED, got ${JSON.stringify(events)}`,
  );
}

function asPage(response: ServePullResponse, message: string): ServePullPage {
  assert.equal(response.ok, true, `${message}: expected a page, got ${JSON.stringify(response)}`);
  return response as ServePullPage;
}

/** Drain every incremental page of a run, acking each by asking for the next. */
async function drainRun(
  manager: LocalSyncManager,
  connectorId: string,
  runId: string,
  mode: 'FULL' | 'INCREMENTAL' = 'INCREMENTAL',
  maxEvents = 50,
): Promise<{ events: WatchEvent[]; cursor: string | null; pages: number }> {
  const events: WatchEvent[] = [];
  let cursor: string | null = null;
  let batchIndex = 0;
  let pages = 0;
  for (;;) {
    const page = asPage(
      await manager.servePull(pullArgs({ connectorId, runId, batchIndex, mode, cursor, maxEvents })),
      `drainRun page ${batchIndex}`,
    );
    events.push(...page.events);
    cursor = page.cursor;
    pages += 1;
    batchIndex += 1;
    if (!page.hasMore) break;
    assert.ok(batchIndex < 500, 'drainRun did not terminate');
  }
  return { events, cursor, pages };
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

test('Live edits reach the server only when it pulls', async () => {
  const { manager, syncRoot } = setup();
  await manager.start({ connectorId: 'c-1', connectorName: 'Live', rootPath: syncRoot });
  await sleep(700); // chokidar 'ready'

  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'hello');
  await sleep(2500); // correlator (250ms) + awaitWriteFinish (1500ms) + dispatcher (1000ms)

  const { events } = await drainRun(manager, 'c-1', 'run-1');
  assert.equal(
    events.filter((e) => e.type === 'CREATED' && e.path === 'a.txt').length,
    1,
    `expected exactly one CREATED a.txt, got ${JSON.stringify(events)}`,
  );

  await manager.shutdown();
});

test('Create, content change, rename, move and delete all reach one pull', async () => {
  const { manager, syncRoot } = setup();
  await seedAllChangeFiles(syncRoot);
  await manager.start({ connectorId: 'c-1', connectorName: 'All', rootPath: syncRoot });
  await sleep(1000);

  await applyAllChangeTypes(syncRoot);
  await sleep(3500);

  const { events } = await drainRun(manager, 'c-1', 'run-1');
  assertAllLiveChangeEvents(events, 'incremental pull');

  await manager.shutdown();
});

test('Directory rename expands to the folder and its children', async () => {
  const { manager, syncRoot } = setup();
  const docDir = path.join(syncRoot, 'doc');
  await fsp.mkdir(docDir, { recursive: true });
  await fsp.writeFile(path.join(docDir, 'note.txt'), 'hello');

  await manager.start({ connectorId: 'c-1', connectorName: 'Dir rename', rootPath: syncRoot });
  await sleep(1000);
  // The seed batch from first start covers the pre-rename state; drop it so the
  // assertion below is about the rename alone.
  await drainRun(manager, 'c-1', 'seed-run');

  await fsp.rename(docDir, path.join(syncRoot, 'docs'));
  await sleep(3500);

  const { events } = await drainRun(manager, 'c-1', 'rename-run');
  // Exact counts, not `some`: the child is described twice by the OS (once by
  // the directory event we expand, once by its own unlink/add) and both
  // descriptions used to reach the backend as separate records.
  assert.equal(
    countEvents(events, (e) => e.type === 'DIR_RENAMED' && e.oldPath === 'doc' && e.path === 'docs' && e.isDirectory),
    1,
    `expected exactly one DIR_RENAMED doc -> docs, got ${JSON.stringify(events)}`,
  );
  assert.equal(
    countEvents(events, (e) => e.type === 'RENAMED' && e.oldPath === 'doc/note.txt' && e.path === 'docs/note.txt'),
    1,
    `expected exactly one child RENAMED doc/note.txt -> docs/note.txt, got ${JSON.stringify(events)}`,
  );
  assertNoStrayChildEvents(events, 'doc/note.txt', 'docs/note.txt');

  await manager.shutdown();
});

test('Moving a populated directory reports each child exactly once', async () => {
  const { manager, syncRoot } = setup();
  const photos = path.join(syncRoot, 'photos');
  await fsp.mkdir(photos, { recursive: true });
  await fsp.writeFile(path.join(photos, 'a.jpg'), 'aaa');
  await fsp.writeFile(path.join(photos, 'b.jpg'), 'bbb');
  await fsp.mkdir(path.join(syncRoot, 'album'), { recursive: true });

  await manager.start({ connectorId: 'c-1', connectorName: 'Dir move', rootPath: syncRoot });
  await sleep(1000);
  await drainRun(manager, 'c-1', 'seed-run');

  await fsp.rename(photos, path.join(syncRoot, 'album', 'photos'));
  // Long enough for the children's own unlink/add to arrive and be dropped —
  // not merely long enough for the directory event itself.
  await sleep(4000);

  const { events } = await drainRun(manager, 'c-1', 'move-run');
  assert.equal(
    countEvents(events, (e) => e.isDirectory && e.oldPath === 'photos' && e.path === 'album/photos'),
    1,
    `expected exactly one directory move event, got ${JSON.stringify(events)}`,
  );
  for (const name of ['a.jpg', 'b.jpg']) {
    assert.equal(
      countEvents(events, (e) => e.path === `album/photos/${name}` && e.oldPath === `photos/${name}`),
      1,
      `expected exactly one move event for ${name}, got ${JSON.stringify(events)}`,
    );
    assertNoStrayChildEvents(events, `photos/${name}`, `album/photos/${name}`);
  }

  await manager.shutdown();
});

test('A file created inside a just-moved directory is still reported', async () => {
  const { manager, syncRoot } = setup();
  const docDir = path.join(syncRoot, 'doc');
  await fsp.mkdir(docDir, { recursive: true });
  await fsp.writeFile(path.join(docDir, 'note.txt'), 'hello');

  await manager.start({ connectorId: 'c-1', connectorName: 'Dir move + create', rootPath: syncRoot });
  await sleep(1000);
  await drainRun(manager, 'c-1', 'seed-run');

  await fsp.rename(docDir, path.join(syncRoot, 'docs'));
  // Inside the move's suppression window: only the children carried over by the
  // move may be dropped, never a genuinely new file that lands next to them.
  await fsp.writeFile(path.join(syncRoot, 'docs', 'fresh.txt'), 'brand new');
  await sleep(4000);

  const { events } = await drainRun(manager, 'c-1', 'move-run');
  assert.equal(
    countEvents(events, (e) => e.type === 'CREATED' && e.path === 'docs/fresh.txt'),
    1,
    `expected the new file to survive the move window, got ${JSON.stringify(events)}`,
  );

  await manager.shutdown();
});

test('First-ever start seeds pre-existing files, which the first pull delivers', async () => {
  const { manager, syncRoot } = setup();
  await seedAllChangeFiles(syncRoot);

  await manager.start({ connectorId: 'c-1', connectorName: 'Seed', rootPath: syncRoot });
  await sleep(700);

  const { events } = await drainRun(manager, 'c-1', 'run-1');
  const created = events.filter((e) => e.type === 'CREATED').map((e) => e.path).sort();
  assert.deepEqual(
    created,
    ['delete-me.txt', 'existing.txt', 'move-from.txt', 'rename-from.txt'],
    `expected every pre-existing file seeded as CREATED, got ${JSON.stringify(events)}`,
  );

  await manager.shutdown();
});

test('A repeated (runId, batchIndex) returns the identical page and does not advance', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await fsp.writeFile(path.join(syncRoot, 'b.txt'), 'b');
  await manager.start({ connectorId: 'c-1', connectorName: 'Idempotent', rootPath: syncRoot });
  await sleep(700);

  const first = asPage(await manager.servePull(pullArgs({ maxEvents: 1 })), 'first');
  const retry = asPage(await manager.servePull(pullArgs({ maxEvents: 1 })), 'retry');

  assert.deepEqual(retry, first, 'a re-sent index must answer byte-identically');
  // And still nothing committed: the ack is the *next* index arriving.
  assert.equal(
    manager.journal.listBatches('c-1').every((b) => b.status !== 'synced'),
    true,
    'a served page must not be marked synced',
  );

  await manager.shutdown();
});

test('A batch is only marked synced once the next pull arrives past it', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await manager.start({ connectorId: 'c-1', connectorName: 'Commit', rootPath: syncRoot });
  await sleep(700);

  const first = asPage(await manager.servePull(pullArgs()), 'first page');
  assert.ok(first.events.length > 0, 'expected the seed batch on page 0');
  assert.ok(first.cursor, 'expected a cursor after serving events');
  assert.deepEqual(
    manager.journal.listBatches('c-1').map((b) => b.status),
    ['pending'],
    'serving must not commit',
  );

  await manager.servePull(pullArgs({ batchIndex: 1, cursor: first.cursor }));
  assert.deepEqual(
    manager.journal.listBatches('c-1').map((b) => b.status),
    ['synced'],
    'the next pull past the batch is its ack',
  );

  await manager.shutdown();
});

test('An unresolvable cursor answers CURSOR_UNKNOWN rather than guessing', async () => {
  const { manager, syncRoot } = setup();
  await manager.start({ connectorId: 'c-1', connectorName: 'Cursor', rootPath: syncRoot });
  await sleep(700);

  const unknown = await manager.servePull(
    pullArgs({ cursor: encodeCursor({ v: 1, mode: 'INCREMENTAL', afterBatchId: 'nope' }) }),
  );
  assert.equal(unknown.ok, false);
  assert.equal((unknown as { error: { code: string } }).error.code, 'CURSOR_UNKNOWN');

  // A FULL token handed to an incremental run is equally unresolvable — the two
  // position spaces are not interchangeable.
  const wrongMode = await manager.servePull(
    pullArgs({ runId: 'run-2', cursor: encodeCursor({ v: 1, mode: 'FULL', afterPath: 'x' }) }),
  );
  assert.equal(wrongMode.ok, false);
  assert.equal((wrongMode as { error: { code: string } }).error.code, 'CURSOR_UNKNOWN');

  await manager.shutdown();
});

test('A superseded run is told STALE_RUN instead of resurrecting', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await manager.start({ connectorId: 'c-1', connectorName: 'Stale', rootPath: syncRoot });
  await sleep(700);

  await manager.servePull(pullArgs({ runId: 'run-old' }));
  await manager.servePull(pullArgs({ runId: 'run-new' }));

  const straggler = await manager.servePull(pullArgs({ runId: 'run-old', batchIndex: 1 }));
  assert.equal(straggler.ok, false);
  assert.equal((straggler as { error: { code: string } }).error.code, 'STALE_RUN');

  await manager.shutdown();
});

test('A quiet connector still answers one page with hasMore false', async () => {
  const { manager, syncRoot } = setup();
  await manager.start({ connectorId: 'c-1', connectorName: 'Quiet', rootPath: syncRoot });
  await sleep(700);

  const page = asPage(await manager.servePull(pullArgs()), 'quiet page');
  assert.deepEqual(page.events, []);
  assert.equal(page.hasMore, false, 'a quiet tick must terminate, not block until timeout');

  await manager.shutdown();
});

test('FULL: the walk is served in sorted path order and resumes at afterPath', async () => {
  const { manager, syncRoot } = setup();
  await fsp.mkdir(path.join(syncRoot, 'nested'), { recursive: true });
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await fsp.writeFile(path.join(syncRoot, 'm.txt'), 'm');
  await fsp.writeFile(path.join(syncRoot, 'nested', 'z.txt'), 'z');
  await manager.start({ connectorId: 'c-1', connectorName: 'Full', rootPath: syncRoot });
  await sleep(700);

  const first = asPage(
    await manager.servePull(pullArgs({ mode: 'FULL', maxEvents: 2 })),
    'full page 0',
  );
  assert.equal(first.events.length, 2);
  assert.equal(first.hasMore, true);
  const firstPaths = first.events.map((e) => e.path);
  assert.deepEqual(firstPaths, [...firstPaths].sort(), 'walk must be in sorted path order');

  const resumeToken = decodeCursor(first.cursor, 'FULL');
  assert.ok(resumeToken, 'a mid-walk page must carry a FULL cursor');
  assert.equal(
    (resumeToken as { afterPath: string }).afterPath,
    firstPaths[firstPaths.length - 1],
    'cursor names the last path served',
  );

  const second = asPage(
    await manager.servePull(
      pullArgs({ mode: 'FULL', batchIndex: 1, cursor: first.cursor, maxEvents: 2 }),
    ),
    'full page 1',
  );
  assert.ok(
    second.events.every((e) => !firstPaths.includes(e.path)),
    `page 1 must resume past page 0, got ${JSON.stringify(second.events)}`,
  );

  await manager.shutdown();
});

test('FULL: the whole disk snapshot is delivered exactly once', async () => {
  const { manager, syncRoot } = setup();
  await seedAllChangeFiles(syncRoot);
  await applyAllChangeTypes(syncRoot);
  await manager.start({ connectorId: 'c-1', connectorName: 'Full all', rootPath: syncRoot });
  await sleep(700);

  const { events } = await drainRun(manager, 'c-1', 'full-run', 'FULL', 2);
  const files = events.filter((e) => !e.isDirectory).map((e) => e.path).sort();
  assert.deepEqual(files, ALL_CHANGE_FINAL_PATHS, 'full walk must match current disk state');
  assert.ok(
    events.every((e) => e.type === 'CREATED' || e.type === 'DIR_CREATED'),
    `full walk should express the snapshot as creations, got ${JSON.stringify(events)}`,
  );
  assert.equal(new Set(files).size, files.length, 'no path may appear twice in one walk');

  await manager.shutdown();
});

test('FULL: the final page hands off an incremental cursor, so the next run is not FULL again', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await manager.start({ connectorId: 'c-1', connectorName: 'Handoff', rootPath: syncRoot });
  await sleep(700);

  const { cursor } = await drainRun(manager, 'c-1', 'full-run', 'FULL', 50);
  assert.ok(cursor, 'a non-empty journal must yield a handoff cursor');
  assert.equal(
    decodeCursor(cursor, 'FULL'),
    null,
    'the handoff cursor must not be a FULL token',
  );
  assert.ok(
    decodeCursor(cursor, 'INCREMENTAL'),
    'the handoff cursor must resolve as an incremental position',
  );

  // And the connector's next run, which is INCREMENTAL, accepts it.
  const next = asPage(
    await manager.servePull(pullArgs({ runId: 'next-run', cursor })),
    'incremental run after a full one',
  );
  assert.equal(next.hasMore, false);

  await manager.shutdown();
});

test('FULL: a resumed walk this process no longer holds answers CURSOR_UNKNOWN', async () => {
  const { manager, syncRoot, userData } = setup();
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await manager.start({ connectorId: 'c-1', connectorName: 'Lost walk', rootPath: syncRoot });
  await sleep(700);
  await manager.shutdown();

  const m2 = reopen(userData);
  const response = await m2.servePull(
    pullArgs({ mode: 'FULL', batchIndex: 3, cursor: encodeCursor({ v: 1, mode: 'FULL', afterPath: 'a.txt' }) }),
  );
  assert.equal(response.ok, false);
  assert.equal((response as { error: { code: string } }).error.code, 'CURSOR_UNKNOWN');

  await m2.shutdown();
});

test('Close then reopen: offline create and delete surface on the next pull', async () => {
  const { manager, syncRoot, userData } = setup();
  await manager.start({ connectorId: 'c-1', connectorName: 'Reopen', rootPath: syncRoot });
  await sleep(700);
  await fsp.writeFile(path.join(syncRoot, 'a.txt'), 'a');
  await sleep(2500);
  await drainRun(manager, 'c-1', 'run-before');
  await manager.shutdown();

  await fsp.writeFile(path.join(syncRoot, 'b.txt'), 'b');
  await fsp.unlink(path.join(syncRoot, 'a.txt'));

  const m2 = reopen(userData);
  await m2.bootstrapFromJournal();
  await sleep(500);

  const { events } = await drainRun(m2, 'c-1', 'run-after');
  assert.ok(
    events.some((e) => e.type === 'CREATED' && e.path === 'b.txt'),
    `expected reconcile CREATED for b.txt, got ${JSON.stringify(events)}`,
  );
  assert.ok(
    events.some((e) => e.type === 'DELETED' && e.path === 'a.txt'),
    `expected reconcile DELETED for a.txt, got ${JSON.stringify(events)}`,
  );

  await m2.shutdown();
});

test('A pull with nothing mounted lazily mounts the watcher', async () => {
  const { manager, syncRoot, userData } = setup();
  await manager.start({ connectorId: 'c-1', connectorName: 'Lazy', rootPath: syncRoot });
  await sleep(700);
  await manager.shutdown();

  await fsp.writeFile(path.join(syncRoot, 'offline.txt'), 'offline');

  // Fresh process, nothing bootstrapped: servePull is the only thing that can
  // bring the watcher up, and it has to before it can find anything.
  const m2 = reopen(userData);
  assert.equal((m2.getStatus('c-1') as { watcherState: string }).watcherState, 'stopped');

  const { events } = await drainRun(m2, 'c-1', 'run-1');
  assert.equal((m2.getStatus('c-1') as { watcherState: string }).watcherState, 'watching');
  assert.ok(
    events.some((e) => e.type === 'CREATED' && e.path === 'offline.txt'),
    `expected the lazily mounted watcher to reconcile offline.txt, got ${JSON.stringify(events)}`,
  );

  await m2.shutdown();
});

test('An unconfigured connector answers CONFIG_MISMATCH, not an empty sync', async () => {
  const { manager } = setup();
  const response = await manager.servePull(pullArgs({ connectorId: 'never-seen' }));
  assert.equal(response.ok, false);
  assert.equal((response as { error: { code: string } }).error.code, 'CONFIG_MISMATCH');
  assert.equal((response as { error: { retryable: boolean } }).error.retryable, false);
});

test('start() called twice with unchanged config is a no-op', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'existing.txt'), 'hello');
  const startArgs = { connectorId: 'c-1', connectorName: 'Revisit', rootPath: syncRoot };

  await manager.start(startArgs);
  await sleep(700);
  const afterFirst = manager.journal.listBatches('c-1').length;
  await manager.start(startArgs);
  await manager.start(startArgs);
  await sleep(300);

  assert.equal(
    manager.journal.listBatches('c-1').length,
    afterFirst,
    'a repeated start must not remount and re-seed the journal',
  );

  await manager.shutdown();
});

test('Duplicate root path: one local folder is watched by at most one connector', async () => {
  const { manager, syncRoot } = setup();

  const starts = await Promise.allSettled([
    manager.start({ connectorId: 'c-one', connectorName: 'First Local FS', rootPath: syncRoot }),
    manager.start({
      connectorId: 'c-two',
      connectorName: 'Second Local FS',
      rootPath: path.join(syncRoot, '.'),
    }),
  ]);

  assert.equal(starts.filter((result) => result.status === 'fulfilled').length, 1);
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

test('A deleted connector does not come back on the next launch and frees its root', async () => {
  const { manager, syncRoot, userData } = setup();
  await manager.start({ connectorId: 'c-deleted', connectorName: 'T1', rootPath: syncRoot });
  await sleep(700);
  await manager.shutdown();

  // Reopen and reap against the backend's list, which no longer has it.
  const m2 = reopen(userData);
  await m2.bootstrapFromJournal();
  assert.equal(
    (m2.getStatus('c-deleted') as { watcherState: string }).watcherState,
    'watching',
    'precondition: the journal remounts every connector it knows about',
  );

  const removed = await m2.reap([]);
  assert.deepEqual(removed, ['c-deleted']);
  assert.deepEqual(m2.listConnectorIds(), [], 'journal meta must be gone');

  // The freed root is now available to a different connector — this is the
  // failure users hit: "already watched by connector T1" after deleting T1.
  await m2.start({ connectorId: 'c-new', connectorName: 'T2', rootPath: syncRoot });
  assert.equal((m2.getStatus('c-new') as { watcherState: string }).watcherState, 'watching');

  // And a relaunch must not resurrect it.
  await m2.shutdown();
  const m3 = reopen(userData);
  await m3.bootstrapFromJournal();
  assert.deepEqual(m3.listConnectorIds(), ['c-new']);

  await m3.shutdown();
});

test('reap keeps a connector that still exists but is switched off', async () => {
  const { manager, syncRoot } = setup();
  await fsp.writeFile(path.join(syncRoot, 'pending.txt'), 'unsynced');
  await manager.start({ connectorId: 'c-off', connectorName: 'Toggled off', rootPath: syncRoot });
  await sleep(700);
  await manager.stop('c-off');

  // Deactivating is not deleting: its pending events must survive.
  const removed = await manager.reap(['c-off']);
  assert.deepEqual(removed, []);
  assert.deepEqual(manager.listConnectorIds(), ['c-off']);
  assert.ok(manager.journal.listBatches('c-off').length > 0, 'journal must be intact');

  await manager.shutdown();
});

test('A watcher that fails to start does not hold the sync root', async () => {
  const { manager, userData } = setup();
  const missing = path.join(userData, 'no-such-folder');

  await assert.rejects(
    () => manager.start({ connectorId: 'c-broken', connectorName: 'Broken', rootPath: missing }),
    /does not exist/,
  );

  // The failed runtime must not linger and block the retry that would work.
  fs.mkdirSync(missing, { recursive: true });
  await manager.start({ connectorId: 'c-broken', connectorName: 'Broken', rootPath: missing });
  assert.equal((manager.getStatus('c-broken') as { watcherState: string }).watcherState, 'watching');

  await manager.shutdown();
});

test('listConnectorIds only reports connectors with a configured folder', async () => {
  const { manager, syncRoot } = setup();
  await manager.start({ connectorId: 'c-configured', connectorName: 'Configured', rootPath: syncRoot });
  manager.journal.setMeta('c-bare', { connectorName: 'No folder yet' });

  assert.deepEqual(manager.listConnectorIds(), ['c-configured']);

  await manager.shutdown();
});
