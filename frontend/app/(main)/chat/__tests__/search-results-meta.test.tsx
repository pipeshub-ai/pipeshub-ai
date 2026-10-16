import React from 'react';
import { cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../store';
import { SearchResultsView } from '../components/search-results/search-results-view';

const { searchResultsExtras } = vi.hoisted(() => ({
  searchResultsExtras: vi.fn(() => null),
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
  }),
}));

vi.mock('@/config', () => ({
  SearchResultsExtras: searchResultsExtras,
}));

vi.mock('../components/message-area/response-tabs/citations/use-citation-actions', () => ({
  useCitationActions: () => ({ onPreview: vi.fn() }),
}));

vi.mock('../components/search-results/search-result-card', () => ({
  SearchResultCard: () => null,
}));

describe('search response meta', () => {
  beforeEach(() => {
    searchResultsExtras.mockClear();
    useChatStore.setState({
      searchResults: [],
      searchId: null,
      searchQuery: '',
      searchResponseMeta: null,
      searchError: null,
      isSearching: false,
    });
  });

  afterEach(() => cleanup());

  it('clears successful-search metadata when a new search starts or fails', () => {
    const responseMeta = { extra: 42 };

    useChatStore.getState().setSearchResults([], 'search-1', 'first query', responseMeta);
    expect(useChatStore.getState().searchResponseMeta).toEqual(responseMeta);

    useChatStore.getState().setIsSearching(true);
    expect(useChatStore.getState().searchResponseMeta).toBeNull();

    useChatStore.getState().setIsSearching(false);
    useChatStore.getState().setSearchResults([], 'search-2', 'second query', responseMeta);
    useChatStore.getState().setSearchError('Search failed');
    expect(useChatStore.getState().searchResponseMeta).toBeNull();
  });

  it('does not render the extras slot while a search error is displayed', () => {
    useChatStore.setState({
      searchQuery: 'failed query',
      searchError: 'Search failed',
      searchResponseMeta: { extra: 42 },
      isSearching: false,
    });

    render(
      <Theme>
        <SearchResultsView />
      </Theme>,
    );

    expect(searchResultsExtras).not.toHaveBeenCalled();
  });
});
