import React from 'react';
import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { SourcesAccordion } from '../sources-accordion';
import type { CitationData, CitationMaps } from '../response-tabs/citations/types';

// jsdom doesn't implement `matchMedia` — `ReferenceCard` (via `useIsMobile`)
// calls it on mount to decide whether to show the desktop or mobile action
// row. Stub a "desktop" (no match) response so the component tree renders.
beforeAll(() => {
  window.matchMedia = window.matchMedia ?? ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

// `ReferenceCard` (rendered by SourcesTab/CitationsTab) transitively imports
// `@/knowledge-base/api` -> the shared axios instance, which hydrates the
// auth store from `window.localStorage` at module load. jsdom's storage
// stubbing is unreliable for that eager side effect, so replace the API
// module entirely — this test only cares about SourcesAccordion's own
// expand/collapse and view-toggle behavior, not the API client.
vi.mock('@/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    streamRecord: vi.fn(),
    getRecordDetails: vi.fn(),
  },
}));

afterEach(() => cleanup());

// No JSX-free requirement here (vitest.config.ts configures the oxc JSX
// transform), but we keep the same createElement style as the sibling
// agent-activity test for consistency within this test directory.
const h = React.createElement;

function makeCitation(overrides: Partial<CitationData> = {}): CitationData {
  return {
    citationId: 'c-1',
    content: 'This is the cited text snippet.',
    chunkIndex: 1,
    recordId: 'r-1',
    recordName: 'Q3 Report.pdf',
    connector: 'GOOGLE_DRIVE',
    recordType: 'FILE',
    webUrl: 'https://example.com/doc',
    mimeType: 'application/pdf',
    extension: 'pdf',
    previewRenderable: true,
    hideWeburl: false,
    citationType: 'vectordb|document',
    ...overrides,
  };
}

/** One source with two citation chunks (chunkIndex 1 and 2), matching how `buildCitationMapsFromApi` links them. */
function makeCitationMaps(): CitationMaps {
  const c1 = makeCitation({ citationId: 'c-1', chunkIndex: 1 });
  const c2 = makeCitation({ citationId: 'c-2', chunkIndex: 2, content: 'A second cited snippet.' });
  return {
    citations: { 'c-1': c1, 'c-2': c2 },
    sources: { 'r-1': 'c-1' },
    sourcesOrder: ['r-1'],
    citationsOrder: { 1: 'c-1', 2: 'c-2' },
  };
}

function emptyCitationMaps(): CitationMaps {
  return { citations: {}, sources: {}, sourcesOrder: [], citationsOrder: {} };
}

function renderAccordion(props: Partial<React.ComponentProps<typeof SourcesAccordion>> = {}) {
  return render(
    h(
      Theme,
      null,
      h(SourcesAccordion, { citationMaps: makeCitationMaps(), ...props }),
    ),
  );
}

describe('SourcesAccordion', () => {
  it('renders nothing when there are no sources', () => {
    const { container } = render(
      h(Theme, null, h(SourcesAccordion, { citationMaps: emptyCitationMaps() })),
    );
    expect(container.querySelector('.rt-Box')).toBeNull();
  });

  it('renders nothing while streaming, even if sources exist', () => {
    const { container } = renderAccordion({ isStreaming: true });
    expect(screen.queryByText(/Sources/)).toBeNull();
    expect(container.querySelector('.rt-Box')).toBeNull();
  });

  it('shows a collapsed "N Sources" header by default, without the source list', () => {
    renderAccordion();
    expect(screen.getByText('1 Sources')).toBeTruthy();
    expect(screen.queryByText('Q3 Report.pdf')).toBeNull();
  });

  it('expands to reveal the source list when the header is clicked', () => {
    renderAccordion();
    fireEvent.click(screen.getByText('1 Sources'));
    expect(screen.getByText('Q3 Report.pdf')).toBeTruthy();
  });

  it('defaults the expanded view to Sources, then switches to Citations on toggle click', () => {
    renderAccordion();
    fireEvent.click(screen.getByText('1 Sources'));

    // Sources view shows one card per unique record (1 source, 2 citations for it).
    expect(screen.getByText('Q3 Report.pdf')).toBeTruthy();

    // The sub-toggle pill renders its label and count as separate text nodes.
    fireEvent.click(screen.getByText('Citation'));

    // Citations view shows one card per individual citation (2 chunks).
    expect(screen.getByText('This is the cited text snippet.')).toBeTruthy();
    expect(screen.getByText('A second cited snippet.')).toBeTruthy();
  });

  it('hides the Citations toggle entirely when citationsOrder is empty', () => {
    const citation = makeCitation({ citationId: 'c-1', chunkIndex: 1 });
    // `citationsOrder` empty (contrived, but exercises the accordion's own
    // count computation independent of `SourcesTab`/`CitationsTab`'s own logic).
    const maps: CitationMaps = {
      citations: { 'c-1': citation },
      sources: { 'r-1': 'c-1' },
      sourcesOrder: ['r-1'],
      citationsOrder: {},
    };
    renderAccordion({ citationMaps: maps });
    fireEvent.click(screen.getByText('1 Sources'));
    expect(screen.getByText('Q3 Report.pdf')).toBeTruthy();
    // Exact match: the accordion's own toggle pill renders label-only text
    // ("Citation"), distinct from `ReferenceCard`'s "1 Citation" count badge
    // (rendered from `citations`, independent of `citationsOrder`).
    expect(screen.queryByText('Citation')).toBeNull();
  });
});
