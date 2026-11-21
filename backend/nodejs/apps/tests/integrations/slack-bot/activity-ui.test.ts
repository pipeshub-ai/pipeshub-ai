import 'reflect-metadata';
import { expect } from 'chai';
import {
  cleanNarration,
  stripSlackActivityTimeline,
} from '../../../src/integrations/slack-bot/src/utils/activity-ui';

describe('slack-bot/utils/activity-ui', () => {
  describe('stripSlackActivityTimeline()', () => {
    it('drops an activity message from thread context', () => {
      expect(stripSlackActivityTimeline('*Thinking*\n_Look in Jira._\n\n• ✓ Searched Jira')).to.equal('');
      expect(stripSlackActivityTimeline('_Thinking..._')).to.equal('');
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

  it('cleanNarration() drops the confidence trailer', () => {
    expect(cleanNarration('Let me check.\n---\nConfidence: High')).to.equal('Let me check.');
  });
});
