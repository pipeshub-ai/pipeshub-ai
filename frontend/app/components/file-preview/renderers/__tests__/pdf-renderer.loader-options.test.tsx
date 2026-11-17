import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const loaderProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);

vi.mock('../owned-pdf-loader', () => ({
  OwnedPdfLoader: (props: Record<string, unknown>) => {
    loaderProps.push(props);
    return null;
  },
}));

vi.mock('react-pdf-highlighter', () => ({
  PdfHighlighter: () => null,
  Highlight: () => null,
  AreaHighlight: () => null,
  Popup: () => null,
}));

import { PDFRenderer } from '../pdf-renderer';
import type { RecordRangeTransport } from '@/lib/record-content/pdf-range-transport';

afterEach(() => {
  cleanup();
  loaderProps.length = 0;
});

describe('PDFRenderer loader options', () => {
  it('asks pdf.js not to use eval when loading a document', () => {
    render(
      <Theme>
        <PDFRenderer fileUrl="https://files.example/report.pdf" fileName="report.pdf" />
      </Theme>,
    );

    expect(loaderProps.length).toBeGreaterThan(0);
    for (const props of loaderProps) {
      expect(props.url).toBe('https://files.example/report.pdf');
      expect(props.isEvalSupported).toBe(false);
    }
  });

  it('loads the pdf.js worker shipped with the app, not a CDN copy', () => {
    render(
      <Theme>
        <PDFRenderer fileUrl="https://files.example/report.pdf" fileName="report.pdf" />
      </Theme>,
    );

    for (const props of loaderProps) {
      expect(String(props.workerSrc)).toMatch(/pdfjs-dist\/build\/pdf\.worker\.min\.mjs$/);
      expect(String(props.workerSrc)).not.toMatch(/unpkg|cdn/);
    }
  });
});

describe('PDFRenderer loading by byte ranges', () => {
  const transport = { length: 200 * 1024 * 1024 } as unknown as RecordRangeTransport;

  it('hands pdf.js the range transport and turns off the background fetch and streaming', () => {
    render(
      <Theme>
        <PDFRenderer fileUrl="" fileName="big.pdf" rangeTransport={transport} />
      </Theme>,
    );
    expect(loaderProps.length).toBeGreaterThan(0);
    for (const props of loaderProps) {
      expect(props.range).toBe(transport);
      expect(props.length).toBe(200 * 1024 * 1024);
      expect(props.disableAutoFetch).toBe(true);
      expect(props.disableStream).toBe(true);
      expect(props.rangeChunkSize).toBe(1024 * 1024);
      expect(props.isEvalSupported).toBe(false);
      // PdfLoader loads nothing without a url; pdf.js ignores it once it has a transport
      expect(props.url).toBeTruthy();
    }
  });

  it('without a transport, none of the range options is passed', () => {
    render(
      <Theme>
        <PDFRenderer fileUrl="https://files.example/report.pdf" fileName="report.pdf" />
      </Theme>,
    );
    for (const props of loaderProps) {
      for (const key of ['range', 'length', 'disableAutoFetch', 'disableStream', 'rangeChunkSize']) {
        expect(props, key).not.toHaveProperty(key);
      }
    }
  });
});
