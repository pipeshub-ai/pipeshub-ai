/**
 * The writer window of the storage fingerprint: every code path that can change the bytes of a stored document runs inside
 * `withWriteWindow`. A random token is registered (and the sequence bumped) before the first byte; a heartbeat keeps the token
 * alive while the write runs; the token is removed (and the sequence bumped again) when the write is over, whether it worked or
 * not. A reader never trusts a fingerprint while a token is live. See the STOR-FP plan, D2.
 */
import { randomBytes } from 'crypto';
import { getDocumentRepository } from '../../../libs/repositories/document';
import type { DocumentRepository } from '../../../libs/repositories/document';
import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  makeOrgContext,
  runWithOrgContext,
  spawnDetached,
} from '../../../libs/context/orgContext';
import { Logger } from '../../../libs/services/logger.service';

const DEFAULT_WINDOW_MS = 300_000;
/** Total time an `endWrite` may take (two attempts); a successful response never waits longer for it. */
export const END_BUDGET_MS = 2000;

export function writeWindowMs(): number {
  const n = Number(process.env.STORAGE_WRITE_WINDOW_MS);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_WINDOW_MS;
}

/** Test seams: the heartbeat can be driven by hand, and the token is announced. */
export const writeWindowHooks: {
  heartbeat: 'auto' | 'manual';
  repo?: () => DocumentRepository;
  onBegin?: (info: {
    documentId: string;
    orgId: string;
    token: string;
  }) => void;
} = { heartbeat: 'auto' };

const logger = Logger.getInstance({ service: 'StorageWriteWindow' });

async function boundedEnd(
  repo: DocumentRepository,
  orgId: string,
  id: string,
  token: string,
): Promise<void> {
  const attempts = (async () => {
    let last: unknown;
    for (let i = 0; i < 2; i++) {
      try {
        await repo.endWrite(orgId, id, token);
        return;
      } catch (error) {
        last = error;
      }
    }
    throw last;
  })();
  let timer: NodeJS.Timeout | undefined;
  try {
    await Promise.race([
      attempts,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('endWrite did not finish in time'));
        }, END_BUDGET_MS);
        timer.unref();
      }),
    ]);
  } catch (error) {
    // The token stays until it expires: the document is simply untrusted for that long. Never thrown over the write's own outcome.
    logger.error('Could not end a write window', {
      documentId: id,
      error: error instanceof Error ? error.message : String(error),
    });
    attempts.catch(() => undefined);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * Runs `write` (the byte writes and the save of the row) inside a writer window of `document`. Throws NotFoundError before any
 * byte when the row is gone, and the store's own 503 (busy, clock unavailable) before any byte.
 */
export async function withWriteWindow<T>(
  document: { _id: string; orgId: string },
  write: () => Promise<T>,
): Promise<T> {
  const repo = writeWindowHooks.repo?.() ?? getDocumentRepository();
  const orgId = document.orgId;
  const id = document._id;
  const window = writeWindowMs();
  const token = randomBytes(16).toString('hex');
  if (!(await repo.beginWrite(orgId, id, token, window)))
    throw new NotFoundError('Document does not exist');
  writeWindowHooks.onBegin?.({ documentId: id, orgId, token });
  // The heartbeat is long-lived work: it starts detached (no borrowed request context) and binds the document's org itself.
  let stopBeat: (() => void) | undefined;
  if (writeWindowHooks.heartbeat === 'auto') {
    let stopped = false;
    let timer: NodeJS.Timeout | undefined;
    const interval = Math.max(10, Math.floor(window / 3));
    const beat = (): void => {
      if (stopped) return;
      timer = setTimeout(() => {
        const ctx = makeOrgContext({
          orgId,
          userId: null,
          kind: 'service',
          source: 'storage_write_window',
        });
        void runWithOrgContext(ctx, () =>
          repo.renewWrite(orgId, id, token, window),
        )
          .catch((error: unknown) => {
            logger.warn('Could not renew a write window', {
              documentId: id,
              error: error instanceof Error ? error.message : String(error),
            });
          })
          .finally(beat);
      }, interval);
      timer.unref();
    };
    spawnDetached(beat);
    stopBeat = () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }
  try {
    return await write();
  } finally {
    stopBeat?.();
    await boundedEnd(repo, orgId, id, token);
  }
}
