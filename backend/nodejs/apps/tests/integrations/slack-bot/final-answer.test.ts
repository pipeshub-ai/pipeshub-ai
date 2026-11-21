import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildFinalSlackChunks,
  looksLikeAnswer,
  markdownTableStart,
  trailingTableRowsStart,
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

describe('slack-bot held text', () => {
  it('looks like an answer at its first paragraph break, heading, list, table or fence', () => {
    for (const text of ['One.\n\nTwo', '## Title', 'Intro\n- item', '1. first', '| a | b |', '```ts', '~~~']) {
      expect(looksLikeAnswer(text), text).to.equal(true);
    }
    for (const text of ['Let me check the filings.', 'One paragraph.\n\n', 'Costs fell 5% - mostly in Q3.']) {
      expect(looksLikeAnswer(text), text).to.equal(false);
    }
  });
});

describe('slack-bot table probe', () => {
  it('finds where a table starts: its header row, then its separator, outside code fences', () => {
    const intro = 'Tickets:\n\n';
    expect(markdownTableStart(`${intro}| A | B |\n|---|---|\n| 1 | 2 |`)).to.equal(intro.length);
    expect(markdownTableStart(`${intro}| A | B |\n|--`), 'the separator is not complete yet').to.equal(-1);
    expect(markdownTableStart('```\n| A | B |\n|---|---|\n```')).to.equal(-1);
    expect(markdownTableStart('Costs | fell\nno table')).to.equal(-1);
  });

  it('finds trailing lines that may still become a table, so they are held', () => {
    expect(trailingTableRowsStart('Intro\n\n| A | B |\n')).to.equal('Intro\n\n'.length);
    expect(trailingTableRowsStart('Intro\n\n| A | B |\n|---')).to.equal('Intro\n\n'.length);
    expect(trailingTableRowsStart('Intro\n\n| A')).to.equal('Intro\n\n'.length);
    expect(trailingTableRowsStart('| A |\nAfter it')).to.equal(-1);
    expect(trailingTableRowsStart('Intro\n')).to.equal(-1);
    expect(trailingTableRowsStart('```\n| A | B |\n')).to.equal(-1);
  });
});
