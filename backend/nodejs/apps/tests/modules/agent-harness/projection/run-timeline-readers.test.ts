/**
 * The run timeline's readers for callers that already authorized the reader
 * (P2.11e's approval queue): the whole
 * journal, read page by page, folded as the timeline folds it.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { RunTimelineService } from '../../../../src/modules/agent-harness/application/run-timeline-service';
import { MAX_JOURNAL_PAGE } from '../../../../src/modules/agent-harness/application/execution-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';

const RUN = 'run-1';
const IX = 'ix.approve.k0123456789abcdef0123456789abcdef01234567';
const run = { orgId: 'org-1', runId: RUN, sessionId: 'ses-1' } as HarnessRun;

const journalOf = (entries: Array<[string, Record<string, unknown>]>): JournalEvent[] =>
  entries.map(
    ([type, data], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: `2026-10-04T12:00:00.${String(index).padStart(3, '0')}Z`,
        writer: 'worker',
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );

const serviceOver = (events: JournalEvent[]) => {
  const readRun = sinon
    .stub()
    .callsFake(async (_org: string, _run: string, after: number, limit: number) =>
      events.filter((e) => e.seq > after).slice(0, limit),
    );
  const resolve = sinon.stub().callsFake(async (all: JournalEvent[]) => all);
  const service = new RunTimelineService({
    runs: { streamAccess: sinon.stub().rejects(new Error('not used')) },
    journal: { readRun },
    journals: { resolve },
    workspaces: null,
    ledger: null,
  });
  return { service, readRun, resolve };
};

describe("run timeline readers (approval queue, unknown effects)", () => {
  it('folds approvals over the whole journal, past the first page', async () => {
    const filler = Array.from(
      { length: MAX_JOURNAL_PAGE },
      (): [string, Record<string, unknown>] => ['x.pipeshub.noise', {}],
    );
    const events = journalOf([
      ...filler,
      [
        'x.pipeshub.engine.progress',
        {
          tool: 'code_task',
          taskId: RUN,
          kind: 'approval',
          text: 'create_issue',
          data: { interactionId: IX, toolName: 'create_issue', argsJson: '{"title":"Q3"}' },
        },
      ],
      ['interaction.requested', { interactionId: IX, kind: 'approval' }],
      ['interaction.resolved', { interactionId: IX, decision: 'denied' }],
    ]);
    const { service, readRun, resolve } = serviceOver(events);

    const approvals = await service.approvals(run);

    expect(readRun.callCount).to.equal(2);
    expect(readRun.secondCall.args.slice(0, 3)).to.deep.equal(['org-1', RUN, MAX_JOURNAL_PAGE]);
    expect(resolve.firstCall.args[0]).to.have.length(events.length);
    expect(approvals).to.have.length(1);
    expect(approvals[0]).to.deep.include({
      interactionId: IX,
      toolName: 'create_issue',
      args: '{"title":"Q3"}',
      status: 'denied',
    });
  });

  it('lists parked unknown effects with the reconciler verdict, and marks resolved ones', async () => {
    const { service } = serviceOver(
      journalOf([
        ['tool.reconciled', { invocationId: 't1.0', verdict: 'still_unknown', reason: 'no read', lookups: 2 }],
        ['interaction.requested', { interactionId: 'ix.unknown.t1.0', kind: 'unknown_effect' }],
        ['interaction.requested', { interactionId: 'ix.unknown.t2.0', kind: 'unknown_effect' }],
        ['interaction.resolved', { interactionId: 'ix.unknown.t2.0', decision: 'applied' }],
      ]),
    );

    const effects = await service.unknownEffects(run);

    expect(effects.map((e) => [e.invocationId, e.status])).to.deep.equal([
      ['t1.0', 'open'],
      ['t2.0', 'resolved'],
    ]);
    expect(effects[0]).to.deep.include({
      toolName: 'tool',
      reconcile: { verdict: 'still_unknown', reason: 'no read', lookups: 2 },
    });
    expect(effects[1]).to.not.have.property('reconcile');
  });
});
