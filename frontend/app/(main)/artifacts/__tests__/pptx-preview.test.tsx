import React, { useSyncExternalStore } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../types';
import { useArtifactsStore } from '../store';
import ArtifactsPage from '../page';

// jsdom has no matchMedia; the artifacts header uses it for the mobile layout.
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }),
});

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/artifacts',
  useSearchParams: () =>
    useSyncExternalStore(
      () => () => {},
      () => search.params,
    ),
}));

const api = vi.hoisted(() => ({
  list: vi.fn(),
  listVersions: vi.fn(),
  streamRecord: vi.fn(),
  streamDownloadRecord: vi.fn(),
}));

vi.mock('../api', () => ({
  ArtifactsApi: {
    list: api.list,
    listVersions: api.listVersions,
    get: vi.fn(),
  },
}));

vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    streamRecord: api.streamRecord,
    streamDownloadRecord: api.streamDownloadRecord,
  },
}));

vi.mock('@/app/components/file-preview', () => ({
  FilePreviewSidebar: ({
    file,
    isLoading,
    onDownload,
  }: {
    file: { type: string };
    isLoading?: boolean;
    onDownload?: () => void;
  }) => (
    <section aria-label="File preview">
      {isLoading ? <p>Loading preview</p> : <p>preview-type:{file.type}</p>}
      <button type="button" onClick={onDownload}>Download preview</button>
    </section>
  ),
}));

const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';

function artifact(overrides: Partial<ArtifactListItem>): ArtifactListItem {
  return {
    artifactId: 'art-1',
    name: 'deck.pptx',
    artifactType: 'PRESENTATION',
    mimeType: PPTX_MIME,
    version: 2,
    ...overrides,
  };
}

function pageOf(item: ArtifactListItem) {
  return {
    items: [item],
    pagination: { page: 1, limit: 50, totalCount: 1, totalPages: 1 },
  };
}

beforeEach(() => {
  search.params = new URLSearchParams();
  useArtifactsStore.setState({
    items: [],
    page: 1,
    search: '',
    artifactTypes: [],
    dateFrom: undefined,
    dateTo: undefined,
    sortBy: 'createdAtTimestamp',
    sortOrder: 'desc',
    viewMode: 'list',
    isLoading: false,
    error: null,
  });
  api.listVersions.mockResolvedValue([{ version: 2, sizeBytes: 10, contentHash: 'h', createdAt: 1 }]);
  api.streamRecord.mockResolvedValue(new Blob(['%PDF'], { type: 'application/pdf' }));
  URL.createObjectURL = vi.fn(() => 'blob:preview');
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openArtifact(item: ArtifactListItem) {
  api.list.mockResolvedValue(pageOf(item));
  render(
    <Theme>
      <ArtifactsPage />
    </Theme>,
  );
  fireEvent.click(await screen.findByRole('button', { name: item.name }));
}

describe('artifact pptx preview', () => {
  it('requests PDF conversion with the MIME the stream API recognizes', async () => {
    await openArtifact(artifact({}));

    await waitFor(() => {
      expect(api.streamRecord).toHaveBeenCalledWith('art-1', {
        version: 2,
        convertTo: 'application/pdf',
      });
    });
    await screen.findByText('preview-type:application/pdf');
    fireEvent.click(screen.getByRole('button', { name: 'Download preview' }));
    await waitFor(() => {
      expect(api.streamDownloadRecord).toHaveBeenCalledWith('art-1', 'deck.pptx', expect.objectContaining({ version: 2 }));
    });
  });

  it('converts a presentation named without an extension when the MIME is PowerPoint', async () => {
    await openArtifact(artifact({ name: 'Quarterly deck', mimeType: 'application/vnd.ms-powerpoint' }));

    await waitFor(() => {
      expect(api.streamRecord).toHaveBeenCalledWith('art-1', {
        version: 2,
        convertTo: 'application/pdf',
      });
    });
  });

  it('does not request conversion for a file the browser can preview as-is', async () => {
    await openArtifact(artifact({
      name: 'notes.pdf',
      artifactType: 'DOCUMENT',
      mimeType: 'application/pdf',
    }));

    await waitFor(() => {
      expect(api.streamRecord).toHaveBeenCalledWith('art-1', { version: 2 });
    });
  });
});
