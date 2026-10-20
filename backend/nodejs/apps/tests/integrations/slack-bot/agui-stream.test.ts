import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  createSlackAGUIEventHandler,
  toolProgressText,
} from '../../../src/integrations/slack-bot/src/utils/agui-stream';

function snapshot(fields: Record<string, unknown>) {
  return { event: 'STATE_SNAPSHOT', data: { type: 'STATE_SNAPSHOT', snapshot: fields } };
}

describe('slack-bot/utils/agui-stream', () => {
  describe("an MCP server's progress on the status line", () => {
    const running = { status: 'running_tool', current_tool: 'mcp_jira_search' };

    it('follows the tool label the way the dashboard shows it', () => {
      const onStatus = sinon.stub();
      const handle = createSlackAGUIEventHandler({ onStatus });

      handle(snapshot({ ...running, progress: 3, total: 10 }));
      handle(snapshot({ ...running, progress: 0.25, total: 1 }));
      handle(snapshot({ ...running, progress: 3, total: 10, progress_message: '  Reading page 3 ' }));
      handle(snapshot({ ...running, progress: 7 }));

      const messages = onStatus.getCalls().map((call) => call.args[0] as string);
      const label = messages[3];
      expect(label.endsWith('...')).to.equal(true);
      expect(messages.slice(0, 3)).to.deep.equal([`${label} 3/10`, `${label} 25%`, `${label} Reading page 3`]);
    });

    it("keeps the server's message plain text: no links or mentions", () => {
      expect(toolProgressText({ progress_message: '<!channel> <https://evil.example|click> & co' })).to.equal(
        ' &lt;!channel&gt; &lt;https://evil.example|click&gt; &amp; co',
      );
    });

    it('says nothing it cannot read', () => {
      expect(toolProgressText({ progress: 'three', total: 10 })).to.equal('');
      expect(toolProgressText({ progress: 3, total: 0 })).to.equal('');
      expect(toolProgressText({ progress: Number.NaN, total: 10 })).to.equal('');
      expect(toolProgressText({ progress: 12.5, total: 10 })).to.equal(' 100%');
    });

    it('still treats the settled answer as no status', () => {
      const onStatus = sinon.stub();
      createSlackAGUIEventHandler({ onStatus })(snapshot({ final: true, progress: 1, total: 2 }));
      expect(onStatus.called).to.equal(false);
    });
  });
});
