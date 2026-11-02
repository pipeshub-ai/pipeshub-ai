import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  createSlackAGUIEventHandler,
  toolProgressText,
} from '../../../src/integrations/slack-bot/src/utils/agui-stream';
import { SlackTaskPlan } from '../../../src/integrations/slack-bot/src/utils/plan-stream';

function snapshot(fields: Record<string, unknown>) {
  return { event: 'STATE_SNAPSHOT', data: { type: 'STATE_SNAPSHOT', snapshot: fields } };
}

describe('slack-bot/utils/agui-stream', () => {
  describe("an MCP server's progress on a running tool", () => {
    const running = { status: 'running_tool', current_tool: 'mcp_jira_search' };

    it('reports it the way the dashboard shows it', () => {
      const onToolProgress = sinon.stub();
      const handle = createSlackAGUIEventHandler({ onToolProgress });

      handle(snapshot({ ...running, progress: 3, total: 10 }));
      handle(snapshot({ ...running, progress: 0.25, total: 1 }));
      handle(snapshot({ ...running, progress: 3, total: 10, progress_message: '  Reading page 3 ' }));
      handle(snapshot({ ...running, progress: 7 }));

      expect(onToolProgress.getCalls().map((call) => call.args)).to.deep.equal([
        ['mcp_jira_search', ' 3/10'],
        ['mcp_jira_search', ' 25%'],
        ['mcp_jira_search', ' Reading page 3'],
      ]);
    });

    it("keeps the server's message plain text: no links or mentions", () => {
      expect(toolProgressText({ progress_message: '<!channel> <https://evil.example|click> & co' })).to.equal(
        ' !channel https://evil.example|click & co',
      );
    });

    it('says nothing it cannot read', () => {
      expect(toolProgressText({ progress: 'three', total: 10 })).to.equal('');
      expect(toolProgressText({ progress: 3, total: 0 })).to.equal('');
      expect(toolProgressText({ progress: Number.NaN, total: 10 })).to.equal('');
      expect(toolProgressText({ progress: 12.5, total: 10 })).to.equal(' 100%');
    });

    it('says nothing once the answer is settled', () => {
      const onToolProgress = sinon.stub();
      createSlackAGUIEventHandler({ onToolProgress })(snapshot({ ...running, final: true, progress: 1, total: 2 }));
      expect(onToolProgress.called).to.equal(false);
    });
  });

  describe('a model call discarded mid-stream', () => {
    const frame = (type: string, fields: Record<string, unknown> = {}) => ({ event: type, data: { type, ...fields } });

    it('takes back the text the failed attempt streamed, so the retry is posted once', () => {
      const onAnswerAccumulated = sinon.stub();
      const handle = createSlackAGUIEventHandler({ onAnswerAccumulated });

      handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
      handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'The dep' }));
      handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));
      handle(frame('CUSTOM', { runId: 'root', name: 'message_discarded', value: { messageId: 'm1' } }));
      expect(onAnswerAccumulated.lastCall.args[0]).to.equal('');

      handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm2' }));
      handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'The deploy broke it.' }));
      expect(onAnswerAccumulated.lastCall.args[0]).to.equal('The deploy broke it.');
    });

    it('drops the reasoning the failed attempt streamed, so the retry shows it once', () => {
      const plan = new SlackTaskPlan();
      const handle = createSlackAGUIEventHandler({
        onReasoning: (delta, done) => {
          if (delta) plan.reasoningDelta(delta);
          if (done) plan.reasoningDone();
        },
        onModelCallDiscarded: () => plan.dropReasoning(),
      });
      const attempt = (messageId: string) => {
        handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId }));
        handle(frame('REASONING_MESSAGE_CONTENT', { runId: 'root', delta: 'Check the deploy log.' }));
        handle(frame('REASONING_END', { runId: 'root' }));
      };

      attempt('m1');
      handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));
      handle(frame('CUSTOM', { runId: 'root', name: 'message_discarded', value: { messageId: 'm1' } }));
      attempt('m2');

      expect(plan.flushReasoning()?.output).to.equal('Check the deploy log.');
    });
  });
});
