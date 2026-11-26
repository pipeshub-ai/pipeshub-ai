/**
 * GA-05 (G-07, C20.08): every terminated run has an end-of-run summary, folded
 * from its journal alone (actions, records touched, artifacts, approvals,
 * cost, outcome), on the run's timeline and on a scheduled run's notice.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { RunTimelineService } from '../../../../src/modules/agent-harness/application/run-timeline-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { digestLine, usdText } from '../../../../src/modules/agent-harness/domain/run-end-notice';
import { foldRunDigest, MAX_DIGEST_ARTIFACTS } from '../../../../src/modules/agent-harness/projection/run-digest';
import { ActivityJournal, activityScenario } from '../support/activity-journal';

const COST = { usdMicros: 41_500, tokens: 12_300 };

const cited = (recordId: string) => ({ content: 'x', chunkIndex: 0, metadata: { recordId, connector: 'DRIVE' } });

/** AP-1's run, answered: it saved a report (and a temporary file) and cited two records, one twice. */
function withAnswer(journal: ActivityJournal): ActivityJournal {
  journal.add('x.pipeshub.artifact.presented', {
    artifactId: 'art-1',
    fileName: 'report.pdf',
    recordId: 'rec-report',
    isTemporary: false,
  });
  journal.add('x.pipeshub.artifact.presented', {
    artifactId: 'art-2',
    fileName: 'scratch.csv',
    isTemporary: true,
  });
  journal.add('x.pipeshub.answer.finalized', {
    outcome: 'answered',
    answer: 'Done.',
    citations: [cited('rec-1'), cited('rec-2'), cited('rec-1')],
  });
  return journal.completed();
}

const serviceOver = (events: JournalEvent[], committed = COST) =>
  new RunTimelineService({
    runs: {
      streamAccess: sinon.stub().callsFake(async (_scope: unknown, runId: string) => ({
        run: { orgId: 'org-ux5', runId, sessionId: 'ses-ux5', status: 'completed' } as HarnessRun,
        access: { audience: 'owner' },
      })),
    },
    journal: {
      readRun: sinon.stub().callsFake(async (_o: string, _r: string, after: number) => events.filter((e) => e.seq > after)),
    },
    journals: { resolve: sinon.stub().callsFake(async (all: JournalEvent[]) => all) },
    workspaces: null,
    ledger: {
      account: sinon.stub().resolves({ committed, cap: { usdMicros: null, tokens: null } }),
      remaining: sinon.stub().resolves({
        committed,
        reserved: { usdMicros: 0, tokens: 0 },
        available: { usdMicros: null, tokens: null },
        exhausted: false,
      }),
    } as never,
  });

