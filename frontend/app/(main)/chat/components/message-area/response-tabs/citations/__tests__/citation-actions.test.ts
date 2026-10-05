/**
 * Citations in an answer: how the backend's citation list becomes the numbered
 * sources a person sees, and what happens when they click one (open it where
 * it lives, or preview it at the cited page). The backend is faked at the axios
 * adapter; `window.open`, `URL.createObjectURL` and the router are faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import type { CitationApiResponse } from '@/chat/types';
import type { CitationData, CitationMaps, StreamingCitationData } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { useChatStore } = await import('@/chat/store');
const {
  buildCitationMapsFromApi,
  buildCitationMapsFromStreaming,
  getCitationCountBySource,
  getCitationCopyHref,
  formatSyncLabel,
} = await import('../utils');
const { useCitationActions } = await import('../use-citation-actions');

const KB = '/api/v1/knowledgeBase';

function apiCitation(id: string, chunkIndex: number, recordId: string, extra: Record<string, unknown> = {}): CitationApiResponse {
  return {
    citationId: id,
    citationData: {
      _id: id,
      content: `chunk ${chunkIndex}`,
      chunkIndex,
      citationType: 'vectordb|document',
      metadata: { recordId, recordName: `${recordId}.pdf`, connector: 'KB', mimeType: 'application/pdf', ...extra },
    },
  } as unknown as CitationApiResponse;
}

function citation(overrides: Partial<CitationData> = {}): CitationData {
  return {
    citationId: 'c1',
    content: 'Revenue grew 12%.',
    chunkIndex: 1,
    recordId: 'r1',
    recordName: 'Q3 report.pdf',
    connector: 'KB',
    recordType: 'FILE',
    mimeType: 'application/pdf',
    extension: 'pdf',
    pageNum: [4],
    previewRenderable: true,
    citationType: 'vectordb|document',
    ...overrides,
  } as CitationData;
}

describe('building citation maps from a saved answer', () => {
  it('numbers citations by chunk and lists each source once, in first-cited order', () => {
    const maps = buildCitationMapsFromApi([
      apiCitation('c1', 1, 'r1'),
      apiCitation('c2', 2, 'r2'),
      apiCitation('c3', 3, 'r1'),
      { citationId: 'broken', citationData: undefined } as unknown as CitationApiResponse,
      { citationId: 'no-meta', citationData: { chunkIndex: 9 } } as unknown as CitationApiResponse,
    ]);
    expect(maps.citationsOrder).toEqual({ 1: 'c1', 2: 'c2', 3: 'c3' });
    expect(maps.sourcesOrder).toEqual(['r1', 'r2']);
    expect(maps.sources).toEqual({ r1: 'c1', r2: 'c2' });
    expect(getCitationCountBySource(maps)).toEqual({ r1: 2, r2: 1 });
  });

  it('drops empty page numbers so no location badge shows without a number', () => {
    const maps = buildCitationMapsFromApi([
      apiCitation('c1', 1, 'r1', { pageNum: [null, 3, 'x'], blockNum: [null], recordName: '' }),
    ]);
    expect(maps.citations.c1).toMatchObject({ pageNum: [3], blockNum: undefined, recordName: 'Untitled Document' });
  });

  it('builds the same shape from citations streamed with the answer', () => {
    const streamed = [
      { chunkIndex: 2, content: 'b', citationType: 'x', metadata: { recordId: 'r2', recordName: 'b.pdf' } },
      { chunkIndex: 1, content: 'a', citationType: 'x', metadata: { recordId: 'r1', recordName: 'a.pdf', pageNum: 'oops' } },
      { chunkIndex: 3, content: 'no meta' },
    ] as unknown as StreamingCitationData[];
    const maps = buildCitationMapsFromStreaming(streamed);
    expect(maps.citationsOrder).toEqual({ 1: 'streaming-1', 2: 'streaming-2' });
    expect(maps.sourcesOrder).toEqual(['r2', 'r1']);
    expect(maps.citations['streaming-1'].pageNum).toBeUndefined();
  });
});

describe('small citation helpers', () => {
  it('offers a copyable link only when the source allows it', () => {
    expect(getCitationCopyHref(citation({ webUrl: ' https://drive.example/doc ' }))).toBe('https://drive.example/doc');
    expect(getCitationCopyHref(citation({ webUrl: 'https://x', hideWeburl: true }))).toBeUndefined();
    expect(getCitationCopyHref(citation({ webUrl: '  ' }))).toBeUndefined();
  });

  it('describes how long ago a source was synced', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-24T12:00:00Z'));
    const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
    expect(formatSyncLabel(undefined)).toBeUndefined();
    expect(formatSyncLabel(ago(-5000))).toBe('Synced just now');
    expect(formatSyncLabel(ago(30_000))).toBe('Synced just now');
    expect(formatSyncLabel(ago(5 * 60_000))).toBe('Synced 5m ago');
    expect(formatSyncLabel(ago(3 * 3600_000))).toBe('Synced 3h ago');
    expect(formatSyncLabel(ago(2 * 86400_000))).toBe('Synced 2d ago');
    expect(formatSyncLabel(ago(65 * 86400_000))).toBe('Synced 2mo ago');
    expect(formatSyncLabel(ago(800 * 86400_000))).toBe('Synced 2y ago');
    vi.useRealTimers();
  });
});

describe('clicking a citation', () => {
  const openWindow = vi.fn();
  const createObjectURL = vi.fn(() => 'blob:preview-1');

  beforeEach(() => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
    useChatStore.setState({ previewFile: null, previewMode: 'sidebar' });
    push.mockReset();
    openWindow.mockReset();
    createObjectURL.mockClear();
    vi.stubGlobal('open', openWindow);
    URL.createObjectURL = createObjectURL;
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const actions = () => renderHook(() => useCitationActions()).result.current;
  const preview = () => useChatStore.getState().previewFile;

  describe('open in its source', () => {
    it('opens a connector record in its own app, safely, without asking the server', async () => {
      const api = fakeApi({});
      await actions().onOpenInCollection(citation({ origin: 'CONNECTOR', webUrl: 'https://slack.example/p1' }));
      expect(openWindow).toHaveBeenCalledWith('https://slack.example/p1', '_blank', 'noopener,noreferrer');
      expect(api.sent).toHaveLength(0);
    });

    it('opens an uploaded file in its collection', async () => {
      fakeApi({ [`GET ${KB}/record/r1`]: { status: 200, data: { record: { origin: 'UPLOAD' }, knowledgeBase: { id: 'kb 1' } } } });
      await actions().onOpenInCollection(citation({ origin: 'UPLOAD' }));
      expect(push).toHaveBeenCalledWith('/knowledge-base?view=all-records&nodeType=app&nodeId=kb%201');
    });

    it('works out the origin when the citation does not say', async () => {
      fakeApi({ [`GET ${KB}/record/r1`]: { status: 200, data: { record: { origin: 'CONNECTOR', webUrl: 'https://jira.example/1' } } } });
      await actions().onOpenInCollection(citation());
      expect(openWindow).toHaveBeenCalledWith('https://jira.example/1', '_blank', 'noopener,noreferrer');
      expect(push).not.toHaveBeenCalled();
    });

    it('falls back to all records when the record cannot be loaded', async () => {
      fakeApi({ [`GET ${KB}/record/r1`]: { status: 500 } });
      await actions().onOpenInCollection(citation({ origin: 'UPLOAD' }));
      expect(push).toHaveBeenCalledWith('/knowledge-base?view=all-records');
    });
  });

  describe('preview', () => {
    const maps: CitationMaps = {
      citations: {
        c1: citation({ citationId: 'c1', chunkIndex: 1, pageNum: [4] }),
        c2: citation({ citationId: 'c2', chunkIndex: 2, recordId: 'other' }),
        c3: citation({ citationId: 'c3', chunkIndex: 3, pageNum: [9], blockNum: [2] }),
      },
      sources: {},
      sourcesOrder: [],
      citationsOrder: { 3: 'c3', 1: 'c1', 2: 'c2' },
    };

    it("opens a PDF at the cited page with that file's other citations listed", async () => {
      const api = fakeApi({
        [`GET ${KB}/record/r1`]: { status: 200, data: { record: { mimeType: 'application/pdf', recordName: 'Q3 report.pdf', previewRenderable: true, sizeInBytes: 2048 } } },
        [`GET ${KB}/stream/record/r1`]: { status: 200, data: new Blob(['%PDF'], { type: 'application/pdf' }) },
      });

      const pending = actions().onPreview(citation({ citationId: 'c3', pageNum: [9] }), maps);
      expect(preview()).toMatchObject({ isLoading: true, initialPage: 9 });
      await pending;

      expect(preview()).toMatchObject({
        id: 'r1',
        url: 'blob:preview-1',
        type: 'application/pdf',
        size: 2048,
        isLoading: false,
        initialPage: 9,
        initialCitationId: 'c3',
      });
      expect(preview()?.citations?.map((c) => c.id)).toEqual(['c1', 'c3']);
      expect(api.sent.find((r) => r.url.includes('/stream/'))?.config.params).toEqual({});
    });

    it('asks for a PDF conversion of a slide deck', async () => {
      const api = fakeApi({
        [`GET ${KB}/record/r1`]: { status: 200, data: { record: { mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', recordName: 'deck.pptx' } } },
        [`GET ${KB}/stream/record/r1`]: { status: 200, data: new Blob(['%PDF'], { type: 'application/pdf' }) },
      });
      await actions().onPreview(citation({ recordName: 'deck.pptx', mimeType: '', extension: 'pptx' }));
      expect(api.sent.find((r) => r.url.includes('/stream/'))?.config.params).toEqual({ convertTo: 'application/pdf' });
      expect(preview()?.type).toBe('application/pdf');
    });

    it('hands a Word document to the renderer as a Blob, with no blob URL', async () => {
      const docx = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
      fakeApi({
        [`GET ${KB}/record/r1`]: { status: 200, data: { record: { mimeType: docx, recordName: 'memo.docx' } } },
        [`GET ${KB}/stream/record/r1`]: { status: 200, data: new Blob(['PK'], { type: docx }) },
      });
      await actions().onPreview(citation({ recordName: 'memo.docx', mimeType: docx, extension: 'docx' }));
      expect(preview()?.url).toBe('');
      expect(preview()?.blob).toBeInstanceOf(Blob);
      expect(createObjectURL).not.toHaveBeenCalled();
    });

    it('does not download a file that cannot be previewed', async () => {
      const api = fakeApi({
        [`GET ${KB}/record/r1`]: { status: 200, data: { record: { mimeType: 'application/zip', previewRenderable: false, webUrl: 'https://drive.example/z' } } },
      });
      await actions().onPreview(citation());
      expect(api.count(`GET ${KB}/stream/record/r1`)).toBe(0);
      expect(preview()).toMatchObject({ previewRenderable: false, webUrl: 'https://drive.example/z', isLoading: false });
    });

    it('shows an error in the preview when the record has no details', async () => {
      fakeApi({ [`GET ${KB}/record/r1`]: { status: 200, data: {} } });
      await actions().onPreview(citation());
      expect(preview()).toMatchObject({ isLoading: false, error: 'Record details unavailable' });
    });
  });
});
