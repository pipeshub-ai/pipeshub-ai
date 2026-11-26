/**
 * BGUI-11: a job's files are classified once, in the activity fold. A file it
 * published as an artifact is delivered; every other changed file is a working
 * file. The activity view (the panel's Outputs tab) and the end-of-run summary
 * (the report under the card) read that one classification, so their counts
 * agree, and a file a commit and a diff line both name is counted once.
 */
import { expect } from 'chai';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { deliveredPaths } from '../../../../src/modules/agent-harness/projection/job-files';
import { foldRunDigest } from '../../../../src/modules/agent-harness/projection/run-digest';
import type { ArtifactView } from '../../../../src/modules/agent-harness/projection/timeline';
import { ActivityJournal } from '../support/activity-journal';

const WORKING = [
  'notes/progress.md',
  'make_report.py',
  'layout_check/ipl_2026_report/page-1.png',
  'layout_check/ipl_2026_report/page-2.png',
  'layout_check/ipl_2026_report/page-3.png',
  'layout_check/ipl_2026_report/page-4.png',
];

/** A job: one PDF delivered under out/, six working files, each named by a commit and by a diff line. */
function iplJob(): ActivityJournal {
  const journal = new ActivityJournal('run-ipl', 'ses-ipl', 'org-ipl');
  journal.engine('started', 'Create a PDF report on IPL 2026');
  for (const path of [...WORKING, 'out/ipl_2026_report.pdf']) {
    journal.engine('diff', path, { change: 'added', diff: '+x', truncated: false });
  }
  journal.committed([...WORKING, 'out/ipl_2026_report.pdf'].map((p) => `/${p}`));
  journal.add('x.pipeshub.artifact.presented', {
    artifactId: 'art-ipl',
    fileName: 'ipl_2026_report.pdf',
    recordId: 'rec-ipl',
    mimeType: 'application/pdf',
    sizeBytes: 48_213,
    artifactType: 'DOCUMENT',
    version: 1,
    isTemporary: false,
    lifecycle: 'final',
  });
  journal.engine('finished', 'Done.', { success: true });
  return journal.completed();
}

const artifact = (fileName: string, patch: Partial<ArtifactView> = {}): ArtifactView => ({
  artifactId: `art-${fileName}`,
  fileName,
  mimeType: 'application/pdf',
  sizeBytes: 1,
  artifactType: 'DOCUMENT',
  isTemporary: false,
  visibility: 'VISIBLE',
  card: true,
  ...patch,
});

describe('BGUI-11 delivered and working files', () => {
  it('one file a commit (`/out/x`) and a diff line (`out/x`) both name is one file', () => {
    const journal = iplJob();
    const view = foldActivity(journal.events, { runId: journal.runId, runStatus: 'completed' });
    expect(view.files.map((f) => f.path)).to.deep.equal([...WORKING, 'out/ipl_2026_report.pdf']);
  });

  it('the published PDF is delivered; notes, the script and the page renders are working files', () => {
    const journal = iplJob();
    const view = foldActivity(journal.events, { runId: journal.runId, runStatus: 'completed' });
    expect(view.files.filter((f) => f.delivered).map((f) => f.path)).to.deep.equal(['out/ipl_2026_report.pdf']);
    expect(view.files.filter((f) => !f.delivered)).to.have.length(WORKING.length);
  });

  it('the summary counts what the activity view lists, and carries what the file card shows', () => {
    const journal = iplJob();
    const view = foldActivity(journal.events, { runId: journal.runId, runStatus: 'completed' });
    const digest = foldRunDigest(journal.events, journal.runId, 'completed', null);
    expect(digest.files).to.deep.equal({ added: 7, modified: 0, deleted: 0, working: 6 });
    expect(digest.files.working).to.equal(view.files.filter((f) => !f.delivered).length);
    expect(digest.artifacts).to.deep.equal([
      { name: 'ipl_2026_report.pdf', recordId: 'rec-ipl', mimeType: 'application/pdf', sizeBytes: 48_213, artifactType: 'DOCUMENT', version: 1 },
    ]);
  });

  it('of two paths ending with the artifact\'s name, the shallowest is the published one', () => {
    const paths = ['notes/drafts/report.pdf', 'out/report.pdf', 'out/charts/q3.png', 'scratch/q3.png'];
    expect([...deliveredPaths(paths, [artifact('report.pdf'), artifact('charts/q3.png')])]).to.deep.equal([
      'out/report.pdf',
      'out/charts/q3.png',
    ]);
  });

  it('a discarded draft or a temporary file delivers nothing', () => {
    const paths = ['out/a.pdf', 'out/b.csv'];
    expect([
      ...deliveredPaths(paths, [artifact('a.pdf', { lifecycle: 'discarded' }), artifact('b.csv', { isTemporary: true })]),
    ]).to.deep.equal([]);
  });
});
