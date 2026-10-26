import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';

import '@/lib/__tests__/test-i18n';

const router = { replace: vi.fn(), push: vi.fn() };
const search = { params: new URLSearchParams() };
const api = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), listVersions: vi.fn() }));
const toastError = vi.hoisted(() => vi.fn());

vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => search.params,
}));
vi.mock('../api', () => ({ ArtifactsApi: api }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: { streamRecord: vi.fn().mockResolvedValue(new Blob(['x'])) },
}));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/file-preview', () => ({
  FilePreviewSidebar: ({ file }: { file: { name: string } }) => <div role="dialog" aria-label={file.name} />,
}));
vi.mock('@/app/components/file-preview/utils', () => ({
  isLegacyWordDocFile: () => false,
  isPresentationFile: () => false,
  resolvePreviewMimeAfterStream: (mime: string) => mime,
}));
vi.mock('@/app/components/share', () => ({ ShareSidebar: () => null }));
vi.mock('../components/header', () => ({ ArtifactsHeader: () => null }));
vi.mock('../components/filter-bar', () => ({ ArtifactsFilterBar: () => null }));
vi.mock('../components/artifacts-data-table', () => ({ ArtifactsDataTable: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { error: toastError } }));

import ArtifactsPage from '../page';
import { useArtifactsStore } from '../store';

const DETAIL = { artifactId: 'a9', name: 'pricing.pdf', artifactType: 'DOCUMENT', version: 1, role: 'READER' };

beforeEach(() => {
  useArtifactsStore.setState(useArtifactsStore.getInitialState(), true);
  URL.createObjectURL = vi.fn(() => 'blob:preview');
  URL.revokeObjectURL = vi.fn();
  api.list.mockResolvedValue({
    items: [],
    pagination: { page: 1, limit: 50, totalCount: 0, totalPages: 0 },
    counts: { all: 0, mine: 0, shared: 0 },
  });
  api.get.mockResolvedValue(DETAIL);
  api.listVersions.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('notification deep link', () => {
  it('opens the shared tab and the artifact, then drops the id from the URL', async () => {
    search.params = new URLSearchParams('tab=shared&artifactId=a9');
    render(<ArtifactsPage />);

    expect(await screen.findByRole('dialog', { name: 'pricing.pdf' })).toBeTruthy();
    expect(api.get).toHaveBeenCalledWith('a9');
    expect(router.replace).toHaveBeenCalledWith('/artifacts/?tab=shared', { scroll: false });
    expect(useArtifactsStore.getState().ownership).toBe('shared');
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ ownership: 'shared' })));
  });

  it('works when the page is already open on another tab', async () => {
    search.params = new URLSearchParams('tab=mine');
    const { rerender } = render(<ArtifactsPage />);
    await waitFor(() => expect(useArtifactsStore.getState().ownership).toBe('mine'));
    router.replace.mockClear();

    search.params = new URLSearchParams('tab=shared&artifactId=a9');
    rerender(<ArtifactsPage />);

    expect(await screen.findByRole('dialog', { name: 'pricing.pdf' })).toBeTruthy();
    expect(useArtifactsStore.getState().ownership).toBe('shared');
    expect(router.replace).toHaveBeenCalledWith('/artifacts/?tab=shared', { scroll: false });
    expect(router.replace).not.toHaveBeenCalledWith('/artifacts/?tab=mine', { scroll: false });
  });

  it('says so when the artifact is no longer shared', async () => {
    api.get.mockRejectedValue(new Error('404'));
    search.params = new URLSearchParams('tab=shared&artifactId=gone');
    render(<ArtifactsPage />);

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('This artifact is no longer available'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
