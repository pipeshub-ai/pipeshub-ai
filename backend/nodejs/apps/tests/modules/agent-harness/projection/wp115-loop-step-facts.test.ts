import { expect } from 'chai';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
import {
  callResultFrames,
  callStartFrames,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { ActivityJournal } from '../support/activity-journal';

/**
 * WP-115 (WP112-FU-1, FU-2): the built-in agent's own steps carry the step facts the worker built
 * (`step_display.py`) on its presented events, and Node passes them through to the live frames, the saved part
 * and the Activity panel's line, as an engine step's are, so the row reads "Read a page · onyx.app".
 */

const RUN = 'run-ls';
const PAGE = 'https://www.onyx.app/pricing';

function journal(): ActivityJournal {
  const j = new ActivityJournal(RUN, 'ses-ls', 'org-ls');
  j.turn(1, [
    { callId: 'c-page', name: 'dynamic__fetch_url', args: { url: PAGE } },
    { callId: 'c-jira', name: 'jira__search_issues', args: { jql: 'x' } },
  ]);
  j.add('x.pipeshub.tool.call_presented', {
    callId: 'c-page',
    toolName: 'dynamic__fetch_url',
    argsJson: JSON.stringify({ url: PAGE }),
    step: { action: 'readPage', target: 'onyx.app', href: PAGE },
  });
  j.add('x.pipeshub.tool.call_presented', {
    callId: 'c-jira',
    toolName: 'jira__search_issues',
    argsJson: '{"jql":"x"}',
  });
  j.intent('t1.0', 'web@default/fetch_url').outcome('t1.0');
  j.add('x.pipeshub.tool.result_presented', {
    callId: 'c-page',
    status: 'completed',
    content: 'Onyx pricing ...',
    step: { action: 'readPage', target: 'onyx.app', href: PAGE, title: 'Onyx pricing', sources: [PAGE] },
  });
  j.intent('t1.1', 'jira@default/search_issues').outcome('t1.1');
  j.add('x.pipeshub.tool.result_presented', { callId: 'c-jira', status: 'completed', content: '[]' });
  return j;
}

const call = (id: string) =>
  foldTimeline(journal().events, RUN)
    .turns.flatMap((turn) => turn.calls)
    .find((view) => view.callId === id)!;

describe('WP-115: a loop step carries the worker\'s step facts end to end', () => {
  it('LS-N1: the saved part has the result\'s facts; a tool of no such action has none', () => {
    const parts = buildParts(foldTimeline(journal().events, RUN)).filter((p) => p['type'] === 'tool_call');
    const page = parts.find((p) => p['toolCallId'] === 'c-page')!;
    expect(page['step']).to.deep.equal({
      action: 'readPage',
      target: 'onyx.app',
      href: PAGE,
      title: 'Onyx pricing',
      sources: [PAGE],
    });
    expect(parts.find((p) => p['toolCallId'] === 'c-jira')).to.not.have.property('step');
  });

  it('LS-N2: the live frames carry the same facts, so a live row equals a reloaded one', () => {
    const start = callStartFrames(call('c-page'), RUN);
    const args = start.find((f) => f.type === 'TOOL_CALL_ARGS')!;
    expect(args['step']).to.deep.include({ action: 'readPage', target: 'onyx.app' });
    const result = callResultFrames(call('c-page'), RUN).find((f) => f.type === 'TOOL_CALL_RESULT')!;
    expect(result['step']).to.deep.include({ title: 'Onyx pricing' });
    expect(callResultFrames(call('c-jira'), RUN).find((f) => f.type === 'TOOL_CALL_RESULT')).to.not.have.property('step');
  });

  it('LS-N3: the Activity panel\'s line names the step, without the links its result returned', () => {
    const tools = foldActivity(journal().events, { runId: RUN, runStatus: 'running' }).tools;
    expect(tools.find((t) => t.toolCallId === 'c-page')!.step).to.deep.equal({
      action: 'readPage',
      target: 'onyx.app',
      href: PAGE,
      title: 'Onyx pricing',
    });
    expect(tools.find((t) => t.toolCallId === 'c-jira')).to.not.have.property('step');
  });

  it('LS-N4: facts outside their contract (a script link, an unknown action) are no step', () => {
    const j = new ActivityJournal(RUN, 'ses-ls', 'org-ls');
    j.turn(1, [{ callId: 'c1', name: 'dynamic__fetch_url', args: { url: 'x' } }]);
    j.add('x.pipeshub.tool.call_presented', {
      callId: 'c1',
      toolName: 'dynamic__fetch_url',
      argsJson: '{}',
      step: { action: 'readPage', href: 'javascript:alert(1)' },
    });
    j.add('x.pipeshub.tool.result_presented', {
      callId: 'c1',
      status: 'completed',
      content: '',
      step: { action: 'runCommand' },
    });
    const [part] = buildParts(foldTimeline(j.events, RUN)).filter((p) => p['type'] === 'tool_call');
    expect(part).to.not.have.property('step');
  });
});
