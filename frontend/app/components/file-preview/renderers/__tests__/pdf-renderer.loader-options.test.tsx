import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const loaderProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);

vi.mock('react-pdf-highlighter', () => ({
  PdfLoader: (props: Record<string, unknown>) => {
    loaderProps.push(props);
    return null;
  },
  PdfHighlighter: () => null,
  Highlight: () => null,
  AreaHighlight: () => null,
  Popup: () => null,
}));

import { PDFRenderer } from '../pdf-renderer';

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
});