describe('GA-05 end-of-run summary (G-07)', () => {
  it('GA-05: a completed run is summarized from its journal: actions, records, artifacts, approvals, cost, outcome', () => {
    const journal = withAnswer(activityScenario());
    const digest = foldRunDigest(journal.events, journal.runId, 'completed', COST);
    expect(digest.outcome).to.equal('answered');
    expect(digest.actions).to.deep.include({ total: 4, failed: 0, refused: 2 });
    expect(digest.actions.tools.map((t) => [t.name, t.count])).to.deep.equal([
      ['run_code', 1],
      ['slack__send_message', 1],
      ['drive__delete_file', 1],
      ['web__post', 1],
    ]);
    // AP-1's fake journal declares both of its completed calls `write`; the next test separates `execute`.
    expect(digest.records).to.deep.equal({ read: 2, changed: 2 });
    expect(digest.artifacts, 'a temporary file is not one the run saved').to.deep.equal([
      { name: 'report.pdf', recordId: 'rec-report' },
    ]);
    expect(digest.approvals).to.deep.equal({ asked: 2, approved: 1, denied: 1, unanswered: 0 });
    expect(digest.commands).to.deep.equal({ total: 3, failed: 1 });
    expect(digest.files).to.deep.equal({ added: 0, modified: 2, deleted: 0, working: 2 });
    expect(digest.cost).to.deep.equal(COST);
    expect(digest.questions).to.deep.equal({ asked: 0, open: false });
    expect(digest.durationMs).to.be.a('number').and.to.be.greaterThan(0);
  });

  it('GA-05: only a `write` that succeeded is a change: an `execute` (code in the sandbox) or a failed write is not', () => {
    const journal = new ActivityJournal()
      .turn(1, [
        { callId: 'c-1', name: 'jira__create_issue', args: {} },
        { callId: 'c-2', name: 'run_code', args: { code: '1' } },
        { callId: 'c-3', name: 'jira__create_issue', args: {} },
      ]);
    journal.add('tool.intent', { invocationId: 't1.0', toolId: 'jira@default/create_issue', effect: 'write' });
    journal.outcome('t1.0');
    journal.add('tool.intent', { invocationId: 't1.1', toolId: 'pipeshub@coding_sandbox/run_code', effect: 'execute' });
    journal.outcome('t1.1');
    journal.add('tool.intent', { invocationId: 't1.2', toolId: 'jira@default/create_issue', effect: 'write' });
    journal.outcome('t1.2', 'error');
    journal.completed();
    const digest = foldRunDigest(journal.events, journal.runId, 'completed', null);
    expect(digest.records.changed).to.equal(1);
    expect(digest.actions).to.deep.include({ total: 3, failed: 1 });
    expect(digest.cost).to.equal(null);
  });

  it('GA-05, GA-02: a dry run\'s shadowed write changed nothing, so the summary counts no change', () => {
    const journal = new ActivityJournal().turn(1, [{ callId: 'c-1', name: 'jira__create_issue', args: {} }]);
    journal.add('tool.intent', { invocationId: 't1.0', toolId: 'jira@default/create_issue', effect: 'write' });
    journal.add('tool.outcome', { invocationId: 't1.0', status: 'ok', shadow: { wouldAsk: true } });
    journal.completed();
    const digest = foldRunDigest(journal.events, journal.runId, 'completed', null);
    expect(digest.records.changed).to.equal(0);
  });

  it('GA-05: a failed run and a cancelled run each have a summary; an approval nobody answered is counted', () => {
    const failed = new ActivityJournal()
      .turn(1, [{ callId: 'c-send', name: 'slack__send_message', args: {} }])
      .held('ix.approve.t1.0', { rule: 'flow_external_write', sources: [], moreSources: 0, conversationGrant: false })
      .failed();
    const digest = foldRunDigest(failed.events, failed.runId, 'failed', null);
    expect(digest.outcome).to.equal('failed');
    expect(digest.approvals.unanswered).to.equal(1);

    const cancelled = new ActivityJournal().turn(1, [{ callId: 'c-1', name: 'search', args: {} }]);
    cancelled.add('run.cancelled', {}, 'node');
    expect(foldRunDigest(cancelled.events, cancelled.runId, 'cancelled', null).outcome).to.equal('cancelled');
    // No terminal event journaled (a run the sweeper ended): the run's status decides.
    const bare = new ActivityJournal();
    expect(foldRunDigest(bare.events, bare.runId, 'failed', null).outcome).to.equal('failed');
  });

  it('GA-05: a run that ended asking the person says so; a cut-short run names its limit', () => {
    const journal = new ActivityJournal();
    journal.add('x.pipeshub.question.asked', { callId: 'q-1', status: 'success', toolData: {} });
    journal.add('run.completed', { outcome: 'needs_input', truncatedBy: 'turns' }, 'node');
    const digest = foldRunDigest(journal.events, journal.runId, 'completed', null);
    expect(digest).to.deep.include({ outcome: 'needs_input', questions: { asked: 1, open: true } });
    expect(digest.cutShortBy).to.equal('turns');
  });

  it('GA-05: at most the first files are listed, each once', () => {
    const journal = new ActivityJournal();
    for (let i = 0; i < MAX_DIGEST_ARTIFACTS + 3; i += 1) {
      for (let twice = 0; twice < 2; twice += 1) {
        journal.add('x.pipeshub.artifact.presented', { artifactId: `a-${String(i)}`, fileName: `f${String(i)}.md`, recordId: `r-${String(i)}` });
      }
    }
    journal.completed();
    const digest = foldRunDigest(journal.events, journal.runId, 'completed', null);
    expect(digest.artifacts).to.have.length(MAX_DIGEST_ARTIFACTS);
    expect(digest.artifacts[0]).to.deep.equal({ name: 'f0.md', recordId: 'r-0' });
  });

  it("GA-05: the run's timeline carries the summary once it ended, with the ledger's committed spend", async () => {
    const journal = withAnswer(activityScenario());
    const service = serviceOver(journal.events);
    const view = await service.timeline({ orgId: 'org-ux5', sessionId: 'ses-ux5', actorId: 'u-1' } as never, journal.runId);
    expect(view.digest).to.deep.include({ outcome: 'answered', cost: COST });
    expect(view.digest!.records).to.deep.equal({ read: 2, changed: 2 });
    // The reader a schedule's notice uses gives the same summary.
    expect(await service.digest('org-ux5', journal.runId, 'completed')).to.deep.equal(view.digest);
  });

  it('GA-05: a run still going has no summary yet', async () => {
    const journal = activityScenario();
    const service = new RunTimelineService({
      runs: {
        streamAccess: sinon.stub().resolves({
          run: { orgId: 'org-ux5', runId: journal.runId, sessionId: 'ses-ux5', status: 'running' } as HarnessRun,
          access: { audience: 'owner' },
        }),
      },
      journal: { readRun: sinon.stub().resolves(journal.events) },
      journals: { resolve: sinon.stub().callsFake(async (all: JournalEvent[]) => all) },
      workspaces: null,
      ledger: null,
    });
    const view = await service.timeline({ orgId: 'org-ux5', sessionId: 'ses-ux5', actorId: 'u-1' } as never, journal.runId);
    expect(view).not.to.have.property('digest');
  });

  it('GA-05: the notice line is counts in plain words, never an id or a record name', () => {
    const journal = withAnswer(activityScenario());
    const line = digestLine(foldRunDigest(journal.events, journal.runId, 'completed', COST));
    expect(line).to.equal(
      'Used 4 tools, ran 3 commands, read 2 records, made 2 changes, changed 2 files, saved 1 file, asked 2 approvals (1 approved, 1 denied), cost USD 0.04.',
    );
    expect(line).not.to.match(/rec-|t1\.|ix\.approve|report\.pdf/);
    expect(digestLine(foldRunDigest([], 'r', 'completed', null))).to.equal('');
    expect(usdText(2_000)).to.equal('under USD 0.01');
  });
});
