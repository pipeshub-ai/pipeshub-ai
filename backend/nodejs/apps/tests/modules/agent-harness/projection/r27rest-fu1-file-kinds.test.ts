/**
 * R27REST-FU-1: the activity drawer's Files changed said "Modified" for every
 * file a job's merge brought into the conversation, the new ones included,
 * because `workspace.committed` named only paths. It now carries each path's
 * kind (`kinds`); the fold reads it, and an event written before reads as it did.
 */
import { expect } from 'chai';
import { changeKinds } from '../../../../src/modules/agent-harness/domain/workspace';
import { foldActivity, laterChange } from '../../../../src/modules/agent-harness/projection/activity';
import { ActivityJournal } from '../support/activity-journal';

const files = (journal: ActivityJournal) =>
  foldActivity(journal.events, { runId: journal.runId, runStatus: 'completed' }).files.map(({ path, change }) => ({
    path,
    change,
  }));

describe('R27REST-FU-1 a commit says how it changed each file', () => {
  it('FU1-N1: the files a job merge added read "added", the one it changed "modified"', () => {
    const journal = new ActivityJournal('run-parent').committed(
      ['/out/q3-churn.csv', '/notes/handoff.json', '/report.md'],
      4,
      { '/out/q3-churn.csv': 'added', '/notes/handoff.json': 'added', '/report.md': 'modified' },
    );
    expect(files(journal)).to.have.deep.members([
      { path: 'out/q3-churn.csv', change: 'added' },
      { path: 'notes/handoff.json', change: 'added' },
      { path: 'report.md', change: 'modified' },
    ]);
  });

  it('FU1-N2: an event written before kinds, or a path it names no kind for, reads "modified" as before', () => {
    const journal = new ActivityJournal()
      .committed(['/old.md'], 1)
      .committed(['/a.md', '/b.md'], 2, { '/b.md': 'deleted' });
    expect(files(journal)).to.have.deep.members([
      { path: 'old.md', change: 'modified' },
      { path: 'a.md', change: 'modified' },
      { path: 'b.md', change: 'deleted' },
    ]);
  });

  it('FU1-N3: across commits a file keeps what the run did to it since it started', () => {
    const journal = new ActivityJournal()
      .committed(['/new.md', '/temp.md', '/old.md'], 1, { '/new.md': 'added', '/temp.md': 'added', '/old.md': 'deleted' })
      .committed(['/new.md', '/temp.md', '/old.md'], 2, { '/new.md': 'modified', '/temp.md': 'deleted', '/old.md': 'added' });
    expect(files(journal)).to.have.deep.members([
      { path: 'new.md', change: 'added' },
      { path: 'old.md', change: 'modified' },
    ]);
  });

  it('FU1-N4: an engine line still names its own kind and keeps its diff', () => {
    const journal = new ActivityJournal()
      .engine('diff', 'src/app.py', { change: 'added', diff: '+x', truncated: false })
      .committed(['/src/app.py'], 1, { '/src/app.py': 'added' });
    const [file] = foldActivity(journal.events, { runId: journal.runId, runStatus: 'completed' }).files;
    expect(file).to.deep.include({ path: 'src/app.py', change: 'added', diff: '+x' });
  });

  it('FU1-N5: laterChange composes every pair', () => {
    expect(laterChange('added', 'modified')).to.equal('added');
    expect(laterChange('added', 'added')).to.equal('added');
    expect(laterChange('added', 'deleted')).to.equal(null);
    expect(laterChange('modified', 'modified')).to.equal('modified');
    expect(laterChange('modified', 'deleted')).to.equal('deleted');
    expect(laterChange('deleted', 'added')).to.equal('modified');
    expect(laterChange('deleted', 'deleted')).to.equal('deleted');
  });

  it('FU1-N6: changeKinds names a change set against the files it changes (Python: ChangeSet.kinds)', () => {
    const entry = (path: string) => ({
      path,
      objectId: `obj-${path}`,
      contentHash: `hmac-sha256:${path}`,
      sizeBytes: 1,
      mimeType: 'text/plain',
    });
    const before = new Map([
      ['kept', entry('kept')],
      ['gone', entry('gone')],
    ]);
    expect(changeKinds({ upserts: [entry('kept'), entry('new.csv')], deletes: ['gone'] }, before)).to.deep.equal({
      gone: 'deleted',
      kept: 'modified',
      'new.csv': 'added',
    });
  });
});
