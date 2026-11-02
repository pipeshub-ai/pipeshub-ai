import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildFinalSlackChunks,
  slackBlocksFallbackText,
} from '../../../src/integrations/slack-bot/src/helpers';

const BLOCKS = [
  { type: 'section', text: { type: 'mrkdwn', text: '*Revenue* grew 12%.' } },
  {
    type: 'rich_text',
    elements: [{ type: 'rich_text_section', elements: [{ type: 'link', url: 'https://x.test', text: ' Q3 report' }] }],
  },
];

describe('slack-bot final answer', () => {
  describe('slackBlocksFallbackText', () => {
    it('joins the text of section and rich text blocks', () => {
      expect(slackBlocksFallbackText(BLOCKS)).to.equal('*Revenue* grew 12%. Q3 report');
    });

    it('is never empty', () => {
      expect(slackBlocksFallbackText([{ type: 'divider' }])).to.equal('Reply');
    });
  });
});

describe('slack-bot final answer blocks', () => {
  it('marks every answer section to always expand, so Slack never folds the answer', async () => {
    const long = Array.from({ length: 60 }, (_, i) => `Line ${i} of the answer.`).join('\n\n');
    const chunks = await buildFinalSlackChunks(
      `${long}\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n${long}`,
    );

    const sections = chunks.flat().filter((b) => b.type === 'section');
    expect(sections.length).to.be.greaterThan(1);
    for (const section of sections) {
      expect(section.expand).to.equal(true);
    }
  });
});
