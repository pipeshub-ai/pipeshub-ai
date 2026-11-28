import { describe, it, expect } from 'vitest';
import { repairStreamingMarkdown, settledAnswerText, withoutUndefinedFootnotes } from '../repair-streaming-markdown';

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

describe('repairStreamingMarkdown — citations not numbered yet', () => {
  it('leaves out the model\'s raw citations, and the space before them, never drawing a provisional chip', () => {
    expect(repairStreamingMarkdown('Freeze starts Monday [Release process](ref2). Then QA [x](https://ref7.xyz) signs.')).toBe(
      'Freeze starts Monday. Then QA signs.',
    );
    expect(repairStreamingMarkdown('See [the page](https://h/record/r1/preview) now')).toBe('See now');
  });

  it('keeps numbered citations, ordinary links and brackets in code', () => {
    const text = 'Ships Tuesday [1](https://h/record/r1/preview), see [docs](https://docs.example.com) and `a[0](1)`.';
    expect(repairStreamingMarkdown(text)).toBe(text);
    const fenced = '```py\nx = [1](ref2)\n```\nDone.';
    expect(repairStreamingMarkdown(fenced)).toBe(fenced);
  });

  it('cuts a link still being typed on the last line, but not a bracket an earlier line never closed', () => {
    expect(repairStreamingMarkdown('Freeze starts Monday. [Release pro')).toBe('Freeze starts Monday.');
    expect(repairStreamingMarkdown('Freeze starts Monday. [Release process](re')).toBe('Freeze starts Monday.');
    expect(repairStreamingMarkdown('The range [0, 10) is open.\nNext line')).toBe('The range [0, 10) is open.\nNext line');
  });

  it('holds back a last line that may become the confidence trailer, with the rule above it', () => {
    expect(repairStreamingMarkdown('Two open issues.\n\n---\nConfidence: Hi')).toBe('Two open issues.');
    expect(repairStreamingMarkdown('Two open issues.\n\n---')).toBe('Two open issues.');
    expect(repairStreamingMarkdown('Two open issues.\n\nConfident teams ship')).toBe('Two open issues.\n\nConfident teams ship');
  });
});

describe('withoutUndefinedFootnotes', () => {
  it('leaves out a footnote reference until its definition is in the text, then shows it', () => {
    expect(withoutUndefinedFootnotes('A note.[^1] More text')).toBe('A note. More text');
    expect(withoutUndefinedFootnotes('A note.[^1] More text\n\n[^1]: The definition.')).toBe('A note.[^1] More text\n\n[^1]: The definition.');
  });

  it('leaves a reference inside code alone', () => {
    expect(withoutUndefinedFootnotes('```\nx = a[^1]\n```')).toBe('```\nx = a[^1]\n```');
  });

  it('is left to the text as drawn: the repair of all that arrived keeps a reference whose definition came', () => {
    expect(repairStreamingMarkdown('A note.[^1] More text\n\n[^1]: The definition.')).toBe('A note.[^1] More text\n\n[^1]: The definition.');
  });
});

describe('settledAnswerText', () => {
  const url = (n: number) => `(https://h/record/r${n}/preview#blockIndex=${n})`;

  it('shows the words as they arrive, leaving out a citation the server has not numbered yet', () => {
    const tidiedPart = `Ships Tuesday [1]${url(1)}. `;
    const text = `${tidiedPart}QA signs [source](ref4) off`;
    expect(settledAnswerText(text, tidiedPart.length)).toBe(`${tidiedPart}QA signs off`);
  });

  it('keeps a descriptive label as the words the server leaves in the sentence, once', () => {
    expect(settledAnswerText('See [Release process](ref2) for details', 0)).toBe('See Release process for details');
    // Words the answer already wrote just before the link are not written twice.
    expect(settledAnswerText('See the release process [Release process](ref2) now', 0)).toBe('See the release process now');
  });

  it('shows a run of citations only whole: all of it numbered and the sentence going on after it', () => {
    const numbered = `Freeze rules [1]${url(1)} [2]${url(2)}`;
    // The third citation of the run is not numbered yet: none of it shows, so the group never grows.
    expect(settledAnswerText(`${numbered} [source](ref3) and more`, numbered.length)).toBe('Freeze rules and more');
    // All numbered, but nothing after it yet: another citation may still join it.
    expect(settledAnswerText(numbered, numbered.length)).toBe('Freeze rules');
    expect(settledAnswerText(`${numbered} [3]${url(3)}. Next`, 1000)).toBe(`${numbered} [3]${url(3)}. Next`);
  });

  it('treats citations in different paragraphs as different runs', () => {
    const text = `One [1]${url(1)}\n\nTwo [source](ref2) more`;
    const settled = `One [1]${url(1)}`.length;
    expect(settledAnswerText(text, settled)).toBe(`One [1]${url(1)}\n\nTwo more`);
  });

  it('holds a link the tidied part ended in the middle of', () => {
    expect(settledAnswerText('Ships Tuesday. [Release pro', 27)).toBe('Ships Tuesday.');
  });
});
