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

/** Derive isAllRecordsMode from URLSearchParams (or Next.js ReadonlyURLSearchParams). */
export function getIsAllRecordsMode(searchParams: { get(key: string): string | null }): boolean {
  return searchParams.get('view') === 'all-records';
}
