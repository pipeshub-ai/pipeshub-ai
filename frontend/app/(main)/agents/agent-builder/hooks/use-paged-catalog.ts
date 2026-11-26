'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import {
  catalogHasMore,
  catalogItems,
  catalogSteps,
  emptyCatalog,
  type CatalogPageReader,
  type CatalogState,
} from './paged-catalog';

export interface PagedCatalog<T> {
  /** The pages read, then the current search's hits not among them. */
  items: T[];
  hasMore: boolean;
  /** How many the catalog holds, when the server says. */
  total: number | null;
  loadingMore: boolean;
  /** The first page of the list for `listQuery` ('' : all), replacing what was read. Rejects as the reader does. */
  first: (listQuery?: string) => Promise<void>;
  /** The next page; a failure keeps what was read. */
  more: () => Promise<void>;
  /** Every remaining page, for a whole group dropped on the canvas. */
  rest: () => Promise<T[]>;
  /** The server's first page for a palette query; '' clears it. A failure keeps the pages read. */
  search: (query: string) => Promise<void>;
  clear: () => void;
}

/** A palette catalog read a page at a time over `read` (paged-catalog.ts), in React state. */
export function usePagedCatalog<T, C>(
  read: CatalogPageReader<T, C>,
  key: (item: T) => string,
  label: string,
): PagedCatalog<T> {
  // `read` must be stable (a module function or a callback): a new one starts the steps over.
  const steps = useMemo(() => catalogSteps<T, C>(read), [read]);
  const [state, setState] = useState<CatalogState<T, C>>(emptyCatalog);
  const stateRef = useRef(state);
  const [loadingMore, setLoadingMore] = useState(false);
  // A newer list or search wins over one still in flight.
  const listGen = useRef(0);
  const searchGen = useRef(0);

  const commit = useCallback((next: CatalogState<T, C>) => {
    stateRef.current = next;
    setState(next);
  }, []);
  const withList = useCallback(
    (list: CatalogState<T, C>) => {
      const { query, hits, hitsComplete } = stateRef.current;
      commit({ ...list, query, hits, hitsComplete });
    },
    [commit],
  );

  const first = useCallback(
    async (listQuery = '') => {
      const gen = ++listGen.current;
      const list = await steps.first(listQuery);
      if (gen === listGen.current) withList(list);
    },
    [steps, withList],
  );

  const more = useCallback(async () => {
    const gen = listGen.current;
    setLoadingMore(true);
    try {
      const list = await steps.more(stateRef.current);
      if (gen === listGen.current) withList(list);
    } catch (error) {
      console.error(`Failed to load more ${label}:`, error);
    } finally {
      setLoadingMore(false);
    }
  }, [label, steps, withList]);

  const rest = useCallback(async () => {
    const gen = listGen.current;
    setLoadingMore(true);
    try {
      const list = await steps.rest(stateRef.current);
      if (gen === listGen.current) withList(list);
      return catalogItems(list, key);
    } catch (error) {
      console.error(`Failed to load every ${label}:`, error);
      return catalogItems(stateRef.current, key);
    } finally {
      setLoadingMore(false);
    }
  }, [key, label, steps, withList]);

  const search = useCallback(
    async (query: string) => {
      const gen = ++searchGen.current;
      try {
        const found = await steps.search(stateRef.current, query);
        if (gen !== searchGen.current) return;
        commit({ ...stateRef.current, query: found.query, hits: found.hits, hitsComplete: found.hitsComplete });
      } catch (error) {
        console.error(`Failed to search ${label}:`, error);
      }
    },
    [commit, label, steps],
  );

  const clear = useCallback(() => {
    listGen.current += 1;
    searchGen.current += 1;
    commit(emptyCatalog());
  }, [commit]);

  const items = useMemo(() => catalogItems(state, key), [state, key]);
  return {
    items,
    hasMore: catalogHasMore(state),
    total: state.total,
    loadingMore,
    first,
    more,
    rest,
    search,
    clear,
  };
}
