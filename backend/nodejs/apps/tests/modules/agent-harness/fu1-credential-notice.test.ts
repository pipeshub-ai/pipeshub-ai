/**
 * QA23-F1: a durable run goes on without a toolset or MCP server its person has not connected, and the worker
 * journals one `x.pipeshub.run.notice` `credential_missing` per item, naming it, so the answer says what to connect.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { journalOnlySteps, project } from '../../contracts/support/durable';

const RUN = 'run-1';
const JIRA = { code: 'credential_missing', item: 'Jira', kind: 'toolset' };
const GITHUB = { code: 'credential_missing', item: 'GitHub', kind: 'mcp' };

function journal(...notices: Array<Record<string, unknown>>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ...notices.map((data): [string, Record<string, unknown>, 'worker'] => ['x.pipeshub.run.notice', data, 'worker']),
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Connect Jira.', partTexts: ['Connect Jira.'], citations: [] }, 'worker'],
    ['run.completed', { outcome: 'answered' }, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: '2026-10-16T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const append = (data: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    runId: RUN,
    writer: 'worker',
    events: [{ type: 'x.pipeshub.run.notice', schemaVersion: 1, critical: false, visibility: 'owner', data }],
  } as never);

describe('QA23-F1: the notice naming an item the person has not connected', () => {
  it('the worker may journal it with the item and its kind; an unknown kind is refused', () => {
    expect(append(JIRA)).to.not.throw();
    expect(append(GITHUB)).to.not.throw();
    expect(append({ ...JIRA, kind: 'connector' })).to.throw();
  });

  it('the answer carries each item once, saved and live', () => {
    const events = journal(JIRA, GITHUB, JIRA);
    const parts = buildParts(foldTimeline(events, RUN)).filter((part) => part['type'] === 'notice');
    expect(parts).to.deep.equal([
      { type: 'notice', ...JIRA, runId: RUN },
      { type: 'notice', ...GITHUB, runId: RUN },
    ]);
    const journalled = { scenario: 'fu1', query: 'q', runId: RUN, sessionId: 'ses-1', events, live: [] };
    const frames = project(journalled, journalOnlySteps(journalled)).map((f) => f.frame);
    expect(frames.filter((f) => f['name'] === 'notice').map((f) => f['value'])).to.deep.equal([JIRA, GITHUB]);
  });
});
