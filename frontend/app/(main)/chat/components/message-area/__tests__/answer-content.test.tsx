import React from 'react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent, createMarkdownComponents } from '../answer-content';
import type { CitationData, CitationMaps } from '../response-tabs/citations';

afterEach(() => cleanup());

// No JSX here — see the same note in agent-activity.test.tsx.
const h = React.createElement;

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

// Table + links + citations — the exact combination that used to flicker.
const FIXTURE = [
  'Here is a summary with a citation[1] and a [link](https://example.com/x).',
  '',
  '| Name | Role | Link |',
  '| --- | --- | --- |',
  '| Alice | Engineer | [profile](https://example.com/alice) |',
  '| Bob | Manager | [profile](https://example.com/bob) |',
].join('\n');

function renderAnswer(isStreaming: boolean) {
  return render(
    h(
      Theme,
      null,
      h(AnswerContent, {
        content: FIXTURE,
        citationMaps: makeCitationMaps(),
        isStreaming,
      }),
    ),
  );
}

describe('AnswerContent — streaming block-split vs single-instance render equivalence', () => {
  // Guards against splitting artifacts: whatever `splitMarkdownBlocks` does
  // while streaming must produce the exact same visible result as the
  // single-`ReactMarkdown`-instance path used for the persisted message.
  it('renders identical textContent whether block-split (streaming) or not', () => {
    const streamed = renderAnswer(true);
    const streamedText = streamed.container.textContent;
    cleanup();

    const single = renderAnswer(false);
    const singleText = single.container.textContent;

    expect(streamedText).toBeTruthy();
    expect(streamedText).toBe(singleText);
  });

  it('renders the same table/td counts in both modes', () => {
    const streamed = renderAnswer(true);
    const streamedTableCount = streamed.container.querySelectorAll('table').length;
    const streamedTdCount = streamed.container.querySelectorAll('td').length;
    cleanup();

    const single = renderAnswer(false);
    const singleTableCount = single.container.querySelectorAll('table').length;
    const singleTdCount = single.container.querySelectorAll('td').length;

    expect(streamedTableCount).toBe(1);
    expect(streamedTableCount).toBe(singleTableCount);
    expect(streamedTdCount).toBe(singleTdCount);
    expect(streamedTdCount).toBeGreaterThan(0);
  });

  it('renders the same number of links in both modes', () => {
    const streamed = renderAnswer(true);
    const streamedLinkCount = streamed.container.querySelectorAll('a').length;
    cleanup();

    const single = renderAnswer(false);
    const singleLinkCount = single.container.querySelectorAll('a').length;

    expect(streamedLinkCount).toBe(singleLinkCount);
    expect(streamedLinkCount).toBeGreaterThan(0);
  });
});

describe('AnswerContent — link labels', () => {
  function renderContent(content: string) {
    return render(h(Theme, null, h(AnswerContent, { content, citationMaps: makeCitationMaps(), isStreaming: false })));
  }

  it('shows an autolinked URL by its short name, with the full URL on hover', () => {
    const url = 'https://github.com/pipeshub-ai/pipeshub-ai/pull/1234';
    const { container } = renderContent(`Merged in ${url}.`);
    const link = container.querySelector('a') as HTMLAnchorElement;
    expect(link.textContent).toBe('#1234');
    expect(link.getAttribute('href')).toContain(url);
    expect(link.getAttribute('title')).toBe(url);
  });

  it('shortens a markdown link whose text is its own URL', () => {
    const url = 'https://acme.atlassian.net/browse/PROJ-12';
    const { container } = renderContent(`[${url}](${url})`);
    expect(container.querySelector('a')?.textContent).toBe('PROJ-12');
  });

  it('keeps link text the answer wrote', () => {
    const { container } = renderContent('Read the [release notes](https://example.com/a/b/notes).');
    const link = container.querySelector('a') as HTMLAnchorElement;
    expect(link.textContent).toBe('release notes');
    expect(link.getAttribute('title')).toBeNull();
  });
});

describe('AnswerContent — link URLs with parentheses', () => {
  it('keeps escaped brackets in link text out of math when the URL has parentheses', () => {
    const { container } = render(
      h(Theme, null, h(AnswerContent, {
        content: 'Read [the \\[draft\\] notes](https://x.test/wiki/A_(b)) first.',
        citationMaps: makeCitationMaps(),
        isStreaming: false,
      })),
    );
    const link = container.querySelector('a') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('https://x.test/wiki/A_(b)');
    expect(link.textContent).toBe('the [draft] notes');
    expect(container.querySelector('.katex')).toBeNull();
  });
});

