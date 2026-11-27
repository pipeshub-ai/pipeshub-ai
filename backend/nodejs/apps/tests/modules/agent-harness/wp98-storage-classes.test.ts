/**
 * WP98-NODE-1..4 (ADR-RUN-01 D2): the person's file views show their files only. A fetched repository is recorded by
 * the worker as address and version in a store-only record; `/files` names it on its own line ("fetched copy, not
 * saved") and never lists the record, `/open`, `/download` and `/diff` never serve it, and an upload cannot write it.
 * The record's shape is pinned with Python by contracts/harness/vectors/storage-record.json.
 */
import { bufferedUploadFile } from '../../../src/modules/agent-harness/application/session-workspace-service';
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  decodeStorageRecord,
  isStorageRecordPath,
  referenceLabel,
  STORAGE_RECORD_PATH,
  WorkspaceReference,
} from '../../../src/modules/agent-harness/domain/storage-record';
import { workspaceCode } from './support/workspace-repository-contract';
import { scope, workspaceWorld } from './support/slash2-workspace-world';

interface Vectors {
  path: string;
  record: string;
  references: WorkspaceReference[];
  unreadable: string[];
}

const vectors = JSON.parse(
  readFileSync(
    path.resolve(__dirname, '../../../../../../contracts/harness/vectors/storage-record.json'),
    'utf8',
  ),
) as Vectors;

describe('WP-98 storage classes: references in the person file views', () => {
  it('WP98-NODE-1: reads the record Python writes, and nothing it cannot read', () => {
    expect(STORAGE_RECORD_PATH).to.equal(vectors.path);
    expect(decodeStorageRecord(Buffer.from(vectors.record))).to.deep.equal(vectors.references);
    for (const bad of vectors.unreadable) {
      expect(decodeStorageRecord(Buffer.from(bad)), bad).to.deep.equal([]);
    }
    expect(decodeStorageRecord(Buffer.alloc(64 * 1024 + 1, 32))).to.deep.equal([]);
    expect(referenceLabel('https://github.com/onyx-dot-app/onyx.git')).to.equal('onyx-dot-app/onyx');
    expect(isStorageRecordPath('.pipeshub/references.json')).to.equal(true);
    expect(isStorageRecordPath('.pipeshubx/a')).to.equal(false);
  });

  it('WP98-NODE-2: /files lists the person files and names each reference on its own line', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), {
      'out/report.md': '# Report',
      'notes.md': 'notes',
      [STORAGE_RECORD_PATH]: vectors.record,
    });
    const all = await w.owner.files(scope, {});
    expect(all.files.map((f) => f.path)).to.deep.equal(['notes.md', 'out/report.md']);
    expect(all.totalBytes).to.equal('# Report'.length + 'notes'.length);
    expect(all.references).to.deep.equal(vectors.references);
    // A folder holding only a reference is found, with the reference and no files.
    const repos = await w.owner.files(scope, { prefix: 'repos/onyx' });
    expect(repos.files).to.deep.equal([]);
    expect(repos.references?.map((r) => r.path)).to.deep.equal(['repos/onyx']);
    // A folder without one carries no reference line.
    expect((await w.owner.files(scope, { prefix: 'out' })).references).to.equal(undefined);
  });

  it('WP98-NODE-3: the record is never opened, downloaded or shown as a change', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'a', [STORAGE_RECORD_PATH]: vectors.record });
    expect(await workspaceCode(w.owner.file(scope, { path: STORAGE_RECORD_PATH }))).to.equal(
      'WORKSPACE_PATH_NOT_FOUND',
    );
    const zip = await w.owner.archive(scope, {});
    expect(zip.entries.map((e) => e.path)).to.deep.equal(['a.txt']);
    const diff = await w.owner.diff(scope, 'chat');
    expect(diff.files.map((f) => f.path)).to.deep.equal(['a.txt']);
  });

  it('WP98-NODE-4: an upload cannot write the record', async () => {
    const w = workspaceWorld();
    const refused = await workspaceCode(
      w.owner.upload(scope, {
        baseRevision: 0,
        idempotencyKey: 'up-1',
        folder: '.pipeshub',
        files: [bufferedUploadFile('references.json', Buffer.from('{}'), 'application/json')],
        replace: false,
      }),
    );
    expect(refused).to.equal('WORKSPACE_INVALID_CHANGE');
  });
});
