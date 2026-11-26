/**
 * FIX-TOOLWORDS (QA-AUTO FU-5): an engine's gateway call that waits for an approval is one step. The worker names the
 * step by the gateway's tool name and the ask names the step it gates (`callId`); both UIs fold the two from these
 * lines, so the live frame and the saved timeline must carry them as the worker wrote them.
 */
import { expect } from 'chai';
import { engineProgressFrame } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { ActivityJournal } from '../support/activity-journal';

const ASK = 'ix.approve.kaaa8862305c13d14f9b79ade140095fd8bc8587f';
const TOOL = 'mcp_e2esaas_create_ticket';
const STEP = {
  args: '{"title": "UIP2 444fc4", "body": "From UI-P2."}',
  callId: 'call_e2e_33',
  displayName: 'E2Esaas · create_ticket',
};
const ASKED = {
  argsJson: '{"body": "From UI-P2.", "title": "UIP2 444fc4"}',
  displayName: 'E2Esaas · create_ticket',
  interactionId: ASK,
  toolName: TOOL,
  callId: 'call_e2e_33',
};

describe('FIX-TOOLWORDS engine gateway steps', () => {
  it('the live frames keep the gateway name, the display name and the step the ask gates', () => {
    const step = engineProgressFrame({ tool: 'code_task', taskId: 't', kind: 'tool', text: TOOL, data: STEP }, 'run-1');
    const ask = engineProgressFrame({ tool: 'code_task', taskId: 't', kind: 'approval', text: TOOL, data: ASKED }, 'run-1');
    expect(step.value).to.deep.include({ kind: 'tool', text: TOOL, data: STEP });
    expect(ask.value).to.deep.include({ kind: 'approval', text: TOOL, data: ASKED });
  });

  it('the saved timeline and the activity keep them too, and list the ask once', () => {
    const journal = new ActivityJournal()
      .engine('started', 'File a ticket UIP2 444fc4.')
      .engine('tool', TOOL, STEP)
      .engine('approval', TOOL, ASKED)
      .held(ASK, { rule: 'writes' });
    const timeline = foldTimeline(journal.events, journal.runId);
    const frames = timeline.engine!.frames;
    expect(frames[1]).to.deep.include({ kind: 'tool', text: TOOL, data: STEP });
    expect(frames[2]).to.deep.include({ kind: 'approval', text: TOOL, data: ASKED });
    expect(timeline.approvals.map((a) => [a.interactionId, a.toolName])).to.deep.equal([[ASK, TOOL]]);
    const { tools } = foldActivity(journal.events, { runId: journal.runId, runStatus: 'waiting_approval' });
    expect(tools.map((t) => [t.toolCallId, t.status])).to.deep.equal([[ASK, 'awaiting_approval']]);
  });
});
