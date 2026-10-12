import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { KnowledgeHubApi } from '../api';
import { FilterBar } from '../components/filter-bar';
import { useKnowledgeBaseStore } from '../store';
import type { KnowledgeHubApiResponse } from '../types';
import { hubResponse, installBrowserShims, renderInTheme } from './kb-page-harness';

const STATUS_OPTIONS: KnowledgeHubApiResponse = hubResponse([], {
  filters: {
    applied: {
      q: null,
      nodeTypes: null,
      recordTypes: null,
      origins: null,
      connectorIds: null,
      kbIds: null,
      indexingStatus: null,
      createdAt: null,
      updatedAt: null,
      size: null,
      sortBy: 'name',
      sortOrder: 'asc',
    },
    available: {
      nodeTypes: [],
      recordTypes: [],
      origins: [],
      connectors: [],
      kbs: [],
      indexingStatus: [
        { id: 'QUEUED', label: 'Queued' },
        { id: 'IN_PROGRESS', label: 'In Progress' },
      ],
    },
  },
});

beforeEach(() => {
  installBrowserShims();
  useKnowledgeBaseStore.setState(useKnowledgeBaseStore.getInitialState(), true);
  useKnowledgeBaseStore.setState({ allRecordsTableData: STATUS_OPTIONS });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('FilterBar', () => {
  it('keeps a status selection local until Apply filters is clicked', () => {
    const setAllRecordsFilter = vi.spyOn(useKnowledgeBaseStore.getState(), 'setAllRecordsFilter');
    const getAllRootItems = vi.spyOn(KnowledgeHubApi, 'getAllRootItems');
    renderInTheme(<FilterBar pageViewMode="all-records" />);

    fireEvent.click(screen.getByRole('button', { name: /Status/ }));
    fireEvent.click(screen.getByText('Queued'));

    expect(setAllRecordsFilter).not.toHaveBeenCalled();
    expect(getAllRootItems).not.toHaveBeenCalled();
    expect(useKnowledgeBaseStore.getState().allRecordsFilter.indexingStatus).toBeUndefined();

    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

    expect(setAllRecordsFilter).toHaveBeenCalledTimes(1);
    expect(useKnowledgeBaseStore.getState().allRecordsFilter.indexingStatus).toEqual(['QUEUED']);
    expect(screen.queryByRole('button', { name: 'Apply filters' })).toBeNull();
  });

  it('gives Apply filters a 44px touch target on a phone', async () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = (query: string) =>
      ({
        matches: query.includes('max-width'),
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList;

    try {
      renderInTheme(<FilterBar pageViewMode="all-records" />);
      fireEvent.click(screen.getByRole('button', { name: /Status/ }));
      fireEvent.click(screen.getByText('Queued'));

      expect(screen.getByRole('button', { name: 'Apply filters' }).style.minHeight).toBe('44px');
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('clears applied filters immediately', () => {
    useKnowledgeBaseStore.setState({ allRecordsFilter: { indexingStatus: ['QUEUED'] } });
    renderInTheme(<FilterBar pageViewMode="all-records" />);

    fireEvent.click(screen.getByText('Clear Filter'));

    expect(useKnowledgeBaseStore.getState().allRecordsFilter).toEqual({});
    expect(screen.queryByText('Clear Filter')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Apply filters' })).toBeNull();
  });
});
