import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  openLocalFsRecordSource,
  resolveRecordSourcePath,
} from '../open-record-source';

function withTempDir(run: (dir: string) => void | Promise<void>): Promise<void> | void {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'local-sync-open-'));
  const cleanup = () => fs.rmSync(dir, { recursive: true, force: true });
  const result = run(dir);
  if (result && typeof result.then === 'function') {
    return result.finally(cleanup);
  }
  cleanup();
}

test('resolveRecordSourcePath resolves a relative path under the connector root', () => {
  withTempDir((dir) => {
    const root = path.join(dir, 'root');
    fs.mkdirSync(path.join(root, 'docs'), { recursive: true });
    const target = path.join(root, 'docs', 'notes.txt');
    fs.writeFileSync(target, 'hello');

    const result = resolveRecordSourcePath(root, {
      connectorId: 'connector-1',
      localFsRelativePath: 'docs/notes.txt',
    });

    assert.equal(result.ok, true);
    assert.equal(result.ok ? result.path : '', fs.realpathSync.native(target));
  });
});

test('resolveRecordSourcePath rejects relative traversal outside the connector root', () => {
  withTempDir((dir) => {
    const root = path.join(dir, 'root');
    fs.mkdirSync(root, { recursive: true });

    const result = resolveRecordSourcePath(root, {
      connectorId: 'connector-1',
      localFsRelativePath: '../secret.txt',
    });

    assert.equal(result.ok, false);
    assert.equal(result.ok ? '' : result.code, 'PATH_OUTSIDE_ROOT');
  });
});

test('resolveRecordSourcePath rejects absolute paths outside the connector root', () => {
  withTempDir((dir) => {
    const root = path.join(dir, 'root');
    const outside = path.join(dir, 'outside.txt');
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(outside, 'nope');

    const result = resolveRecordSourcePath(root, {
      connectorId: 'connector-1',
      absolutePath: outside,
    });

    assert.equal(result.ok, false);
    assert.equal(result.ok ? '' : result.code, 'PATH_OUTSIDE_ROOT');
  });
});

test('resolveRecordSourcePath accepts absolute paths inside the connector root', () => {
  withTempDir((dir) => {
    const root = path.join(dir, 'root');
    const target = path.join(root, 'nested', 'inside.txt');
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, 'yes');

    const result = resolveRecordSourcePath(root, {
      connectorId: 'connector-1',
      absolutePath: target,
    });

    assert.equal(result.ok, true);
    assert.equal(result.ok ? result.path : '', fs.realpathSync.native(target));
  });
});

test('openLocalFsRecordSource reveals a valid file in the native file manager', async () => {
  await withTempDir(async (dir) => {
    const root = path.join(dir, 'root');
    const target = path.join(root, 'nested', 'inside.txt');
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, 'yes');

    const revealed: string[] = [];
    const result = await openLocalFsRecordSource(
      {
        connectorId: 'connector-1',
        localFsRelativePath: 'nested/inside.txt',
      },
      {
        getMeta: () => ({ connectorId: 'connector-1', rootPath: root }),
        showItemInFolder: (itemPath) => {
          revealed.push(itemPath);
        },
        openPath: async () => '',
      },
    );

    assert.deepEqual(revealed, [fs.realpathSync.native(target)]);
    assert.equal(result.ok, true);
    assert.equal(result.ok ? result.action : '', 'show-item');
  });
});
