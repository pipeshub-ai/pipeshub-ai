import { expect } from 'chai';
import { foldActivity, stepDetail } from '../../../../src/modules/agent-harness/projection/activity';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
import { ActivityJournal } from '../support/activity-journal';
import { without } from '../support/without';

/**
 * ACTRESULTS: the activity panel's snapshot carries each step's line (what it asked for, its outcome, its time,
 * how its approval was answered) and a fingerprint of its detail; the detail itself (arguments and result) is what
 * the chat's own step shows, from the same function that builds it, read once when the step is opened. A snapshot,
 * sent on every journal advance, never carries a result.
 */

const RUN = 'run-ar';
const DETAIL = ['args', 'argsSummary', 'resultPreview', 'resultSummary', 'resultView', 'artifactId'] as const;
const LONG = 'x'.repeat(2_500);
const VIEW = {
  kind: 'records',
  columns: ['Issue', 'Status'],
  rows: [{ cells: ['PAY-1423', 'In progress'], url: 'https://acme.atlassian.net/browse/PAY-1423' }],
  total: 4,
};

function journal(): ActivityJournal {
  const j = new ActivityJournal(RUN, 'ses-ar', 'org-ar');
  j.turn(1, [
    { callId: 'c-search', name: 'jira__search_issues', args: { jql: 'text ~ "release"' } },
    { callId: 'c-page', name: 'fetch_url', args: { url: 'https://docs.acme.dev/release' } },
    { callId: 'c-send', name: 'slack__send_message', args: { channel: '#eng', text: 'Shipped' } },
    { callId: 'c-code', name: 'code_task', args: { goal: 'Fix the flaky test' } },
    { callId: 'c-none', name: 'jira__search_issues', args: { jql: 'nothing' } },
  ]);
  j.add('x.pipeshub.tool.call_presented', {
    callId: 'c-search',
    toolName: 'jira__search_issues',
    argsJson: '{"jql":"text ~ \\"release\\""}',
    argsSummary: 'Search issues: "release"',
  });
  j.intent('t1.0', 'jira@default/search_issues').outcome('t1.0');
  j.add('x.pipeshub.tool.result_presented', {
    callId: 'c-search',
    status: 'completed',
    content: '{"issues":[{"key":"PAY-1423"}]}',
    resultSummary: '- Issues fetched successfully — 1 item\n- PAY-1423',
    resultView: VIEW,
  });
  j.intent('t1.1', 'web@default/fetch_url').outcome('t1.1');
  j.add('x.pipeshub.tool.result_presented', { callId: 'c-page', status: 'completed', content: LONG });
  j.held('ix.approve.t1.2', { reason: 'policy', toolId: 'slack@default/send_message' })
    .decided('ix.approve.t1.2', 'approved')
    .intent('t1.2', 'slack@default/send_message')
    .outcome('t1.2');
  j.add('x.pipeshub.tool.result_presented', { callId: 'c-send', status: 'completed', content: '{"ok":true}' });
  j.intent('t1.3', 'pipeshub@default/code_task');
  j.add('child.spawned', { childRunId: 'run-ar-child', invocationId: 't1.3', taskId: 'task-1' }, 'node');
  j.intent('t1.4', 'jira@default/search_issues').outcome('t1.4');
  j.add('x.pipeshub.tool.result_presented', { callId: 'c-none', status: 'completed', content: '[]' });
  return j;
}

const tools = (j = journal()) => foldActivity(j.events, { runId: RUN, runStatus: 'running' }).tools;
const byId = (id: string, j?: ActivityJournal) => tools(j).find((t) => t.toolCallId === id)!;

describe('ACTRESULTS: a step\'s detail is what the chat\'s step shows, field for field', () => {
  it('the detail of every step equals the chat step\'s fields', () => {
    const events = journal().events;
    const timeline = foldTimeline(events, RUN);
    const parts = buildParts(timeline).filter((p) => p['type'] === 'tool_call');
    expect(parts).to.have.length(5);
    for (const part of parts) {
      const detail = stepDetail(timeline, part['toolCallId'] as string)!;
      for (const field of DETAIL) {
        expect((detail as unknown as Record<string, unknown>)[field], `${String(part['toolCallId'])}.${field}`).to.deep.equal(part[field]);
      }
    }
  });

  it('a long result is cut where the chat cuts it, never longer', () => {
    const detail = stepDetail(foldTimeline(journal().events, RUN), 'c-page')!;
    expect(detail.resultPreview).to.have.length(2_000);
  });

  it('a step the run does not have has no detail', () => {
    expect(stepDetail(foldTimeline(journal().events, RUN), 'c-missing')).to.equal(null);
  });
});

