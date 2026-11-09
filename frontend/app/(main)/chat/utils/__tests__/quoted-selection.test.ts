import { describe, it, expect } from 'vitest';
import {
  QUOTED_SELECTION_MAX_CHARS,
  clipQuotedSelection,
  conversationTitleFromQuery,
  formatQuotedSelection,
  normalizeQuotedSelection,
  parseQuotedSelection,
  previewQuotedSelection,
  readableQuotedSelection,
} from '../quoted-selection';

describe('quoted-selection', () => {
  it('round-trips excerpt and question', () => {
    const stored = formatQuotedSelection('the SLA is 99.9%', 'What does this cover?');
    expect(parseQuotedSelection(stored)).toEqual({
      excerpt: 'the SLA is 99.9%',
      question: 'What does this cover?',
    });
  });

  it('leaves a normal question unchanged', () => {
    expect(parseQuotedSelection('What changed in Q3?')).toEqual({
      excerpt: null,
      question: 'What changed in Q3?',
    });
  });

  it('stores an excerpt with no follow-up question', () => {
    const stored = formatQuotedSelection('only this', '   ');
    expect(parseQuotedSelection(stored)).toEqual({
      excerpt: 'only this',
      question: '',
    });
  });

  it('normalizes selection whitespace', () => {
    expect(normalizeQuotedSelection('  a\u00a0b  \n\n\n  c  ')).toBe('a b\n\nc');
  });

  it('caps a long excerpt and marks it truncated', () => {
    const long = 'x'.repeat(QUOTED_SELECTION_MAX_CHARS + 80);
    const clipped = clipQuotedSelection(long);
    expect(clipped.truncated).toBe(true);
    expect(clipped.text.length).toBe(QUOTED_SELECTION_MAX_CHARS);
    expect(formatQuotedSelection(long, 'Why?').includes('x'.repeat(QUOTED_SELECTION_MAX_CHARS))).toBe(
      true,
    );
  });

  it('builds a one-line chip preview', () => {
    expect(previewQuotedSelection('hello\nworld', 20)).toBe('hello world');
    expect(previewQuotedSelection('x'.repeat(40), 12)).toBe(`${'x'.repeat(12)}…`);
  });

  it('titles from the question, not the wrapper', () => {
    const stored = formatQuotedSelection('a long quoted passage about invoices', 'Who owns this?');
    expect(conversationTitleFromQuery(stored)).toBe('Who owns this?');
    expect(conversationTitleFromQuery('Plain question')).toBe('Plain question');
    expect(conversationTitleFromQuery(formatQuotedSelection('only excerpt', ''))).toBe(
      'only excerpt',
    );
  });

  it('copies as a readable quote plus question', () => {
    const stored = formatQuotedSelection('line one\nline two', 'Explain');
    expect(readableQuotedSelection(stored)).toBe('> line one\n> line two\n\nExplain');
    expect(readableQuotedSelection('just a question')).toBe('just a question');
  });
});
