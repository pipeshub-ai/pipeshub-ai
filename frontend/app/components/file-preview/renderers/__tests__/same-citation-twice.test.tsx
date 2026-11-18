/**
 * The same citation clicked twice, after scrolling away, must bring the viewer back to it in every
 * renderer. The click counter reaches each renderer as `citationClickVersion`; with the active id
 * unchanged, only that counter tells an effect to run again.
 */
import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import JSZip from 'jszip';
import { FilePreviewRenderer } from '../file-preview-renderer';
import { SET_ACTIVE_CITATION } from '../../sandboxed-html';
import type { PreviewCitation } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: Record<string, unknown>) => (opts?.defaultValue as string | undefined) ?? key }),
}));

const h = React.createElement;
const CITATIONS = [{ id: 'c1', content: 'Revenue grew.', paragraphNumbers: [2], boundingBox: [{ x: 0.1, y: 0.1 }, { x: 0.4, y: 0.3 }] }] as PreviewCitation[];

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


let scrollSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubGlobal('CSS', { escape: (v: string) => v.replace(/[^\w-]/g, '\\$&') });
  scrollSpy = vi.fn();
  Element.prototype.scrollIntoView = scrollSpy as never;
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => setTimeout(fn, 0));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(fileName: string, fileType: string, version: number) {
  const props = (v: number) => ({
    fileUrl: 'blob:http://localhost/x',
    fileName,
    fileType,
    citations: CITATIONS,
    activeCitationId: 'c1',
    citationClickVersion: v,
  });
  const view = render(h(Theme, null, h(FilePreviewRenderer, props(version))));
  return { ...view, again: (v: number) => view.rerender(h(Theme, null, h(FilePreviewRenderer, props(v)))) };
}

describe('the same citation twice', () => {
  it.each([
    ['text', 'notes.txt', 'text/plain'],
    ['markdown', 'notes.md', 'text/markdown'],
  ])('re-scrolls in the %s renderer', async (_name, fileName, fileType) => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => 'Revenue grew. More text.' })));
    const view = mount(fileName, fileType, 1);
    await waitFor(() => expect(scrollSpy).toHaveBeenCalled(), { timeout: 3000 });
    const first = scrollSpy.mock.calls.length;
    view.again(2);
    await waitFor(() => expect(scrollSpy.mock.calls.length).toBeGreaterThan(first), { timeout: 3000 });
  });

  it('re-scrolls in the HTML renderer (the sandboxed frame is told again)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => '<p>Revenue grew.</p>' })));
    const view = mount('page.html', 'text/html', 1);
    await waitFor(() => expect(view.container.querySelector('iframe')).not.toBeNull(), { timeout: 3000 });
    const post = vi.spyOn((view.container.querySelector('iframe') as HTMLIFrameElement).contentWindow as Window, 'postMessage');
    await new Promise((r) => setTimeout(r, 50));
    post.mockClear();
    view.again(2);
    expect(post).toHaveBeenCalledWith({ type: SET_ACTIVE_CITATION, id: 'c1' }, '*');
  });

  it('re-scrolls in the DOCX renderer (the sandboxed frame is told again)', async () => {
    const docx = await buildDocx('<w:p><w:r><w:t>Revenue grew.</w:t></w:r></w:p>');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => docx })));
    const view = mount('memo.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 1);
    await waitFor(() => expect(view.container.querySelector('iframe')).not.toBeNull(), { timeout: 5000 });
    const post = vi.spyOn((view.container.querySelector('iframe') as HTMLIFrameElement).contentWindow as Window, 'postMessage');
    await new Promise((r) => setTimeout(r, 50));
    post.mockClear();
    view.again(2);
    expect(post).toHaveBeenCalledWith({ type: SET_ACTIVE_CITATION, id: 'c1' }, '*');
  });

  it('re-scrolls in the CSV renderer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => 'a,b\n1,2\n3,4\n', arrayBuffer: async () => new TextEncoder().encode('a,b\n1,2\n3,4\n').buffer })));
    const view = mount('data.csv', 'text/csv', 1);
    await waitFor(() => expect(scrollSpy).toHaveBeenCalled(), { timeout: 5000 });
    const first = scrollSpy.mock.calls.length;
    view.again(2);
    await waitFor(() => expect(scrollSpy.mock.calls.length).toBeGreaterThan(first), { timeout: 5000 });
  });

  it('re-scrolls in the image renderer', async () => {
    const view = mount('scan.png', 'image/png', 1);
    const img = view.container.querySelector('img') as HTMLImageElement;
    img.dispatchEvent(new Event('load'));
    await waitFor(() => expect(scrollSpy).toHaveBeenCalled());
    const first = scrollSpy.mock.calls.length;
    view.again(2);
    await waitFor(() => expect(scrollSpy.mock.calls.length).toBeGreaterThan(first));
  });
});
