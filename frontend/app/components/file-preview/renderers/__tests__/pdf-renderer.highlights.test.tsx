import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

// What PDFRenderer hands PdfHighlighter, render by render.
const highlighterProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);

vi.mock('react-pdf-highlighter', () => ({
  PdfLoader: ({ children }: { children: (doc: unknown) => React.ReactNode }) =>
    children({ numPages: 4 }),
  PdfHighlighter: (props: Record<string, unknown>) => {
    highlighterProps.push(props);
    return null;
  },
  Highlight: () => null,
  AreaHighlight: () => null,
  Popup: () => null,
}));

import { PDFRenderer } from '../pdf-renderer';

const CITATIONS = [{ id: 'c1', content: 'cited text', pageNumbers: [2] }];

function renderer(clickVersion: number) {
  return (
    <Theme>
      <PDFRenderer
        fileUrl="https://files.example/report.pdf"
        fileName="report.pdf"
        citations={CITATIONS}
        activeCitationId="c1"
        citationClickVersion={clickVersion}
      />
    </Theme>
  );
}

const last = () => highlighterProps[highlighterProps.length - 1];

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  highlighterProps.length = 0;
});

describe('PDFRenderer highlights handed to react-pdf-highlighter', () => {
  it('keeps one empty array until the viewer is ready, then the active citation', () => {
    const view = render(renderer(0));
    view.rerender(renderer(0));
    view.rerender(renderer(0));

    // Version 7 redraws on any new array; one before its viewer exists throws reading getPageView.
    const beforeReady = highlighterProps.map((p) => p.highlights);
    expect(beforeReady.length).toBeGreaterThan(1);
    expect(new Set(beforeReady).size).toBe(1);
    expect(beforeReady[0]).toEqual([]);

    act(() => {
      (last().scrollRef as (scrollTo: () => void) => void)(() => {});
    });

    const ready = last().highlights as Array<{ id: string }>;
    expect(ready.map((h) => h.id)).toEqual(['c1']);
  });

  it('hands over a new array when the same citation is clicked again', () => {
    const view = render(renderer(0));
    act(() => {
      (last().scrollRef as (scrollTo: () => void) => void)(() => {});
    });
    const firstClick = last().highlights;

    view.rerender(renderer(1));

    expect(last().highlights).not.toBe(firstClick);
    expect((last().highlights as Array<{ id: string }>).map((h) => h.id)).toEqual(['c1']);
  });
});

describe('PDFRenderer citation scroll requested before the viewer is ready', () => {
  it('scrolls to the citation once the viewer is ready', () => {
    vi.useFakeTimers();
    render(renderer(0));
    const scrollTo = vi.fn();

    act(() => {
      (last().scrollRef as (fn: () => void) => void)(scrollTo);
    });
    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(scrollTo).toHaveBeenCalled();
  });

  it('drops the scroll when the preview closes first', () => {
    // The timer used to outlive the component and fire after the test environment was gone.
    vi.useFakeTimers();
    const view = render(renderer(0));
    const scrollTo = vi.fn();

    act(() => {
      (last().scrollRef as (fn: () => void) => void)(scrollTo);
    });
    view.unmount();
    vi.advanceTimersByTime(500);

    expect(scrollTo).not.toHaveBeenCalled();
  });
});

describe('PDFRenderer citation scroll parked for one document', () => {
  const DOC_A = 'https://files.example/a.pdf';
  const DOC_B = 'https://files.example/b.pdf';
  const A_CITATIONS = [{ id: 'c1', content: 'cited in A', pageNumbers: [3] }];

  function preview(
    fileUrl: string,
    citations: Array<{ id: string; content: string; pageNumbers: number[] }>,
    onPageChange: (page: number) => void,
  ) {
    return (
      <Theme>
        <PDFRenderer
          fileUrl={fileUrl}
          fileName="doc.pdf"
          citations={citations}
          activeCitationId="c1"
          pagination={{ currentPage: 1, totalPages: 5, onPageChange }}
        />
      </Theme>
    );
  }

  const fireViewerReady = (scrollTo: () => void) => {
    act(() => {
      (last().scrollRef as (fn: () => void) => void)(scrollTo);
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
  };

  it('is not replayed on the next document, which has no matching citation', () => {
    vi.useFakeTimers();
    const onPageChange = vi.fn();
    const view = render(preview(DOC_A, A_CITATIONS, onPageChange));

    view.rerender(preview(DOC_B, [], onPageChange));
    const scrollTo = vi.fn();
    fireViewerReady(scrollTo);

    expect(scrollTo).not.toHaveBeenCalled();
    expect(onPageChange).not.toHaveBeenCalledWith(3);
  });

  it('is not replayed when the next document cites something else', () => {
    vi.useFakeTimers();
    const onPageChange = vi.fn();
    const view = render(preview(DOC_A, A_CITATIONS, onPageChange));

    view.rerender(
      preview(DOC_B, [{ id: 'b1', content: 'cited in B', pageNumbers: [2] }], onPageChange),
    );
    const scrollTo = vi.fn();
    fireViewerReady(scrollTo);

    expect(scrollTo).not.toHaveBeenCalled();
    expect(onPageChange).not.toHaveBeenCalledWith(3);
  });

  it('does not run a scroll already scheduled for the previous document', () => {
    vi.useFakeTimers();
    const onPageChange = vi.fn();
    const view = render(preview(DOC_A, A_CITATIONS, onPageChange));
    const scrollToA = vi.fn();
    act(() => {
      (last().scrollRef as (fn: () => void) => void)(scrollToA);
    });

    view.rerender(preview(DOC_B, [], onPageChange));
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(scrollToA).not.toHaveBeenCalled();
    expect(onPageChange).not.toHaveBeenCalledWith(3);
  });

  it("scrolls the next document to its own citation with the same id", () => {
    vi.useFakeTimers();
    const onPageChange = vi.fn();
    const view = render(preview(DOC_A, A_CITATIONS, onPageChange));

    view.rerender(
      preview(DOC_B, [{ id: 'c1', content: 'cited in B', pageNumbers: [2] }], onPageChange),
    );
    const scrollTo = vi.fn();
    fireViewerReady(scrollTo);

    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(scrollTo.mock.calls[0][0].position.pageNumber).toBe(2);
    expect(onPageChange).toHaveBeenCalledWith(2);
    expect(onPageChange).not.toHaveBeenCalledWith(3);
  });

  it('scrolls a new version of the document when the citations stay the same', () => {
    vi.useFakeTimers();
    const onPageChange = vi.fn();
    const view = render(preview(DOC_A, A_CITATIONS, onPageChange));

    view.rerender(preview(DOC_B, A_CITATIONS, onPageChange));
    const scrollTo = vi.fn();
    fireViewerReady(scrollTo);

    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(onPageChange).toHaveBeenCalledWith(3);
  });
});
