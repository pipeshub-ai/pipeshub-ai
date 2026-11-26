/**
 * TOOLWORDS-FU-2: an opencode run journaled before FIX-TOOLWORDS named its gateway call `tools_<name>` and its ask
 * carried no `callId`, so the chat drew "Tools MCP …" and a second row for the ask. The timeline (from the journal)
 * and a saved answer read those lines as FIX-TOOLWORDS writes them: the gateway's name, the display name, and the
 * ask naming its step. Lines are QA-AUTO FU-5's `kept-qadur3` frames (`fu5-opencode-ticket-frames.json`, `recorded`).
 */
import { expect } from 'chai';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import {
  LegacyEngineLines,
  withSavedEngineLines,
} from '../../../../src/modules/agent-harness/projection/legacy-engine-lines';
import { attachMessages } from '../../../../src/modules/enterprise_search/utils/utils';
import { ActivityJournal } from '../support/activity-journal';

const ASK = 'ix.approve.kaaa8862305c13d14f9b79ade140095fd8bc8587f';
const TOOL = 'mcp_e2esaas_create_ticket';
const RECORDED_STEP = { args: '{"title": "UIP2 444fc4", "body": "From UI-P2."}', callId: 'call_e2e_33' };
const RECORDED_ASK = {
  argsJson: '{"body": "From UI-P2.", "title": "UIP2 444fc4"}',
  displayName: 'E2Esaas · create_ticket',
  interactionId: ASK,
  toolName: TOOL,
};
/** What FIX-TOOLWORDS journals for the same call (the fixture's `fixed`). */
const FIXED_STEP = { ...RECORDED_STEP, displayName: 'E2Esaas · create_ticket' };
const FIXED_ASK = { ...RECORDED_ASK, callId: 'call_e2e_33' };

const line = (kind: string, text: string, data: Record<string, unknown>) => ({ tool: 'code_task', taskId: 't', kind, text, data });

describe('TOOLWORDS-FU-2 old opencode lines read as FIX-TOOLWORDS writes them', () => {
  it('the timeline folded from an old journal names the call by the gateway and links its ask', () => {
    const journal = new ActivityJournal()
      .engine('started', 'File a ticket UIP2 444fc4.')
      .engine('tool', `tools_${TOOL}`, RECORDED_STEP)
      .engine('approval', TOOL, RECORDED_ASK)
      .held(ASK, { rule: 'writes' });
    const frames = foldTimeline(journal.events, journal.runId).engine!.frames;
    expect(frames[1]).to.deep.include({ kind: 'tool', text: TOOL, data: FIXED_STEP });
    expect(frames[2]).to.deep.include({ kind: 'approval', text: TOOL, data: FIXED_ASK });
    expect(journal.events.some((e) => JSON.stringify(e).includes('"callId":"call_e2e_33","displayName"'))).to.equal(false);
  });

  it('a saved answer with the ask before its call (the saved order) is linked too', () => {
    const saved = {
      _id: 'm1',
      parts: [{ type: 'engine_task', frames: [line('approval', TOOL, RECORDED_ASK), line('tool', `tools_${TOOL}`, RECORDED_STEP)] }],
    };
    const [message] = attachMessages({ _id: 's' }, [saved]).messages;
    const [ask, step] = message.parts[0].frames;
    expect(ask.data).to.deep.equal(FIXED_ASK);
    expect(step).to.deep.include({ text: TOOL, data: FIXED_STEP });
    expect(saved.parts[0]!.frames[1]!.text).to.equal(`tools_${TOOL}`);
  });

  it('two calls of one tool each take their own ask; an ask with other arguments stays unlinked', () => {
    const reader = new LegacyEngineLines();
    const a = reader.line(line('tool', `tools_${TOOL}`, { args: '{"title":"A"}', callId: 'c1' }));
    reader.line(line('tool_result', `tools_${TOOL}`, { callId: 'c1', status: 'completed' }));
    reader.line(line('tool', `tools_${TOOL}`, { args: '{"title":"B"}', callId: 'c2' }));
    const askB = reader.line(line('approval', TOOL, { toolName: TOOL, argsJson: '{"title":"B"}', interactionId: 'ix2' }));
    const askC = reader.line(line('approval', TOOL, { toolName: TOOL, argsJson: '{"title":"C"}', interactionId: 'ix3' }));
    expect(a.text).to.equal(TOOL);
    expect(askB.data['callId']).to.equal('c2');
    expect(askC.data).to.not.have.property('callId');
  });

  it('leaves a new line, a native tool and another engine’s name as they are', () => {
    const reader = new LegacyEngineLines();
    expect(reader.line(line('tool', 'edit', { callId: 'e' })).text).to.equal('edit');
    expect(reader.line(line('tool', 'mcp__tools__create_ticket', { callId: 'm' })).text).to.equal('mcp__tools__create_ticket');
    expect(reader.line(line('approval', TOOL, FIXED_ASK)).data).to.equal(FIXED_ASK);
    expect(reader.line(line('tool', 'tools_', { callId: 'x' })).text).to.equal('tools_');
    const plain = [{ type: 'text', content: 'hi' }];
    expect(withSavedEngineLines(plain)).to.equal(plain);
  });
});
