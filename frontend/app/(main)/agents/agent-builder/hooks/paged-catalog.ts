/**
 * A catalog the agent builder offers in its palette (the org's collections, connected apps, actions), read a page
 * at a time (R30-LAT-FU-1): the builder's load asks for the first page whatever the org holds, "Show more" asks for
 * the next, and a palette search asks the server for what matches. The agent's own grants never come from here:
 * the agent's answer names them.
 */

/** One page of a catalog and where the next one starts; `next` null: the last page. */
export interface CatalogPage<T, C> {
  items: T[];
  next: C | null;
  /** How many the whole catalog holds, when the server says. */
  total?: number | null;
}

/** Reads a page after `cursor` (null: the first), of the items matching `query` ('' : all). */
export type CatalogPageReader<T, C> = (cursor: C | null, query: string) => Promise<CatalogPage<T, C>>;

export interface CatalogState<T, C> {
  /** What the pages read list: '' for the whole catalog, or a query the list itself was narrowed to. */
  listQuery: string;
  /** The pages read so far, in order. */
  pages: T[];
  next: C | null;
  total: number | null;
  /** What the server found for `query`; empty while not searching. */
  query: string;
  hits: T[];
  /** Whether `hits` is all the server holds for `query`. */
  hitsComplete: boolean;
}

export const emptyCatalog = <T, C>(): CatalogState<T, C> => ({
  listQuery: '',
  pages: [],
  next: null,
  total: null,
  query: '',
  hits: [],
  hitsComplete: true,
});

const dedupe = <T>(items: readonly T[], key: (item: T) => string): T[] => {
  const seen = new Set<string>();
  return items.filter((item) => {
    const k = key(item);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
};

/** What the palette shows of a catalog: the pages read, then any search hits not among them. */
export const catalogItems = <T, C>(state: CatalogState<T, C>, key: (item: T) => string): T[] =>
  dedupe([...state.pages, ...state.hits], key);

export const catalogHasMore = <T, C>(state: CatalogState<T, C>): boolean => state.next !== null;

/**
 * The catalog's operations over a reader, as pure steps: each returns the next state from the current one, so a
 * React hook can hold it and a test can drive it without one.
 */
export const catalogSteps = <T, C>(read: CatalogPageReader<T, C>) => ({
  async first(listQuery = ''): Promise<CatalogState<T, C>> {
    const q = listQuery.trim();
    const page = await read(null, q);
    return { ...emptyCatalog<T, C>(), listQuery: q, pages: page.items, next: page.next, total: page.total ?? null };
  },

  async more(state: CatalogState<T, C>): Promise<CatalogState<T, C>> {
    if (state.next === null) return state;
    const page = await read(state.next, state.listQuery);
    return { ...state, pages: [...state.pages, ...page.items], next: page.next, total: page.total ?? state.total };
  },

  /** Every page after those read; only for "use all of them" (a group dropped whole), never on load. */
  async rest(state: CatalogState<T, C>): Promise<CatalogState<T, C>> {
    let current = state;
    const seen = new Set<string>();
    while (current.next !== null) {
      // Guards a server that answers the same cursor again instead of advancing.
      const marker = JSON.stringify(current.next);
      if (seen.has(marker)) break;
      seen.add(marker);
      current = await this.more(current);
    }
    return current;
  },

  /** The first page of what matches `query`; an empty query clears the hits. */
  async search(state: CatalogState<T, C>, query: string): Promise<CatalogState<T, C>> {
    const q = query.trim();
    if (q === '') return { ...state, query: '', hits: [], hitsComplete: true };
    const page = await read(null, q);
    return { ...state, query: q, hits: page.items, hitsComplete: page.next === null };
  },
});
