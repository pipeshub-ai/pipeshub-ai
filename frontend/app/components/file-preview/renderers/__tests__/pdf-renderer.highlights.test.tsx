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
