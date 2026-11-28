import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationData, CitationMaps } from '../response-tabs/citations';

afterEach(() => cleanup());

// No JSX here, as in answer-content.test.tsx.
const h = React.createElement;

/** One cited record, as the answer's citation maps carry it. */
function makeCitationMaps(): CitationMaps {
  const citation: CitationData = {
    citationId: 'c1',
    content: 'snippet text',
    chunkIndex: 1,
    recordId: 'r1',
    recordName: 'report.pdf',
    connector: 'ONEDRIVE',
    recordType: 'FILE',
    webUrl: 'https://example.com/report.pdf',
    mimeType: 'application/pdf',
    extension: 'pdf',
    previewRenderable: true,
    hideWeburl: false,
    citationType: 'vectordb|document',
  };
  return {
    citations: { c1: citation },
    sources: { r1: 'c1' },
    sourcesOrder: ['r1'],
    citationsOrder: { 1: 'c1' },
  };
}

describe('AnswerContent: the typing caret while an answer streams', () => {
  const streaming = (content: string, isStreaming = true) =>
    render(h(Theme, null, h(AnswerContent, { content, citationMaps: makeCitationMaps(), isStreaming }))).container;
  const host = (root: HTMLElement) => {
    const hosts = root.querySelectorAll('[data-caret]');
    expect(hosts.length).toBeLessThanOrEqual(1);
    return (hosts[0] as HTMLElement | undefined) ?? null;
  };

  it('rides after the citation chip that ends the line, drawn by CSS: the words are one text node', () => {
    const root = streaming('The plan ships in March [1].');
    const line = host(root) as HTMLElement;
    const chip = root.querySelector('[data-citation-pill]') as HTMLElement;
    // The caret is the paragraph's ::after, so it follows everything in it, the chip included.
    expect(line.contains(chip)).toBe(true);
    expect(line.lastChild).toBe(chip);
    // Nothing is put between the words to hold a caret: the chip is the line's only element.
    expect(Array.from(line.children)).toEqual([chip]);
    expect(root.querySelector('.typing-caret')).toBeNull();
    expect(root.textContent).toBe(streaming('The plan ships in March [1].', false).textContent);
  });

  it('follows a link or bold words at the end from outside them, never inside', () => {
    const link = host(streaming('Read [the handbook](https://example.com/handbook)')) as HTMLElement;
    expect(link.tagName).toBe('DIV');
    expect(link.lastElementChild?.tagName).toBe('A');
    cleanup();
    const bold = host(streaming('Read **the handbook**[1]')) as HTMLElement;
    expect(bold.querySelector('[data-citation-pill]')).not.toBeNull();
  });

  it('rides the last item of a list, and the item\'s own paragraph in a loose one', () => {
    const tight = host(streaming('- one\n- two\n  - two a')) as HTMLElement;
    expect(tight.tagName).toBe('LI');
    expect(tight.textContent).toBe('two a');
    cleanup();
    const loose = host(streaming('1. one\n\n2. two\n\n   More on two.')) as HTMLElement;
    expect(loose.textContent).toBe('More on two.');
  });

  it('rides a heading that ends the text so far, at any level', () => {
    for (const source of ['Intro.\n\n# Handbook', 'Intro.\n\n## Steps', 'Intro.\n\n#### Notes']) {
      const heading = host(streaming(source)) as HTMLElement;
      expect(heading.tagName).toMatch(/^H[1-6]$/);
      expect(heading.className).toContain('rt-Heading');
      cleanup();
    }
  });

  it('is never inside code, a table or a quote', () => {
    expect(host(streaming('Intro.\n\n```js\nconst a = 1;'))).toBeNull();
    cleanup();
    expect(host(streaming('| a | b |\n| --- | --- |\n| 1 | 2 |'))).toBeNull();
    cleanup();
    expect(host(streaming('> quoted'))).toBeNull();
  });

  it('follows a closed details block\'s summary, the footnotes\' last note, and a display formula', () => {
    const details = host(streaming('Intro.\n\n<details><summary>More</summary>\n\nHidden text\n\n</details>')) as HTMLElement;
    expect(details?.tagName).toBe('SUMMARY');
    expect(details.textContent).toBe('More');
    cleanup();
    const note = host(streaming('A note.[^1]\n\n[^1]: The definition.')) as HTMLElement;
    expect(note).not.toBeNull();
    expect(note.closest('section')).not.toBeNull();
    expect(note.textContent).toContain('The definition.');
    cleanup();
    const formula = host(streaming('Intro.\n\n$$\na^2 + b^2 = c^2\n$$')) as HTMLElement;
    expect(formula?.classList.contains('katex')).toBe(true);
    expect(formula.parentElement?.classList.contains('katex-display')).toBe(true);
  });

  it('is gone once the answer is done', () => {
    expect(host(streaming('Still typing this line'))).not.toBeNull();
    cleanup();
    expect(host(streaming('Still typing this line', false))).toBeNull();
  });
});

describe('AnswerContent: the saved answer taking over from the streamed one', () => {
  it('keeps every paragraph and chip on screen as the same nodes, citation data refreshed in place', () => {
    const content = 'Freeze starts Monday [1].\n\nQA signs off first.\n\nThen it ships.';
    const live = makeCitationMaps();
    const view = (isStreaming: boolean, maps: CitationMaps) =>
      h(Theme, null, h(AnswerContent, { content, citationMaps: maps, isStreaming }));
    const { container, rerender } = render(view(true, live));
    const paragraphs = Array.from(container.querySelectorAll('.answer-prose > div'));
    const chip = container.querySelector('[data-citation-pill]');
    expect(paragraphs).toHaveLength(3);

    const saved = makeCitationMaps();
    saved.citations.c1 = { ...saved.citations.c1, recordName: 'report-final.pdf' };
    rerender(view(false, saved));

    expect(Array.from(container.querySelectorAll('.answer-prose > div'))).toEqual(paragraphs);
    expect(paragraphs.every((p) => p.isConnected)).toBe(true);
    expect(container.querySelector('[data-citation-pill]')).toBe(chip);
    expect(chip?.textContent).toContain('report-final');
  });
});