describe('AnswerContent headings', () => {
  const render2 = (content: string, isStreaming = false) =>
    render(h(Theme, null, h(AnswerContent, { content, citationMaps: makeCitationMaps(), isStreaming })));

  it('keeps every answer heading below its 20px question: h1 18px and h2 16px semibold, h3 to h6 14px bold', () => {
    const { container } = render2('Intro.\n\n# Handbook\n\nSome text.\n\n## Steps\n\n### Release days\n\n#### A\n\n##### B\n\n###### C\n\nMore.');
    const [h1, h2, ...rest] = Array.from(container.querySelectorAll('h1, h2, h3, h4, h5, h6')) as HTMLElement[];
    expect(h1.className).toContain('rt-Heading');
    expect(h1.className).toContain('rt-r-size-4');
    expect(h1.style.fontWeight).toBe('600');
    // The top level keeps its rule under it, below the answer's opening.
    expect(h1.style.borderBottom).toContain('var(--border-default)');
    expect(h1.style.paddingBottom).toBe('var(--space-2)');
    expect(h2.className).toContain('rt-r-size-3');
    expect(h2.style.fontWeight).toBe('600');
    expect(h2.style.marginTop).toBe('var(--space-4)');
    expect(h2.style.borderBottom).toBe('');
    expect(rest.map((h) => h.tagName)).toEqual(['H3', 'H4', 'H5', 'H6']);
    for (const h of rest) {
      expect(h.className, h.tagName).toContain('rt-r-size-2');
      expect(h.className, h.tagName).toContain('rt-r-weight-bold');
    }
    expect(container.innerHTML).not.toMatch(/rt-r-size-[5-9]/);
  });

  it('drops the rule of an h1 that opens the answer, as the line under the question separates them; later h1s keep theirs', () => {
    for (const isStreaming of [false, true]) {
      const { container } = render2('\n\n# Launch plan\n\nThe plan moved.\n\n# Owners\n\nPriya.\n\n## Notes\n\n# Risks', isStreaming);
      const [opening, ...later] = Array.from(container.querySelectorAll('h1')) as HTMLElement[];
      expect(opening.textContent, `streaming ${isStreaming}`).toContain('Launch plan');
      expect(opening.style.borderBottom, `streaming ${isStreaming}`).toBe('');
      expect(opening.style.paddingBottom).toBe('');
      // Still the answer's h1: its size, weight and space below.
      expect(opening.className).toContain('rt-r-size-4');
      expect(opening.style.fontWeight).toBe('600');
      expect(opening.style.marginBottom).toBe('var(--space-3)');
      expect(later.map((h) => h.textContent?.replace('#', ''))).toEqual(['Owners', 'Risks']);
      for (const h of later) expect(h.style.borderBottom, h.textContent ?? '').toContain('var(--border-default)');
      cleanup();
    }
    // An answer that opens with text keeps the rule on its first h1.
    const intro = render2('First a line.\n# Title').container.querySelector('h1') as HTMLElement;
    expect(intro.style.borderBottom).toContain('var(--border-default)');
  });

  it('gives a heading the same size wherever it falls, and while the answer streams in blocks', () => {
    const opening = render2('## Overview\n\nSome text.\n\n## Details\n\nMore', true).container;
    for (const heading of Array.from(opening.querySelectorAll('h2'))) expect(heading.className).toContain('rt-r-size-3');
    cleanup();
    expect((render2('Intro first.\n\n## Later').container.querySelector('h2') as HTMLElement).className).toContain('rt-r-size-3');
  });
});

describe('AnswerContent footnotes and link definitions', () => {
  const answer = (content: string) => h(Theme, null, h(AnswerContent, { content, citationMaps: makeCitationMaps() }));
  const BEFORE = ['## Overview', '', 'Intro text.', '', '| A | B |', '| --- | --- |', '| 1 | 2 |', '', 'A claim.[^1]', '', 'Closing.'];

  it('keeps what is drawn above the first use of a footnote when its definition arrives, and puts the note at the end', () => {
    const { container, rerender } = render(answer(BEFORE.join('\n')));
    const table = container.querySelector('table');
    const heading = container.querySelector('h2');
    expect(table).not.toBeNull();
    rerender(answer([...BEFORE, '', '[^1]: The source.'].join('\n')));
    // The same elements: a definition arriving late does not draw the answer above its first use again.
    expect(container.querySelector('table')).toBe(table);
    expect(container.querySelector('h2')).toBe(heading);
    const prose = container.querySelector('.answer-prose') as HTMLElement;
    const notes = prose.querySelector('section[data-footnotes]');
    expect(notes).not.toBeNull();
    expect(prose.lastElementChild).toBe(notes);
    expect(notes?.textContent).toContain('The source.');
    const ref = prose.querySelector('sup a') as HTMLAnchorElement;
    expect(ref.getAttribute('href')).toBe('#user-content-fn-1');
  });

  it('draws the same text as the whole answer in one piece', () => {
    const content = [...BEFORE, '', '[^1]: The source.', '', 'See [the guide][g].', '', '[g]: https://example.com/g'].join('\n');
    const split = render(answer(content)).container;
    const link = split.querySelector('a[href="https://example.com/g"]');
    expect(link?.textContent).toBe('the guide');
    // The whole answer as one block: the text a reader sees is the same.
    expect((split.querySelector('.answer-prose') as HTMLElement).textContent).toContain('Intro text.');
    expect((split.querySelector('.answer-prose') as HTMLElement).textContent?.trim().endsWith('↩')).toBe(true);
  });
});

