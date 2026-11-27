/** The key of a refused reap's label set (CLEANUP-G6): the same labels are one pending reap. */
export const pendingReapId = (
  labels: Readonly<Record<string, string>>,
): string =>
  Object.keys(labels)
    .sort()
    .map((key) => `${key}=${labels[key] ?? ''}`)
    .join('&');
