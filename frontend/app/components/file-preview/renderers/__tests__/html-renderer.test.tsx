import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { HtmlRenderer } from '../html-renderer';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('HtmlRenderer', () => {
  it('puts sanitized HTML in a sandboxed iframe so page CSS can apply', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => `<!DOCTYPE html>
<html><head><style>body{background:#000}</style></head>
<body><h1>Good pizza.</h1><svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"></circle></svg></body></html>`,
      }),
    );

    render(
      React.createElement(HtmlRenderer, {
        fileUrl: 'https://example.test/pizza-page.html',
        fileName: 'pizza-page.html',
      }),
    );

    const iframe = await waitFor(() => {
      const el = document.querySelector('iframe');
      if (!el) throw new Error('iframe not ready');
      return el as HTMLIFrameElement;
    });

    expect(iframe.getAttribute('sandbox')).toContain('allow-same-origin');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
    const srcDoc = iframe.getAttribute('srcdoc') || '';
    expect(srcDoc).toContain('Good pizza.');
    expect(srcDoc).toContain('<style>');
    expect(srcDoc.toLowerCase()).toContain('<svg');
  });

  it('shows an error when the file URL is missing', () => {
    render(React.createElement(HtmlRenderer, { fileUrl: '', fileName: 'empty.html' }));
    expect(screen.getByText('File URL not available')).toBeTruthy();
  });
});
