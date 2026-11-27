import { describe, expect, it } from 'vitest';
import { citationLinkLabel, processMarkdownContent } from '../process-markdown-content';

// P4.11 CR-5: while the answer streams, a citation's meaningful link text stays before its number,
// as the backend keeps it in the final answer (`app/utils/citations.py`).
describe('processMarkdownContent citation link text', () => {
  it('keeps a ticket id written as link text beside its number', () => {
    expect(processMarkdownContent('Tracked as [NW-118](ref3).')).toBe('Tracked as NW-118 [1].');
  });

  it('keeps a document name on a full record link', () => {
    const url = 'https://app.test/record/0a1b-2c3d/preview#blockIndex=2';
    expect(processMarkdownContent(`See [Leave Policy](${url}).`)).toBe('See Leave Policy [1].');
  });

  it('turns a generic link text into the number alone, as before', () => {
    expect(processMarkdownContent('Leave is 25 days [source](ref3) and [Source](https://ref4.xyz).')).toBe(
      'Leave is 25 days [1] and [2].',
    );
  });

  it('leaves the backend numbered citations as numbers', () => {
    expect(processMarkdownContent('A [2](https://app.test/record/ab/preview#blockIndex=0) b')).toBe('A [2] b');
  });

  it('numbers the same ref once', () => {
    expect(processMarkdownContent('[NW-112](ref3) and [source](ref3)')).toBe('NW-112 [1] and [1]');
  });
});

describe('citationLinkLabel', () => {
  it.each([
    ['NW-118', 'NW-118'],
    ['Leave Policy 2025', 'Leave Policy 2025'],
    ['source', ''],
    [' Sources ', ''],
    ['citation', ''],
    ['ref3', ''],
    ['3', ''],
    ['[3]', ''],
    ['', ''],
    ['https://ref3.xyz', ''],
  ])('%j → %j', (text, label) => {
    expect(citationLinkLabel(text)).toBe(label);
  });
});

describe('processMarkdownContent', () => {
  it.each([
    [
      'raw parentheses in a web citation URL (answers stored before the backend encoded them)',
      'See [1](https://en.wikipedia.org/wiki/Python_(programming_language)#:~:text=Python%202.0%20was%20released,very%20minor%29%20changes) now.',
      'See [1] now.',
    ],
    [
      'encoded parentheses',
      'See [1](https://en.wikipedia.org/wiki/Python_%28programming_language%29#:~:text=a) now.',
      'See [1] now.',
    ],
    ['double brackets', 'x [[2]](https://x.test/a_(b)) y', 'x [[2]] y'],
    ['connector record citation', 'Q3 grew [3](/record/abc-123/preview#blockIndex=4).', 'Q3 grew [3].'],
    ['several badges in a row', 'a [1](https://x.test/a_(b)) [2](https://x.test/c)', 'a [1] [2]'],
    ['tiny refs', 'per [source](ref3) and [source](https://ref4.xyz)', 'per [1] and [2]'],
    ['record link with a name, which stays before its number', 'see [report](/record/abc-123/preview#blockIndex=0)', 'see report [1]'],
    ['tiny ref inside parentheses in prose', 'It grew (see [source](ref7)) last year.', 'It grew (see [1]) last year.'],
    ['no citations', 'Plain (text) with [a link](https://x.test/a_(b)).', 'Plain (text) with [a link](https://x.test/a_(b)).'],
  ])('%s', (_name, input, expected) => {
    expect(processMarkdownContent(input)).toBe(expected);
  });
});
