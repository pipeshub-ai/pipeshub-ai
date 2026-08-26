import { describe, it, expect } from 'vitest';
import { splitMarkdownBlocks } from '../split-streaming-markdown';

describe('splitMarkdownBlocks', () => {
  it('returns an empty array for empty content', () => {
    expect(splitMarkdownBlocks('')).toEqual([]);
  });

  it('returns a single block for content with no blank lines', () => {
    const content = 'Just one paragraph, still being typed';
    expect(splitMarkdownBlocks(content)).toEqual([content]);
  });

  it('splits two paragraphs separated by a blank line into two blocks', () => {
    const blocks = splitMarkdownBlocks('Para one.\n\nPara two.');
    expect(blocks).toHaveLength(2);
    expect(blocks[0].trim()).toBe('Para one.');
    expect(blocks[1].trim()).toBe('Para two.');
  });

  it('never splits inside a fenced code block, even across an internal blank line', () => {
    const content = [
      'Intro line.',
      '',
      '```js',
      "console.log('a');",
      '',
      "console.log('b');",
      '```',
      '',
      'More text.',
    ].join('\n');

    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(3);
    expect(blocks[0].trim()).toBe('Intro line.');
    // The fence — including its internal blank line — stays in one block.
    expect(blocks[1]).toContain("console.log('a')");
    expect(blocks[1]).toContain("console.log('b')");
    expect(blocks[1].trim().startsWith('```js')).toBe(true);
    expect(blocks[1].trim().endsWith('```')).toBe(true);
    expect(blocks[2].trim()).toBe('More text.');
  });

  it('never splits inside a tilde fence', () => {
    const content = ['~~~', 'line one', '', 'line two', '~~~'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
  });

  it('merges a blockquote continuation across a blank line', () => {
    const content = ['> Quote line one.', '', '> Quote line two.'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toContain('Quote line one.');
    expect(blocks[0]).toContain('Quote line two.');
  });

  it('merges a list continuation across a blank line (loose list)', () => {
    const content = ['- Item one', '', '- Item two'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
  });

  it('merges an ordered-list continuation across a blank line', () => {
    const content = ['1. Item one', '', '2. Item two'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
  });

  it('merges a 4-space indented continuation across a blank line', () => {
    const content = ['Some paragraph.', '', '    indented continuation line'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
  });

  it('merges a closing HTML tag continuation across a blank line', () => {
    const content = [
      '<details>',
      '<summary>Click me</summary>',
      '',
      '</details>',
    ].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
  });

  it('merges a table row continuation across a blank line instead of splitting it off', () => {
    // Conservative by design: a false split could sever a table's header
    // from its delimiter row, which would make remark-gfm fall back to
    // plain text for the whole block.
    const content = ['Intro text.', '', '| A | B |', '| - | - |', '| 1 | 2 |'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(1);
  });

  it('keeps a complete table (no internal blank lines) as a single block', () => {
    const content = ['| Name | Role |', '| --- | --- |', '| Alice | Engineer |', '| Bob | Manager |'].join('\n');
    expect(splitMarkdownBlocks(content)).toEqual([content]);
  });

  it('does not treat an ordinary unrelated paragraph after a blank line as a continuation', () => {
    const content = ['First paragraph.', '', 'Second, unrelated paragraph.'].join('\n');
    const blocks = splitMarkdownBlocks(content);
    expect(blocks).toHaveLength(2);
  });

  it('drops leading blank-only segments instead of emitting an empty block', () => {
    const blocks = splitMarkdownBlocks('\n\n\nHello');
    expect(blocks).toEqual(['Hello']);
  });

  it('is stable for the append-only growth pattern used during streaming', () => {
    // Every block except the very last one must stay byte-identical as more
    // content is appended — this is what lets `MarkdownBlock`'s React.memo
    // actually skip work for settled blocks. (The last block of a given
    // snapshot can still pick up a trailing blank line once a *new* block
    // starts after it — that's one extra render for that one block, not a
    // stability violation for the ones before it.)
    const base = 'First paragraph.\n\nSecond paragraph.\n\nThird paragraph.';
    const grown = `${base}\n\nFourth paragraph, still growing`;

    const blocksBase = splitMarkdownBlocks(base);
    const blocksGrown = splitMarkdownBlocks(grown);

    expect(blocksGrown.slice(0, blocksBase.length - 1)).toEqual(blocksBase.slice(0, -1));
  });
});
