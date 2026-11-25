import 'reflect-metadata';
import { expect } from 'chai';
import {
  cutShortBlocks,
  cutShortNotice,
} from '../../../src/integrations/slack-bot/src/utils/cut-short';
import {
  installSlackBotEdition,
  resetSlackBotEditionForTests,
} from '../../../src/integrations/slack-bot/src/edition';

describe('slack-bot: an answer a limit cut short (BUDGETEND)', () => {
  it('names the spending limit, the turn limit and the working time as the web chat does', () => {
    expect(cutShortNotice('budget')).to.match(/^Stopped early: spending limit reached\./);
    expect(cutShortNotice('turns')).to.match(/^Stopped early: turn limit reached\./);
    expect(cutShortNotice('time')).to.match(/^Stopped early: time limit reached\./);
    expect(cutShortNotice('budget')).to.contain('continue');
  });

  it('BU-06: says an answer its repeating steps cut short stopped for that, and how to carry on', () => {
    expect(cutShortNotice('stall')).to.match(/^Stopped early: its steps kept repeating without progress\./);
    expect(cutShortNotice('stall')).to.contain('continue');
  });

  it('says nothing for an answer that was not cut short, or a limit this build does not know', () => {
    expect(cutShortNotice(undefined)).to.equal(null);
    expect(cutShortNotice('tokens')).to.equal(null);
    expect(cutShortBlocks(undefined)).to.equal(null);
  });

  it('is posted as one context block under the answer', () => {
    const blocks = cutShortBlocks('budget');
    expect(blocks).to.have.length(1);
    expect(blocks![0]).to.deep.include({ type: 'context' });
    const [element] = blocks![0]!['elements'] as Array<Record<string, unknown>>;
    expect(element).to.deep.include({ type: 'mrkdwn' });
    expect(String(element!['text'])).to.contain('Stopped early: spending limit reached');
  });

  describe('WP56-STOP-3: a budget beyond the run\'s own is worded by the edition', () => {
    const NOV = new Date(Date.UTC(2026, 10, 2));
    afterEach(() => resetSlackBotEditionForTests());

    it('the default has no words for it: the spending-limit line stays', () => {
      expect(cutShortNotice('budget', 'org', NOV)).to.match(/^Stopped early: spending limit reached\./);
      expect(cutShortNotice('budget', 'org', NOV)).to.contain('continue');
    });

    it('an installed edition words it, only beside a budget cut', () => {
      installSlackBotEdition({ budgetStopNotice: (scope, now) => `bound ${scope} ${now.getUTCMonth()}` });
      expect(cutShortNotice('budget', 'org', NOV)).to.equal('bound org 10');
      expect(cutShortNotice('budget', undefined, NOV)).to.match(/^Stopped early: spending limit reached\./);
      expect(cutShortNotice('turns', 'org', NOV)).to.match(/^Stopped early: turn limit reached\./);
      const [block] = cutShortBlocks('budget', 'org', NOV)!;
      const [element] = block!['elements'] as Array<Record<string, unknown>>;
      expect(String(element!['text'])).to.contain('bound org 10');
    });
  });
});
