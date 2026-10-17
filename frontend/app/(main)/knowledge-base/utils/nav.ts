/** Build a /knowledge-base URL that preserves the current view mode. */
export function buildNavUrl(isAllRecordsMode: boolean, params: Record<string, string>): string {
  const urlParams = new URLSearchParams();
  if (isAllRecordsMode) {
    urlParams.set('view', 'all-records');
  }
  Object.entries(params).forEach(([key, value]) => {
    if (value) urlParams.set(key, value);
  });
  return `/knowledge-base?${urlParams.toString()}`;
}

/**
 * Navigate inside /knowledge-base, where only the query string changes.
 * router.push fetches the route first (index.txt?_rsc on the static export)
 * and commits the URL only when that returns, so every data request of the
 * click would wait one network round trip. Next keeps useSearchParams in step
 * with history.pushState, which needs no fetch.
 */
export function pushKbUrl(url: string): void {
  if (typeof window === 'undefined') return;
  // trailingSlash is on: the address the router itself would write.
  window.history.pushState(null, '', url.replace(/^\/knowledge-base(?=\?|$)/, '/knowledge-base/'));
}

/** The same, replacing the current history entry (filters, sort and paging written back to the address). */
export function replaceKbUrl(url: string): void {
  if (typeof window === 'undefined') return;
  window.history.replaceState(null, '', url.replace(/^\/knowledge-base(?=\?|$)/, '/knowledge-base/'));
}

/** Derive isAllRecordsMode from URLSearchParams (or Next.js ReadonlyURLSearchParams). */
export function getIsAllRecordsMode(searchParams: { get(key: string): string | null }): boolean {
  return searchParams.get('view') === 'all-records';
}
