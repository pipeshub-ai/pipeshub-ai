import 'reflect-metadata';
import { expect } from 'chai';
import { cutShortNotice } from '../../../../src/integrations/slack-bot/src/utils/cut-short';
import {
  CHILD_DEFAULT_FAILURE,
  childFailureMessage,
} from '../../../../src/modules/agent-harness/projection/transcript';
import { CUT_SHORT_REASONS } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';

/**
 * WP77-WORDS: a long research run's engine child ended on two model provider failures, and the chat said "This
 * sub-task stopped because of an internal error." It was the provider, not PipesHub; the words now say so.
 */
describe('WP77-WORDS: a provider failure is named as the provider', () => {
  it('WP77-WORDS-1: a sub-task the provider stopped says the provider, and that its saved work is kept', () => {
    for (const code of ['provider_unavailable', 'rate_limited']) {
      const message = childFailureMessage('failed', code);
      expect(message, code).to.not.equal(CHILD_DEFAULT_FAILURE);
      expect(message, code).to.match(/AI model provider/);
      expect(message, code).to.not.match(/internal error/);
    }
  });

  it('WP77-WORDS-2: an answer the provider cut short names it in Slack too, and every reason has words', () => {
    expect(CUT_SHORT_REASONS).to.include('provider');
    expect(cutShortNotice('provider')).to.match(/^Stopped early: the AI model provider was unavailable\./);
    for (const reason of CUT_SHORT_REASONS) {
      expect(cutShortNotice(reason), reason).to.match(/^Stopped early: /);
    }
  });
});
