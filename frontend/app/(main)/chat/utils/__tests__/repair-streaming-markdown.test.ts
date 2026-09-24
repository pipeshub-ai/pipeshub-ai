import { describe, it, expect } from 'vitest';
import { repairStreamingMarkdown } from '../repair-streaming-markdown';

describe('repairStreamingMarkdown', () => {
  it('converts escaped newlines to real newlines', () => {
    expect(repairStreamingMarkdown('line one\\nline two')).toBe('line one\nline two');
  });

  it('returns empty/falsy content unchanged', () => {
    expect(repairStreamingMarkdown('')).toBe('');
  });

  it('closes an unclosed backtick fence', () => {
    const result = repairStreamingMarkdown('```python\nprint(1)');
    expect(result).toBe('```python\nprint(1)\n```');
  });

  it('closes an unclosed tilde fence using the same fence character', () => {
    const result = repairStreamingMarkdown('~~~\ncode');
    expect(result).toBe('~~~\ncode\n~~~');
  });

  it('leaves a fully closed fence untouched', () => {
    const content = '```js\nconsole.log(1);\n```\nmore text';
    expect(repairStreamingMarkdown(content)).toBe(content);
  });

  it('drops an incomplete trailing table row instead of force-closing it', () => {
    const content = '| Organization Name | PipesHub';
    // No trailing `|` yet — the row is dropped entirely rather than closed
    // with a synthetic ` |`, so the table doesn't re-lay-out on every
    // partial-cell chunk.
    expect(repairStreamingMarkdown(content)).toBe('');
  });

  it('drops only the last line, keeping earlier complete rows intact', () => {
    const content = '| Col1 | Col2 |\n| --- | --- |\n| a | b |\n| c | d';
    expect(repairStreamingMarkdown(content)).toBe(
      '| Col1 | Col2 |\n| --- | --- |\n| a | b |',
    );
  });

  it('leaves a complete table row (starts and ends with |) untouched', () => {
    const content = '| Col1 | Col2 |\n| --- | --- |\n| a | b |';
    expect(repairStreamingMarkdown(content)).toBe(content);
  });

  it('leaves plain prose (no leading pipe) untouched', () => {
    const content = 'Just a partial sentence still bei';
    expect(repairStreamingMarkdown(content)).toBe(content);
  });

  it('handles escaped newlines feeding into a partial table row', () => {
    const content = '| Col1 | Col2 |\\n| --- | --- |\\n| a | b';
    expect(repairStreamingMarkdown(content)).toBe('| Col1 | Col2 |\n| --- | --- |');
  });

  it('does not touch a partial row while a fence is still open (fence repair takes precedence)', () => {
    const content = '```\n| not a table row because we are in a fence';
    expect(repairStreamingMarkdown(content)).toBe(content + '\n```');
  });
});
