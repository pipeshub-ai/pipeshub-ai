import { expect } from 'chai';
import { AGUI } from '../../../../src/modules/agent-harness/projection/frames';
import { callResultFrames, callStartFrames } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
import { ActivityJournal } from '../support/activity-journal';

/**
 * WP-117 (WP108-FU-4): what main's legacy stream shows of a tool call, the durable projection shows too. The live
 * TOOL_CALL_ARGS frame carries the bounded `argsPreview` (#4180); the saved part keeps this request's citation refs
 * as the URLs they stand for (legacy's saved tool part), while the live frames keep what the tool said.
 */

const RUN = 'run-117';
const URL = '/record/live:1/preview#blockIndex=0';

function journal(presented: Record<string, unknown>, result: Record<string, unknown>): ActivityJournal {
  const j = new ActivityJournal(RUN, 'ses-117', 'org-117');
  j.turn(1, [{ callId: 'c1', name: 'drive__read_file', args: { file_id: 'ref1' } }]);
  j.add('x.pipeshub.tool.call_presented', { callId: 'c1', toolName: 'drive__read_file', argsJson: '{"file_id": "ref1"}', ...presented });
  j.intent('t1.0', 'drive@default/read_file').outcome('t1.0');
  j.add('x.pipeshub.tool.result_presented', { callId: 'c1', status: 'completed', content: '[ref1] May.', ...result });
  return j;
}

const call = (j: ActivityJournal) => foldTimeline(j.events, RUN).turns[0]!.calls[0]!;
const part = (j: ActivityJournal) => buildParts(foldTimeline(j.events, RUN)).find((p) => p['type'] === 'tool_call')!;
const argsFrame = (j: ActivityJournal) => callStartFrames(call(j), RUN).find((f) => f.type === AGUI.TOOL_CALL_ARGS)!;
const resultFrame = (j: ActivityJournal) => callResultFrames(call(j), RUN).find((f) => f.type === AGUI.TOOL_CALL_RESULT)!;

describe('WP-117: a durable tool call shows what the legacy stream shows', () => {
  it('the live arguments frame carries argsPreview: the arguments when they fit', () => {
    const j = journal({}, {});
    expect(argsFrame(j)['argsPreview']).to.equal('{"file_id": "ref1"}');
    expect(argsFrame(j)['delta']).to.equal('{"file_id": "ref1"}');
  });

  it('argsPreview is the bounded serialization the saved part keeps, never the whole arguments', () => {
    const j = journal({ args: '{"file_id": "re… [truncated]"}' }, {});
    expect(argsFrame(j)['argsPreview']).to.equal('{"file_id": "re… [truncated]"}');
    expect(argsFrame(j)['delta']).to.equal('{"file_id": "ref1"}');
    expect(part(j)['args']).to.equal('{"file_id": "re… [truncated]"}');
  });

  it('the saved part keeps refs as record links; the live frames keep the tool\'s own words', () => {
    const j = journal(
      { savedArgs: `{"file_id": "${URL}"}` },
      { resultSummary: 'Read ref1', savedContent: `[${URL}] May.`, savedSummary: `Read ${URL}` },
    );
    expect(part(j)).to.deep.include({ args: `{"file_id": "${URL}"}`, resultPreview: `[${URL}] May.`, resultSummary: `Read ${URL}` });
    expect(argsFrame(j)['argsPreview']).to.equal('{"file_id": "ref1"}');
    expect(resultFrame(j)).to.deep.include({ content: '[ref1] May.', resultSummary: 'Read ref1' });
  });

  it('a journal written before the saved fields reads as it did', () => {
    const j = journal({}, { resultSummary: 'Read ref1' });
    expect(part(j)).to.deep.include({ args: '{"file_id": "ref1"}', resultPreview: '[ref1] May.', resultSummary: 'Read ref1' });
  });
});
