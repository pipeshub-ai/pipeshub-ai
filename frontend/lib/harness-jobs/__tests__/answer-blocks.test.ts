import { describe, expect, it } from 'vitest';
import { answerBlocks, answerPlainText, inlineSegments, repeatsProse } from '../answer-blocks';
import { journalParts } from './fixtures';

describe('answer markdown into the design\'s answer blocks', () => {
  it('a recorded cited answer: its citation links become [n] pills, and nothing of the URL is left', () => {
    const final = journalParts('chat_answer_with_citations').parts.find((p: { isFinal?: boolean }) => p.isFinal) as unknown as { content: string };
    const blocks = answerBlocks(final.content);
    expect(blocks).toHaveLength(1);
    const segs = blocks[0].kind === 'paragraph' ? blocks[0].segments : [];
    expect(segs.filter((s) => s.kind === 'citation').map((s) => s.kind === 'citation' && s.label)).toEqual(['1', '2']);
    expect(JSON.stringify(blocks)).not.toContain('https://');
  });

  it('headings no larger than the body, bullets, numbered items, code and tables', () => {
    const md = ['## Plan', '', 'Intro with **two facts** and `code` and a [link](https://example.com).', '', '- one [1]', '- two [[2]]', '', '1. first', '2. second', '', '### Detail', '', '```ts', 'const a = 1;', '```', '', '| A | B |', '| --- | --- |', '| x | y [3] |'].join('\n');
    const blocks = answerBlocks(md);
    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'paragraph', 'bullet', 'bullet', 'bullet', 'bullet', 'section-header', 'code', 'table']);
    expect(blocks[4]).toMatchObject({ kind: 'bullet', index: 1 });
    expect(blocks[7]).toMatchObject({ kind: 'code', language: 'ts', text: 'const a = 1;' });
    const table = blocks[8];
    expect(table.kind === 'table' && table.rows[0][1].some((s) => s.kind === 'citation' && s.label === '3')).toBe(true);
    const para = blocks[1];
    expect(para.kind === 'paragraph' && para.segments.map((s) => s.kind)).toEqual(['text', 'text', 'text', 'code', 'text', 'link', 'text']);
  });

  it('a reply still streaming: an unclosed fence is code so far, a half-typed marker never shows', () => {
    const blocks = answerBlocks('Here:\n\n```\nline one\nline two');
    expect(blocks[1]).toMatchObject({ kind: 'code', text: 'line one\nline two' });
    expect(answerBlocks('Done. ::artifact[report.pdf').map((b) => b.kind)).toEqual(['paragraph']);
    expect(answerPlainText(answerBlocks('Done. ::artifact[report.pdf'))).toBe('Done.');
  });

  it('emphasis keeps its words, and copy reads as plain text with [n] kept', () => {
    expect(inlineSegments('an *important* note')).toEqual([{ kind: 'text', text: 'an important note' }]);
    expect(answerPlainText(answerBlocks('- a [1]\n- b'))).toBe('- a [1]\n\n- b');
  });

  it('nested items keep their depth: two spaces or four, under a bullet or a number (FIX-UI1)', () => {
    const blocks = answerBlocks(['- top', '  - under', '    - deeper', '1. step', '   - note'].join('\n'));
    expect(blocks.map((b) => (b.kind === 'bullet' ? [b.index ?? '-', b.level ?? 0] : b.kind))).toEqual([['-', 0], ['-', 1], ['-', 2], [1, 0], ['-', 1]]);
    expect(JSON.stringify(blocks)).not.toContain('- ');
  });

  it('an image is a link to it, never a loaded picture or a stray "!"', () => {
    expect(inlineSegments('See ![the chart](https://x.test/c.png) here')).toEqual([
      { kind: 'text', text: 'See ' },
      { kind: 'link', text: 'the chart', href: 'https://x.test/c.png' },
      { kind: 'text', text: ' here' },
    ]);
  });

  it("a table's rule row sets each column's alignment; an all-left table carries none", () => {
    const [right] = answerBlocks('| Team | Pts |\n|---|---:|\n| RCB | 18 |');
    expect(right).toMatchObject({ kind: 'table', align: ['left', 'right'] });
    const [plain] = answerBlocks('| A | B |\n| --- | --- |\n| x | y |');
    expect(plain).not.toHaveProperty('align');
  });

  it('a report and the answer it became are the same prose whatever their citations', () => {
    const report = answerBlocks('# Title\n\nRCB won. [source](ref22) [source](ref3)\n\n- one');
    const answer = answerBlocks('# Title\n\nRCB won. [1](https://a.test/x#:~:text=y) [2](https://b.test)\n\n- one');
    expect(repeatsProse(report, answer)).toBe(true);
    expect(repeatsProse(report, answerBlocks('# Title\n\nGT won.'))).toBe(false);
    expect(repeatsProse([], [])).toBe(false);
    // The job's stream carries the report cut short, mid-word: a long enough start of the answer is the answer.
    const long = `# Title\n\n${'RCB won the final by five wickets. '.repeat(8)}And more after.`;
    expect(repeatsProse(answerBlocks(long.slice(0, 220)), answerBlocks(long))).toBe(true);
    expect(repeatsProse(answerBlocks('# Title'), answerBlocks(long))).toBe(false);
  });
});
