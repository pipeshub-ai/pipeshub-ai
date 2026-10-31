import 'reflect-metadata';
import { expect } from 'chai';
import {
  SlackActivityBuilder,
  stripSlackActivityTimeline,
} from '../../../src/integrations/slack-bot/src/utils/activity-ui';

function tool(activity: SlackActivityBuilder, name: string) {
  activity.startTool(name, `Using ${name}`);
  activity.finishTool(name, name);
}

describe('slack-bot/utils/activity-ui', () => {
  describe('collapsed()', () => {
    it('sums up thinking and tools in one line', () => {
      const activity = new SlackActivityBuilder();
      activity.appendReasoning('Look in Jira first.');
      tool(activity, 'search');
      tool(activity, 'fetch');
      tool(activity, 'search');

      expect(activity.collapsed().text).to.equal(':thought_balloon: _Thought and used 3 tools_');
    });

    it('counts a single tool in the singular', () => {
      const activity = new SlackActivityBuilder();
      tool(activity, 'search');
      expect(activity.collapsed().text).to.equal(':thought_balloon: _Used 1 tool_');
    });

    it('lists sub-agents after tools', () => {
      const activity = new SlackActivityBuilder();
      activity.appendReasoning('Split the work.');
      tool(activity, 'search');
      activity.startSubAgent('Researcher');
      activity.finishSubAgent('Researcher', 'completed');
      activity.startSubAgent('Writer');
      activity.finishSubAgent('Writer', 'failed');

      expect(activity.collapsed().text).to.equal(
        ':thought_balloon: _Thought, used 1 tool and delegated to 2 agents_',
      );
    });

    it('falls back when there is only narration', () => {
      const activity = new SlackActivityBuilder();
      activity.appendNarration('Checking a few places.');
      expect(activity.collapsed().text).to.equal(':thought_balloon: _Worked through it_');
    });

    it('puts the timeline in an attachment without the live status line', () => {
      const activity = new SlackActivityBuilder();
      activity.appendReasoning('Look in Jira first.');
      tool(activity, 'search');

      const { attachments } = activity.collapsed();
      expect(attachments).to.deep.equal([
        { text: '*Thinking*\n_Look in Jira first._\n\n• ✓ search', mrkdwn_in: ['text'] },
      ]);
      expect(attachments[0]!.text).not.to.contain('Thinking..._');
    });
  });

  describe('stripSlackActivityTimeline()', () => {
    it('drops the collapsed summary from thread context', () => {
      const activity = new SlackActivityBuilder();
      tool(activity, 'search');
      expect(stripSlackActivityTimeline(activity.collapsed().text)).to.equal('');
    });

    it('keeps ordinary bot text', () => {
      expect(stripSlackActivityTimeline('  Which project do you mean?  ')).to.equal(
        'Which project do you mean?',
      );
      expect(stripSlackActivityTimeline('I thought about it: _maybe_')).to.equal(
        'I thought about it: _maybe_',
      );
    });
  });
});
