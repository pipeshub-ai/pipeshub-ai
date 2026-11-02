import React from 'react';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationMaps } from '../response-tabs/citations';

afterEach(() => cleanup());

const h = React.createElement;

const EMPTY_CITATIONS: CitationMaps = {
  citations: {},
  sources: {},
  sourcesOrder: [],
  citationsOrder: {},
};

// The stylesheet answer-content.tsx imports. rehype-katex brings its own katex
// dependency, so the HTML it emits only lays out correctly when that copy is
// the same major as this one (KaTeX 0.18 renamed .base/.strut/.sizing).
const KATEX_CSS = readFileSync(
  createRequire(import.meta.url).resolve('katex/dist/katex.min.css'),
  'utf8',
);

function renderMath(content: string) {
  return render(
    h(Theme, null, h(AnswerContent, { content, citationMaps: EMPTY_CITATIONS, isStreaming: false })),
  );
}

describe('AnswerContent math rendering', () => {
  it('renders inline and display math with the class names the imported KaTeX CSS styles', () => {
    const { container } = renderMath(
      ['Inline \\(x^2\\) in a sentence.', '', '\\[\\frac{a}{b} + \\sum_{i=1}^n i\\]'].join('\n'),
    );

    expect(container.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('.katex-display')).not.toBeNull();

    for (const cls of ['katex-base', 'katex-strut', 'katex-sizing']) {
      expect(container.querySelector(`.katex-html .${cls}`), `${cls} in rendered HTML`).not.toBeNull();
      expect(KATEX_CSS, `${cls} in katex.min.css`).toContain(`.${cls}`);
    }
    expect(container.querySelector('.katex-html .base')).toBeNull();
    expect(container.querySelector('.katex-html .strut')).toBeNull();
  });
});
