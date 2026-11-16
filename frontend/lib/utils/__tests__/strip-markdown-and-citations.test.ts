import { describe, it, expect } from 'vitest';
import { stripMarkdownAndCitations } from '../formatters';

describe('stripMarkdownAndCitations', () => {
  it('removes bold and italic markers', () => {
    expect(stripMarkdownAndCitations('**bold**, *italic* and ***both***')).toBe(
      'bold, italic and both',
    );
  });

  it('keeps an asterisk that is not emphasis', () => {
    expect(stripMarkdownAndCitations('2 * 3 * 4 = 24')).toBe('2 * 3 * 4 = 24');
  });

  it('keeps a list bullet and strips emphasis later on the same line', () => {
    expect(stripMarkdownAndCitations('* Revenue grew *fast*\n* Costs fell')).toBe(
      '* Revenue grew fast\n* Costs fell',
    );
  });

  it('keeps asterisk runs around spaces', () => {
    expect(stripMarkdownAndCitations('** spaced **')).toBe('** spaced **');
    expect(stripMarkdownAndCitations('*** spaced ***')).toBe('*** spaced ***');
  });

  it('removes citation markers, headings and links', () => {
    expect(stripMarkdownAndCitations('## Title\nSee [the docs](https://x.y) [1][2].')).toBe(
      'Title\nSee the docs.',
    );
  });
});
