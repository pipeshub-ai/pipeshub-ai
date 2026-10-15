import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../../types';
import { useArtifactsStore } from '../../store';
import { ArtifactsListView } from '../artifacts-list-view';
import { ArtifactsGridView } from '../artifacts-grid-view';
import { ArtifactsDataTable } from '../artifacts-data-table';
import { ArtifactsFilterBar } from '../filter-bar';

vi.mock('@/app/components/ui', () => ({
  FileIcon: () => null,
  EmptyIcon: () => null,
  FilterDropdown: () => null,
  DateRangePicker: () => null,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const viewport = vi.hoisted(() => ({ isMobile: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => viewport.isMobile }));

const PAGINATION = { page: 1, limit: 50, totalCount: 2, totalPages: 1 };

function artifact(overrides: Partial<ArtifactListItem>): ArtifactListItem {
  return { artifactId: 'a1', name: 'report.csv', artifactType: 'DATA_FILE', version: 1, ...overrides };
}

const mine = artifact({ artifactId: 'a1', name: 'mine.csv', role: 'OWNER' });
const sharedWithMe = artifact({ artifactId: 'a2', name: 'theirs.csv', role: 'READER' });

const handlers = {
  onPreview: vi.fn(),
  onDownload: vi.fn(),
  onOpenChat: vi.fn(),
  onShare: vi.fn(),
  onPageChange: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Share action', () => {
  it('is offered in the list only on artifacts the viewer owns', () => {
    render(
      <Theme>
        <ArtifactsListView
          items={[mine, sharedWithMe]}
          sortBy="createdAtTimestamp"
          sortOrder="desc"
          onSort={vi.fn()}
          pagination={PAGINATION}
          {...handlers}
        />
      </Theme>,
    );

    const shareButtons = screen.getAllByRole('button', { name: 'Share' });
    expect(shareButtons).toHaveLength(1);
    expect(within(screen.getByRole('button', { name: 'theirs.csv' })).queryByRole('button', { name: 'Share' })).toBeNull();

    fireEvent.click(shareButtons[0]);
    expect(handlers.onShare).toHaveBeenCalledWith(mine);
    expect(handlers.onPreview).not.toHaveBeenCalled();
  });

  it('is offered in the grid only on artifacts the viewer owns', () => {
    render(
      <Theme>
        <ArtifactsGridView items={[mine, sharedWithMe]} pagination={PAGINATION} {...handlers} />
      </Theme>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Share' }));
    expect(handlers.onShare).toHaveBeenCalledWith(mine);
    expect(handlers.onPreview).not.toHaveBeenCalled();
  });
});

describe('Shared with me tab', () => {
  it('says nothing has been shared yet instead of pointing at chat', () => {
    render(
      <Theme>
        <ArtifactsDataTable
          items={[]}
          viewMode="list"
          ownership="shared"
          isLoading={false}
          error={null}
          sortBy="createdAtTimestamp"
          sortOrder="desc"
          onSort={vi.fn()}
          pagination={{ ...PAGINATION, totalCount: 0, totalPages: 0 }}
          {...handlers}
        />
      </Theme>,
    );

    expect(screen.getByText('Nothing shared with you yet')).toBeTruthy();
    expect(screen.queryByText('No artifacts yet')).toBeNull();
  });
});

describe('Ownership tabs', () => {
  beforeEach(() => {
    viewport.isMobile = false;
    useArtifactsStore.setState({ ownership: 'all', page: 3, counts: { all: 12, mine: 8, shared: 4 } });
  });

  it('show every tab with its count and switch to page 1 of the chosen tab', () => {
    render(
      <Theme>
        <ArtifactsFilterBar />
      </Theme>,
    );

    expect(screen.getByRole('radio', { name: 'All artifacts (12)' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Mine (8)' })).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'Shared with me (4)' }));

    expect(useArtifactsStore.getState().ownership).toBe('shared');
    expect(useArtifactsStore.getState().page).toBe(1);
  });

  it('use short labels on a phone so all three fit', () => {
    viewport.isMobile = true;
    render(
      <Theme>
        <ArtifactsFilterBar />
      </Theme>,
    );

    expect(screen.getByRole('radio', { name: 'All (12)' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Shared (4)' })).toBeTruthy();
  });
});
