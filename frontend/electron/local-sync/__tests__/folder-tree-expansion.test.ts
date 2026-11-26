import test from 'node:test';
import * as assert from 'node:assert';
import { expandWatchEventsForReplay, type FileStateMap } from '../watcher/replay-event-expander';

const files: FileStateMap = {
  docs: { isDirectory: true, mtimeMs: 1 },
  'docs/plan.md': { isDirectory: false, sha256: 'p', size: 1, mtimeMs: 1 },
  'docs/specs': { isDirectory: true, mtimeMs: 1 },
  'docs/specs/api.txt': { isDirectory: false, sha256: 'a', size: 1, mtimeMs: 1 },
  'docs/specs/deep': { isDirectory: true, mtimeMs: 1 },
  'docs-other/x.txt': { isDirectory: false, sha256: 'x', size: 1, mtimeMs: 1 },
};

test('a folder move moves its subfolders, parents first, then its files', async () => {
  const events = await expandWatchEventsForReplay(
    [{ type: 'DIR_MOVED', path: 'archive/docs', oldPath: 'docs', timestamp: 1, isDirectory: true }],
    files,
  );
  assert.deepEqual(events.map((e) => [e.type, e.oldPath, e.path]), [
    ['DIR_MOVED', 'docs', 'archive/docs'],
    ['DIR_MOVED', 'docs/specs', 'archive/docs/specs'],
    ['DIR_MOVED', 'docs/specs/deep', 'archive/docs/specs/deep'],
    ['MOVED', 'docs/plan.md', 'archive/docs/plan.md'],
    ['MOVED', 'docs/specs/api.txt', 'archive/docs/specs/api.txt'],
  ]);
});

test('a folder delete deletes its subfolders, and nothing with a similar name', async () => {
  const events = await expandWatchEventsForReplay(
    [{ type: 'DIR_DELETED', path: 'docs', timestamp: 1, isDirectory: true }],
    files,
  );
  assert.deepEqual(events.map((e) => [e.type, e.path]), [
    ['DIR_DELETED', 'docs'],
    ['DIR_DELETED', 'docs/specs'],
    ['DIR_DELETED', 'docs/specs/deep'],
    ['DELETED', 'docs/plan.md'],
    ['DELETED', 'docs/specs/api.txt'],
  ]);
});