describe('ACTRESULTS: the snapshot carries each step\'s line, never its result', () => {
  it('outcome in a line: the summary\'s first line, the records found, or nothing found', () => {
    expect(byId('c-search').outcome).to.deep.equal({ kind: 'summary', text: 'Issues fetched successfully — 1 item' });
    expect(byId('c-none').outcome).to.deep.equal({ kind: 'empty' });
    expect(byId('c-page').outcome).to.equal(undefined);
    expect(byId('c-search').argsSummary).to.equal('Search issues: "release"');
  });

  it('no argument, preview, summary or view travels in the snapshot; a fingerprint stands for them', () => {
    const snapshot = JSON.stringify(tools());
    expect(snapshot).to.not.include('xxxxxxxxxx');
    expect(snapshot).to.not.include('PAY-1423');
    expect(snapshot).to.not.include('"resultPreview"');
    const search = byId('c-search');
    expect(search.detail).to.equal(stepDetail(foldTimeline(journal().events, RUN), 'c-search')!.fingerprint);
  });

  it('the fingerprint changes when the result arrives, and only then', () => {
    const j = new ActivityJournal(RUN, 'ses-ar', 'org-ar');
    j.turn(1, [{ callId: 'c1', name: 'jira__get_issue', args: { key: 'PAY-1' } }]).intent('t1.0', 'jira@default/get_issue');
    const asked = byId('c1', j).detail;
    j.outcome('t1.0');
    j.add('x.pipeshub.tool.result_presented', { callId: 'c1', status: 'completed', content: '{"key":"PAY-1"}' });
    const answered = byId('c1', j).detail;
    j.add('x.pipeshub.task.updated', {});
    expect(asked).to.be.a('string');
    expect(answered).to.not.equal(asked);
    expect(byId('c1', j).detail).to.equal(answered);
  });

  it('an approved call says it was allowed; a call that started a child names the child run', () => {
    expect(byId('c-send').approvalAnswer).to.equal('allowed');
    expect(byId('c-search').approvalAnswer).to.equal(undefined);
    expect(byId('c-code').child).to.deep.include({ runId: 'run-ar-child', status: 'running', codeTaskGoal: 'Fix the flaky test' });
  });

  it('a declined call says so and has no outcome', () => {
    const j = new ActivityJournal(RUN, 'ses-ar', 'org-ar');
    j.turn(1, [{ callId: 'c-del', name: 'drive__delete_file', args: { id: 'f-1' } }])
      .held('ix.approve.t1.0', { reason: 'policy', toolId: 'drive@default/delete_file' })
      .decided('ix.approve.t1.0', 'denied')
      .denied('t1.0', 'interaction_denied');
    const [tool] = tools(j);
    expect(tool).to.deep.include({ status: 'denied', approvalAnswer: 'declined' });
    expect(tool!.outcome).to.equal(undefined);
  });
});

/** A run of `steps` calls, each with a result at the chat's bound and a summary, as a long Slack investigation. */
function longRun(steps: number): ActivityJournal {
  const j = new ActivityJournal(RUN, 'ses-ar', 'org-ar');
  for (let turn = 1; turn <= steps; turn += 1) {
    const callId = `c${turn}`;
    j.turn(turn, [{ callId, name: 'slack__search_messages', args: { query: `topic ${turn}` } }]);
    j.add('x.pipeshub.tool.call_presented', {
      callId,
      toolName: 'slack__search_messages',
      argsJson: JSON.stringify({ query: `topic ${turn}` }),
      argsSummary: `Search messages: "topic ${turn}"`,
    });
    j.intent(`t${turn}.0`, 'slack@default/search_messages').outcome(`t${turn}.0`);
    j.add('x.pipeshub.tool.result_presented', {
      callId,
      status: 'completed',
      content: 'm'.repeat(2_000),
      resultSummary: `Found 12 messages\n${Array.from({ length: 10 }, (_, i) => `- #channel-${i}: a message about topic ${turn}`).join('\n')}`,
      resultView: {
        kind: 'records',
        columns: ['Message', 'Channel'],
        rows: Array.from({ length: 10 }, (_, i) => ({ cells: [`message ${i} about topic ${turn}`, `#channel-${i}`], url: `https://acme.slack.com/archives/C${i}/p${turn}` })),
        total: 12,
      },
    });
  }
  return j;
}

describe('ACTRESULTS: snapshot size', () => {
  for (const steps of [30, 100]) {
    it(`${steps} steps: the snapshot carries lines, the results stay behind the step route`, () => {
      const j = longRun(steps);
      const view = foldActivity(j.events, { runId: RUN, runStatus: 'running' });
      const timeline = foldTimeline(j.events, RUN);
      const after = Buffer.byteLength(JSON.stringify(view));
      // What the snapshot was when each step carried the chat step's fields (the first ACTRESULTS build).
      const before = Buffer.byteLength(
        JSON.stringify({
          ...view,
          tools: view.tools.map((tool) => {
            const detail = without(stepDetail(timeline, tool.toolCallId)!, 'fingerprint', 'toolCallId');
            const line = without(tool, 'outcome', 'detail');
            return { ...line, ...detail };
          }),
        }),
      );
       
      console.log(`      snapshot bytes, ${steps} steps: before ${before}, after ${after}`);
      expect(after).to.be.below(before / 10);
      expect(after / steps).to.be.below(400);
    });
  }
});
