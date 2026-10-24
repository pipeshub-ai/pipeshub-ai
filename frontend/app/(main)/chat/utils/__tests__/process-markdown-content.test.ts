import { describe, it, expect } from 'vitest';
import { processMarkdownContent } from '../process-markdown-content';

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
    ['record link with text', 'see [report](/record/abc-123/preview#blockIndex=0)', 'see [1]'],
    ['tiny ref inside parentheses in prose', 'It grew (see [source](ref7)) last year.', 'It grew (see [1]) last year.'],
    ['no citations', 'Plain (text) with [a link](https://x.test/a_(b)).', 'Plain (text) with [a link](https://x.test/a_(b)).'],
  ])('%s', (_name, input, expected) => {
    expect(processMarkdownContent(input)).toBe(expected);
  });
});
