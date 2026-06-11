/**
 * Bounded-concurrency helpers for fanning out async work without serializing it
 * behind a single slow item or, conversely, flooding a downstream service with
 * an unbounded number of in-flight requests.
 */

/**
 * Runs `mapper` over `items` with at most `limit` invocations in flight at once,
 * returning results in input order.
 *
 * It is a fixed-size worker pool: `limit` workers each pull the next unclaimed
 * item, await it, then pull the next. So a single slow item (e.g. a large file
 * upload) only ever occupies ONE slot — the remaining items keep flowing through
 * the other workers instead of waiting in line behind it. This is the fix for a
 * mixed upload batch where one big file otherwise stalls every smaller file.
 *
 * `mapper` is expected to handle its own per-item errors (e.g. record a failure
 * and return a sentinel). If it rejects, the returned promise rejects with that
 * error and items not yet started are not launched.
 *
 * @param items  The work items. An empty array resolves to `[]`.
 * @param limit  Max concurrent invocations. Clamped to `[1, items.length]`.
 * @param mapper Async function applied to each item; receives `(item, index)`.
 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  mapper: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  if (items.length === 0) return results;

  const workerCount = Math.max(1, Math.min(Math.floor(limit) || 1, items.length));
  let next = 0;

  const worker = async (): Promise<void> => {
    // Claim-and-advance the shared cursor. JS is single-threaded, so the
    // read+increment is atomic with respect to the other workers.
    while (next < items.length) {
      const current = next;
      next += 1;
      // In-bounds by the loop guard; cast past noUncheckedIndexedAccess.
      results[current] = await mapper(items[current] as T, current);
    }
  };

  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}
