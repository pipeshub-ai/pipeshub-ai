import 'reflect-metadata';
import { expect } from 'chai';
import { runFailure } from '../../../../src/modules/agent-harness/projection/transcript';
import type { Timeline } from '../../../../src/modules/agent-harness/projection/timeline';

/**
 * WP79-WORDS: a run that had a tool its MCP server no longer offers ends `mcp_tool_withdrawn` with the worker's
 * words, which name the server by its label (a run once said only "A tool or connection this agent needs is no
 * longer available").
 */
describe('WP79-WORDS: a withdrawn tool names its server', () => {
  const failed = (errorType: string, message?: string): Timeline =>
    ({
      end: { status: 'failed', errorType, ...(message !== undefined ? { message } : {}) },
      approvals: [],
      turns: [{}],
      questions: [],
      answer: null,
    }) as unknown as Timeline;

  it("WP79-WORDS-1: the worker's message, naming the server, is what the person reads", () => {
    const words =
      'Atlassian no longer offers a tool this task was using, so the task stopped. Send your message again to continue without it, or ask your admin to review the server\'s tools.';
    expect(runFailure(failed('mcp_tool_withdrawn', words))).to.deep.equal({
      code: 'mcp_tool_withdrawn',
      message: words,
    });
  });

  it('WP79-WORDS-2: without a message it still says what happened and what to do, never the generic line', () => {
    const { message } = runFailure(failed('mcp_tool_withdrawn'));
    expect(message).to.match(/no longer offers a tool/);
    expect(message).to.match(/again/);
    expect(message).to.not.equal(runFailure(failed('no_such_code')).message);
  });

  it("WP79-WORDS-3: capability_lost keeps Node's own words, whatever the journal holds", () => {
    expect(runFailure(failed('capability_lost', 'raw worker text')).message).to.not.equal('raw worker text');
  });
});