describe('AnswerContent prose', () => {
  const renderProse = (content: string) =>
    render(h(Theme, null, h(AnswerContent, { content, citationMaps: makeCitationMaps(), isStreaming: false })));

  it("sets lists with the browser's own bullets and numbers, the text 16px in", () => {
    const { container } = renderProse('1. **Freeze.** Cut the branch.\n2. **Ship.** Deploy.\n\n- First\n- Second');
    const ol = container.querySelector('ol') as HTMLElement;
    const ul = container.querySelector('ul') as HTMLElement;
    expect(ol.closest('.answer-prose')).not.toBeNull();
    expect(ol.style.listStyleType).toBe('decimal');
    expect(ul.style.listStyleType).toBe('disc');
    for (const list of [ol, ul]) {
      expect(list.style.paddingLeft).toBe('var(--space-4)');
      expect(list.style.marginBottom).toBe('var(--space-3)');
    }
  });

  it('reads paragraphs on a 1.6 line and list items on a 1.7 line', () => {
    const { container } = renderProse('One paragraph.\n\n- A bullet\n- Another\n\n1. A loose step\n\n2. Another');
    const paragraph = container.querySelector('.answer-prose > div.rt-Text') as HTMLElement;
    expect(paragraph.style.lineHeight).toBe('1.6');
    expect(paragraph.style.marginBottom).toBe('var(--space-3)');
    const items = Array.from(container.querySelectorAll('li')) as HTMLElement[];
    expect(items).toHaveLength(4);
    for (const li of items) {
      expect(li.style.lineHeight).toBe('1.7');
      expect(li.style.marginBottom).toBe('var(--space-1)');
    }
  });

  it('counts a list from its own start', () => {
    expect((renderProse('3. Third\n4. Fourth').container.querySelector('ol') as HTMLElement).getAttribute('start')).toBe('3');
    cleanup();
    expect((renderProse('0. Zeroth\n1. First').container.querySelector('ol') as HTMLElement).getAttribute('start')).toBe('0');
    cleanup();
    expect((renderProse('1. First').container.querySelector('ol') as HTMLElement).getAttribute('start')).toBeNull();
  });

  it('sets inline code as a bordered code chip', () => {
    const { container } = renderProse('Run `make db-failover` from the ops repository.');
    const code = container.querySelector('code') as HTMLElement;
    expect(code.style.background).toBe('var(--fill-hover)');
    expect(code.style.border).toContain('var(--gray-6)');
  });

  it('sets a quote with an accent rule, and keeps callouts', () => {
    const { container } = renderProse('> Ready for review.\n\n> [!NOTE]\n> Heads up.');
    const quote = container.querySelector('blockquote') as HTMLElement;
    expect(quote.style.borderLeft).toContain('var(--accent-9)');
    expect(quote.style.background).toBe('');
    expect(container.textContent).toContain('Note');
  });

  it('starts the answer flush, in globals.css', () => {
    // jsdom doesn't load globals.css: check the rule that zeroes the first block's top margin exists.
    const css = readFileSync(path.resolve(__dirname, '../../../../../globals.css'), 'utf8');
    const match = css.match(/^\.answer-prose > :first-child \{([^}]*)\}/m);
    expect(match).not.toBeNull();
    expect(match![1]).toContain('margin-top: 0 !important');
    // The answer's lists use the browser's markers: no drawn numbers or dots are left.
    expect(css).not.toContain('.answer-prose ol > li::before');
    expect(css).not.toContain('.answer-prose ul > li::before');
  });

  it('leaves the base map the trail narration uses as it was', () => {
    const ref = { current: undefined };
    const base = createMarkdownComponents(ref, ref);
    const heading = render(h(Theme, null, base.h2({ children: 'Step' }))).container.querySelector('h2') as HTMLElement;
    expect(heading.className).toContain('rt-Text');
    expect(heading.className).toContain('rt-r-size-2');
    cleanup();
    const { container } = render(h('div', null, base.ol({ children: h('li', null, 'step') })));
    expect((container.querySelector('ol') as HTMLElement).style.listStyleType).toBe('decimal');
    const narration = render(h(Theme, null, base.p({ children: 'A step.' }))).container;
    expect((narration.querySelector('div.rt-Text') as HTMLElement).style.lineHeight).toBe('calc(var(--line-height-2) + var(--space-1) / 2)');
  });
});
