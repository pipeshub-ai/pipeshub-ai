import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../../types';
import { ArtifactsDataTable } from '../artifacts-data-table';

vi.mock('@/app/components/ui', () => ({ FileIcon: () => null, EmptyIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../artifact-thumbnail', () => ({ ArtifactThumbnail: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const ITEM: ArtifactListItem = { artifactId: 'a1', name: 'mine.csv', artifactType: 'DATA_FILE', version: 1 };

function renderTable(props: Partial<React.ComponentProps<typeof ArtifactsDataTable>>) {
  const onRetry = vi.fn();
  const onClearFilters = vi.fn();
  render(
    <Theme>
      <ArtifactsDataTable
        items={[]}
        viewMode="list"
        ownership="all"
        isLoading={false}
        error={null}
        hasActiveFilters={false}
        sortBy="createdAtTimestamp"
        sortOrder="desc"
        onSort={vi.fn()}
        onPreview={vi.fn()}
        onDownload={vi.fn()}
        onOpenChat={vi.fn()}
        onShare={vi.fn()}
        onDelete={vi.fn()}
        onRetry={onRetry}
        onClearFilters={onClearFilters}
        pagination={{ page: 1, limit: 50, totalCount: 0, totalPages: 0 }}
        onPageChange={vi.fn()}
        {...props}
      />
    </Theme>,
  );
  return { onRetry, onClearFilters };
}

afterEach(() => cleanup());

describe('ArtifactsDataTable states', () => {
  it.each(['list', 'grid'] as const)('shows a skeleton, not text, while the first %s page loads', (viewMode) => {
    renderTable({ isLoading: true, viewMode });

    expect(document.querySelector('[aria-busy="true"]')).toBeTruthy();
    expect(screen.queryByText('Loading...')).toBeNull();
  });

  it('offers a retry when loading failed', () => {
    const { onRetry } = renderTable({ error: "Couldn't load artifacts" });

    expect(screen.getByText("Couldn't load artifacts")).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('explains an empty gallery', () => {
    renderTable({});

    expect(screen.getByText('No artifacts yet')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  });

  it('a search on the Shared with me tab with no results still offers to clear it', () => {
    const { onClearFilters } = renderTable({ hasActiveFilters: true, ownership: 'shared' });

    expect(screen.getByText('No artifacts match your filters')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(onClearFilters).toHaveBeenCalledOnce();
  });

  it('tells a filtered search with no results apart from an empty gallery, and clears the filters', () => {
    const { onClearFilters } = renderTable({ hasActiveFilters: true });

    expect(screen.getByText('No artifacts match your filters')).toBeTruthy();
    expect(screen.queryByText('No artifacts yet')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(onClearFilters).toHaveBeenCalledOnce();
  });

  it('keeps the rows on screen and marks them busy while refreshing', () => {
    renderTable({ isLoading: true, items: [ITEM], pagination: { page: 1, limit: 50, totalCount: 1, totalPages: 1 } });

    expect(screen.getByRole('button', { name: 'mine.csv' })).toBeTruthy();
    expect(document.querySelector('[aria-busy="true"]')).toBeTruthy();
  });
});
