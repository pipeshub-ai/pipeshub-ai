/**
 * QAFIX2: a PDF opens at the panel's width until the person picks a zoom, so a phone shows the whole slide; a page
 * that already fits at 100 % (desktop) stays at 100 %. A chosen zoom is applied as chosen.
 */
import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { act, render, cleanup, renderHook } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const highlighterProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);
// react-pdf-highlighter 7 hands PDFRenderer its pdf.js viewer through the component's ref.
const pdfViewer = vi.hoisted(() => ({ current: undefined as unknown }));

vi.mock('../owned-pdf-loader', () => ({
  OwnedPdfLoader: ({ children }: { children: (doc: unknown) => React.ReactNode }) => <>{children({ numPages: 1 })}</>,
}));
vi.mock('react-pdf-highlighter', () => ({
  PdfHighlighter: class PdfHighlighter extends React.Component<Record<string, unknown>> {
    get viewer() {
      return pdfViewer.current;
    }
    render() {
      highlighterProps.push(this.props);
      return null;
    }
  },
  Highlight: () => null,
  AreaHighlight: () => null,
  Popup: () => null,
}));

import { PDFRenderer } from '../pdf-renderer';
import { usePdfZoom } from '../../use-pdf-zoom';

/** A pdf.js viewer whose `page-width` scale is `fitScale`. */
function fakeViewer(fitScale: number) {
  const viewer = {
    currentPageNumber: 1,
    currentScale: 1,
    get currentScaleValue() {
      return String(this.currentScale);
    },
    set currentScaleValue(value: string) {
      this.currentScale = value === 'page-width' ? fitScale : Number(value);
    },
  };
  pdfViewer.current = viewer;
  return viewer;
}

function renderPdf(scale: number | undefined, onScaleChange = vi.fn()) {
  render(
    <Theme>
      <PDFRenderer
        fileUrl="https://files.example/deck.pdf"
        fileName="deck.pdf"
        pagination={{ currentPage: 1, totalPages: 1, onPageChange: () => {}, scale, onScaleChange }}
      />
    </Theme>,
  );
  return onScaleChange;
}

afterEach(() => {
  cleanup();
  highlighterProps.length = 0;
  pdfViewer.current = undefined;
});

describe('PDF preview zoom before the person picks one (QAFIX2)', () => {
  it('opens at the page width on a phone and reports that zoom', () => {
    const viewer = fakeViewer(0.42);
    const onScaleChange = renderPdf(undefined);
    expect(highlighterProps.at(-1)?.pdfScaleValue).toBe('page-width');
    expect(viewer.currentScale).toBe(0.42);
    expect(onScaleChange).toHaveBeenLastCalledWith(0.42);
  });

  it('keeps 100 % where the page already fits (desktop)', () => {
    const viewer = fakeViewer(1.6);
    const onScaleChange = renderPdf(undefined);
    expect(viewer.currentScale).toBe(1);
    expect(onScaleChange).toHaveBeenLastCalledWith(1);
  });

  it('applies a chosen zoom as chosen', () => {
    const viewer = fakeViewer(0.42);
    const onScaleChange = renderPdf(1.5);
    expect(highlighterProps.at(-1)?.pdfScaleValue).toBe('1.5');
    expect(viewer.currentScale).toBe(1.5);
    expect(onScaleChange).not.toHaveBeenCalled();
  });

  it('the zoom hook names no scale until the person zooms', () => {
    const { result } = renderHook(() => usePdfZoom('f', 'u'));
    expect(result.current.pdfScaleValue).toBeUndefined();
    act(() => result.current.setPdfScale(0.42));
    expect(result.current.pdfScaleValue).toBeUndefined();
    act(() => result.current.handlePdfZoomIn());
    expect(result.current.pdfScaleValue).toBe(0.5);
  });
});
