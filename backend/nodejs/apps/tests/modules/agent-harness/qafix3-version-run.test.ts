/**
 * QAFIX3 W3: a workspace version names the run that wrote it, so a `/run` that changed no files offers no Undo
 * (the chat shows Undo only under a run that has a version). A person's upload or restore names no run.
 */
import { bufferedUploadFile } from '../../../src/modules/agent-harness/application/session-workspace-service';
import 'reflect-metadata';
import { expect } from 'chai';
import { scope, workspaceWorld } from './support/slash2-workspace-world';

describe('QAFIX3 W3 the run that wrote a workspace version', () => {
  it('a run names itself; an upload and a restore name no run', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'one\n' });
    await w.owner.upload(scope, {
      baseRevision: 1,
      idempotencyKey: 'up-1',
      files: [bufferedUploadFile('b.txt', Buffer.from('b'), 'text/plain')],
      replace: false,
    });
    await w.owner.restore(scope, { revision: 1, baseRevision: 2, idempotencyKey: 'undo-1' });
    const versions = (await w.owner.versions(scope, {})).versions;
    expect(versions.map((v) => [v.revision, v.reason, v.runId ?? null])).to.deep.equal([
      [3, 'rollback', null],
      [2, 'upload', null],
      [1, 'turn_end', 'run-a'],
    ]);
  });
});
