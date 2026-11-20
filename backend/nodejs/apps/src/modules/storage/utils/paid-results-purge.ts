import { PAID_RESULT_LIST_PAGE } from '../constants/constants';
import type { StorageServiceAdapter } from '../adapter/base-storage.adapter';
import { REFRESH_MARKER_FOLDER } from './paid-results';

/**
 * Removes the objects under `root` last written before `before` (ms): the purge of a refresh. A store's write time is
 * its own clock (whole seconds on S3 and Azure), so the caller passes the cutoff the store itself returned.
 *
 * What an object overwritten between the listing and the delete does (a refresh's own write, or a bump):
 * - Where the store deletes by version (`conditionalDelete`), the delete names the listed ETag and leaves a changed object.
 * - Elsewhere (a store that ignores the condition, MinIO), and for an object listed with no version, the same page is
 *   listed again immediately before the plain delete and an object whose write time or ETag moved, or that is no longer
 *   older than the cutoff, is kept. What is left is the window between that second listing and the delete: one request.
 *   A write that lands in it is a bump or a re-store of an object the cutoff already covers, which the refresh marker
 *   and a result's `originMs` refuse to reuse; a refresh's own result is written after the cutoff and so is not removed
 *   by a purge that, like every purge, compares each object's time with the cutoff just before deleting it.
 * - A probe that could not tell answers 503 (`unavailable`): purging unconditionally on an unknown is never done.
 */
export async function purgePaidResultsOlderThan(
  adapter: StorageServiceAdapter,
  root: string,
  before: number,
  unavailable: new (message: string) => Error,
  pageSize: number = PAID_RESULT_LIST_PAGE,
): Promise<void> {
  let after: string | undefined;
  const capabilities = await adapter.conditionalCapabilities();
  if (capabilities.inconclusive === true) {
    throw new unavailable(
      'The store could not say whether it deletes by version; try again',
    );
  }
  const conditional = capabilities.conditionalDelete;
  type Listed = { key: string; lastModifiedMs: number; etag?: string };
  const doomedOf = (objects: Listed[]): Listed[] =>
    objects.filter(
      (o) =>
        o.key.startsWith(`${root}/`) &&
        !o.key.startsWith(`${root}/${REFRESH_MARKER_FOLDER}/`) &&
        o.lastModifiedMs < before,
    );
  for (;;) {
    const page = await adapter.listObjects(`${root}/`, {
      after,
      limit: pageSize,
    });
    // The refresh marker outlives every cutoff purge: it is how a late bump or re-store knows a refresh began.
    const doomed = doomedOf(page.objects);
    const guarded = conditional
      ? doomed.flatMap((o) =>
          o.etag === undefined ? [] : [{ key: o.key, etag: o.etag }],
        )
      : [];
    const guardedKeys = new Set(guarded.map((g) => g.key));
    const plain = doomed.filter((o) => !guardedKeys.has(o.key));
    if (plain.length > 0) {
      // Listed again right before the delete. An insert or a delete in the key range can move a key off the page this
      // second listing returns, so the listing walks on until it has covered every key of the first page: a key missing
      // from a covered range is gone, never "probably shifted". What moved since the first listing stays.
      const lastPlain = plain[plain.length - 1]?.key ?? '';
      const fresh = new Map<string, Listed>();
      let cursor = after;
      for (;;) {
        const again = await adapter.listObjects(`${root}/`, {
          after: cursor,
          limit: pageSize,
        });
        for (const o of again.objects) fresh.set(o.key, o);
        const last = again.objects[again.objects.length - 1]?.key;
        if (
          again.next === undefined ||
          (last !== undefined && last >= lastPlain)
        )
          break;
        cursor = again.next;
      }
      const stillOld = plain.filter((o) => {
        const now = fresh.get(o.key);
        return (
          now !== undefined &&
          now.lastModifiedMs < before &&
          now.lastModifiedMs === o.lastModifiedMs &&
          now.etag === o.etag
        );
      });
      if (stillOld.length > 0)
        await adapter.deleteObjects(stillOld.map((o) => o.key));
    }
    // After the plain ones, so they do not wait for an unrelated guarded delete.
    if (guarded.length > 0) await adapter.deleteObjectsIfMatch(guarded);
    if (page.next === undefined) return;
    after = page.next;
  }
}
