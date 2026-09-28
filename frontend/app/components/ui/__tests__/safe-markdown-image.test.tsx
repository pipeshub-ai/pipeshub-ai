import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { SafeMarkdownImage } from '../safe-markdown-image';
import { classifyImageUrl, markdownUrlTransform, rehypeStripStyleUrls } from '@/lib/utils/image-url-policy';
import type { Element, Root } from 'hast';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const template = opts?.defaultValue as string | undefined;
      if (template === undefined) return key;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(opts?.[name]));
    },
  }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

const h = React.createElement;
const renderImage = (src: string, alt = 'chart') =>
  render(h(Theme, null, h(SafeMarkdownImage, { src, alt })));

describe('classifyImageUrl', () => {
  // T26
  it.each([
    ['relative path', '/api/v1/knowledgeBase/stream/record/r1'],
    ['same-origin absolute', `${window.location.origin}/api/v1/document/d1/download`],
    ['data image', 'data:image/png;base64,iVBORw0KGgo='],
    ['blob', `blob:${window.location.origin}/2b6c0d7e`],
  ])('auto-loads %s', (_label, src) => {
    expect(classifyImageUrl(src).kind).toBe('auto');
  });

  it('auto-loads the configured API origin (split frontend/backend deployments)', () => {
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'https://api.pipeshub.example');
    expect(classifyImageUrl('https://api.pipeshub.example/api/v1/document/d1/download').kind).toBe('auto');
  });

  // T24
  it.each([
    ['remote https', 'https://evil.example/p?d=secret', 'evil.example'],
    ['protocol-relative', '//evil.example/p.png', 'evil.example'],
    ['look-alike presigned URL', 'https://evil.example/x?X-Amz-Signature=abc&d=secret', 'evil.example'],
    ['other port on same host', 'http://localhost:9999/p.png', 'localhost:9999'],
  ])('requires a click for %s', (_label, src, host) => {
    expect(classifyImageUrl(src)).toMatchObject({ kind: 'click', host });
  });

  it.each(['javascript:alert(1)', 'data:text/html,<script>1</script>', '', '   '])(
    'blocks %j outright',
    (src) => {
      expect(classifyImageUrl(src).kind).toBe('blocked');
    },
  );
});

describe('markdownUrlTransform', () => {
  const img = { type: 'element', tagName: 'img', properties: {}, children: [] } as Element;
  const a = { type: 'element', tagName: 'a', properties: {}, children: [] } as Element;

  it('keeps data:image and blob: on <img src> only', () => {
    expect(markdownUrlTransform('data:image/png;base64,AAAA', 'src', img)).toBe('data:image/png;base64,AAAA');
    expect(markdownUrlTransform('blob:http://x/1', 'src', img)).toBe('blob:http://x/1');
    expect(markdownUrlTransform('data:image/png;base64,AAAA', 'href', a)).toBe('');
    expect(markdownUrlTransform('javascript:alert(1)', 'href', a)).toBe('');
  });
});

describe('rehypeStripStyleUrls', () => {
  it('drops inline styles that could fetch (url(), CSS escapes) and keeps harmless ones', () => {
    const el = (style: string): Element => ({ type: 'element', tagName: 'span', properties: { style }, children: [] });
    const nodes = [
      el('background:url(https://evil.example/?d=1)'),
      el('background-image: \\75 rl(https://evil.example/)'),
      el('color: red'),
    ];
    const tree: Root = { type: 'root', children: nodes };
    rehypeStripStyleUrls()(tree);
    expect(nodes.map((n) => n.properties.style)).toEqual([undefined, undefined, 'color: red']);
  });
});

describe('SafeMarkdownImage', () => {
  // T24
  it('renders a placeholder naming the host instead of a remote <img>', () => {
    const { container } = renderImage('https://evil.example/p?d=secret');
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('Image from evil.example not loaded')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Load image from evil.example' })).toBeTruthy();
  });

  // T25
  it('loads the image after the user clicks "Load image"', () => {
    const { container } = renderImage('https://evil.example/p?d=secret');
    fireEvent.click(screen.getByRole('button', { name: 'Load image from evil.example' }));
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe('https://evil.example/p?d=secret');
    expect(img?.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(screen.queryByTestId('remote-image-placeholder')).toBeNull();
  });

  it('exposes "Load image" as a native, keyboard-focusable button', () => {
    renderImage('https://evil.example/p.png');
    const button = screen.getByRole('button', { name: 'Load image from evil.example' });
    expect(button.tagName).toBe('BUTTON');
    expect(button.getAttribute('type')).toBe('button');
    expect(button.textContent).toBe('Load image');
  });

  // T26
  it('renders same-origin and data: images directly', () => {
    const { container } = render(
      h(Theme, null,
        h(SafeMarkdownImage, { src: '/api/v1/knowledgeBase/stream/record/r1', alt: 'a' }),
        h(SafeMarkdownImage, { src: 'data:image/png;base64,iVBORw0KGgo=', alt: 'b' }),
      ),
    );
    const srcs = Array.from(container.querySelectorAll('img')).map((i) => i.getAttribute('src'));
    expect(srcs).toEqual(['/api/v1/knowledgeBase/stream/record/r1', 'data:image/png;base64,iVBORw0KGgo=']);
    expect(screen.queryByTestId('remote-image-placeholder')).toBeNull();
  });

  it('renders only the alt text for a URL it will never load', () => {
    const { container } = renderImage('javascript:alert(1)', 'alt text');
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
    expect(container.textContent).toBe('alt text');
  });
});
