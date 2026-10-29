import test from 'node:test';
import * as assert from 'node:assert';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import {
  carryForwardUnreadable,
  emptyUnreadable,
  scanSyncRoot,
  WatcherStateStore,
} from '../persistence/watcher-state-store';

const posixNonRoot = process.platform !== 'win32' && process.getuid?.() !== 0;

async function tree(): Promise<string> {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-'));
  await fsp.mkdir(path.join(root, 'locked', 'deeper'), { recursive: true });
  await fsp.writeFile(path.join(root, 'locked', 'a.txt'), 'a');
  await fsp.writeFile(path.join(root, 'locked', 'deeper', 'b.txt'), 'b');
  await fsp.writeFile(path.join(root, 'open.txt'), 'open');
  return root;
}

test('the scan reports a folder it could not list', { skip: !posixNonRoot }, async () => {
  const root = await tree();
  fs.chmodSync(path.join(root, 'locked'), 0);
  const unreadable = emptyUnreadable();
  const scan = await scanSyncRoot(root, { unreadable });
  assert.deepEqual([...unreadable.dirs], ['locked']);
  assert.ok(scan.has('locked'), 'the folder itself is still listed by its parent');
  assert.ok(!scan.has('locked/a.txt'));
  fs.chmodSync(path.join(root, 'locked'), 0o755);
  fs.rmSync(root, { recursive: true, force: true });
});

test('what could not be read is carried forward from the snapshot, not dropped', { skip: !posixNonRoot }, async () => {
  const root = await tree();
  const before = await scanSyncRoot(root, {});
  fs.chmodSync(path.join(root, 'locked'), 0);
  fs.chmodSync(path.join(root, 'open.txt'), 0);
  const unreadable = emptyUnreadable();
  const scan = carryForwardUnreadable(await scanSyncRoot(root, { unreadable }), unreadable,
    Object.fromEntries(before));
  for (const rel of ['locked/a.txt', 'locked/deeper', 'locked/deeper/b.txt']) {
    assert.deepEqual(scan.get(rel), before.get(rel), `${rel} must keep its last known entry`);
  }
  assert.equal(scan.get('open.txt')?.sha256, before.get('open.txt')?.sha256, 'an unhashable file keeps its hash');
  fs.chmodSync(path.join(root, 'locked'), 0o755);
  fs.chmodSync(path.join(root, 'open.txt'), 0o644);
  fs.rmSync(root, { recursive: true, force: true });
});

test('reconcile does not report the files of an unreadable folder as deleted', { skip: !posixNonRoot }, async () => {
  const root = await tree();
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-state-'));
  const store = new WatcherStateStore({ baseDir: base, syncRoot: root, connectorInstanceId: 'c-1' });
  store.applyScan(await scanSyncRoot(root, {}));
  fs.chmodSync(path.join(root, 'locked'), 0);
  const unreadable = emptyUnreadable();
  const scan = carryForwardUnreadable(await scanSyncRoot(root, { unreadable }), unreadable,
    store.getSnapshot().files);
  const events = store.reconcile(scan);
  assert.deepEqual(events.filter((e) => e.type.endsWith('DELETED')), []);
  fs.chmodSync(path.join(root, 'locked'), 0o755);
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(base, { recursive: true, force: true });
});

test('a folder that is really gone is still reported deleted', async () => {
  const root = await tree();
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-state-'));
  const store = new WatcherStateStore({ baseDir: base, syncRoot: root, connectorInstanceId: 'c-1' });
  store.applyScan(await scanSyncRoot(root, {}));
  fs.rmSync(path.join(root, 'locked'), { recursive: true });
  const unreadable = emptyUnreadable();
  const scan = carryForwardUnreadable(await scanSyncRoot(root, { unreadable }), unreadable,
    store.getSnapshot().files);
  const deleted = store.reconcile(scan).filter((e) => e.type.endsWith('DELETED')).map((e) => e.path).sort();
  assert.deepEqual(deleted, ['locked', 'locked/a.txt', 'locked/deeper', 'locked/deeper/b.txt']);
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(base, { recursive: true, force: true });
});

test('a file edited while unreadable keeps no hash, so the edit is reported', () => {
  const known = { inode: 7, size: 4, mtimeMs: 1000, isDirectory: false, sha256: 'ab'.repeat(32) };
  const unreadable = emptyUnreadable();
  unreadable.files.add('x.txt');
  const edited = carryForwardUnreadable(
    new Map([['x.txt', { ...known, mtimeMs: 2000, sha256: undefined }]]), unreadable, { 'x.txt': known });
  assert.equal(edited.get('x.txt')?.sha256, undefined);
  const untouched = carryForwardUnreadable(
    new Map([['x.txt', { ...known, sha256: undefined }]]), unreadable, { 'x.txt': known });
  assert.equal(untouched.get('x.txt')?.sha256, known.sha256);

  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-'));
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-state-'));
  const store = new WatcherStateStore({ baseDir: base, syncRoot: root, connectorInstanceId: 'c-1' });
  store.applyScan(new Map([['x.txt', known]]));
  const events = store.reconcile(edited);
  assert.deepEqual(events.map((e) => [e.type, e.path, Boolean(e.sha256)]), [['MODIFIED', 'x.txt', false]]);
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(base, { recursive: true, force: true });
});

test('a file first reported without a hash is reported again once it can be read', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-'));
  await fsp.writeFile(path.join(root, 'x.txt'), 'x');
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-state-'));
  const store = new WatcherStateStore({ baseDir: base, syncRoot: root, connectorInstanceId: 'c-1' });
  const first = await scanSyncRoot(root, {});
  const entry = first.get('x.txt')!;
  store.applyScan(new Map([['x.txt', { ...entry, sha256: undefined }]]));
  const events = store.reconcile(await scanSyncRoot(root, {}));
  assert.deepEqual(events.map((e) => [e.type, e.path, Boolean(e.sha256)]), [['MODIFIED', 'x.txt', true]]);
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(base, { recursive: true, force: true });
});

test('the periodic rescan refreshes what it knows and absorbs no unreported change', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-'));
  await fsp.writeFile(path.join(root, 'same.txt'), 'same');
  await fsp.writeFile(path.join(root, 'edited.txt'), 'v1');
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-unreadable-state-'));
  const store = new WatcherStateStore({ baseDir: base, syncRoot: root, connectorInstanceId: 'c-1' });
  store.applyScan(await scanSyncRoot(root, {}));
  const before = JSON.parse(JSON.stringify(store.getSnapshot().files));
  await fsp.writeFile(path.join(root, 'edited.txt'), 'version two');
  await fsp.writeFile(path.join(root, 'new.txt'), 'new');
  await fsp.unlink(path.join(root, 'same.txt'));
  store.refreshUnchanged(await scanSyncRoot(root, {}));
  const after = store.getSnapshot().files;
  assert.equal(after['edited.txt'].sha256, before['edited.txt'].sha256, 'an unreported edit must stay unreported');
  assert.equal(after['new.txt'], undefined, 'an unreported file must not be absorbed');
  assert.ok(after['same.txt'], 'an unreported delete must not be absorbed');
  // So the next start still sees all three.
  const events = store.reconcile(await scanSyncRoot(root, {}));
  assert.deepEqual(events.map((e) => `${e.type}:${e.path}`).sort(),
    ['CREATED:new.txt', 'DELETED:same.txt', 'MODIFIED:edited.txt']);
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(base, { recursive: true, force: true });
});
