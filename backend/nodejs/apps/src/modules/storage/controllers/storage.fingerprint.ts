/**
 * The verified document read (`GET /internal/:id?verify=1`): one pass `row -> stat -> re-read`, an adopt of a finished direct upload
 * followed by one whole second pass, and the response a reader may trust. The order is the proof: the row is read before the object
 * is stat-ed and the write state is read again after, so a write that lands in between either shows in the counters or leaves a
 * token (a presigned PUT, which no database event sees, changes the ETag instead). See the STOR-FP plan, D4 and D5.
 */
import type { DocumentRepository } from '../../../libs/repositories/document';
import { ServerClockUnavailable } from '../../../libs/repositories/document/types';
import { ServiceUnavailableError } from '../../../libs/errors/http.errors';
import { StorageUrlParseError } from '../../../libs/errors/storage.errors';
import type { Document } from '../types/storage.service.types';
import {
  FINGERPRINT_VERSION,
  fingerprintEpoch,
  fingerprintValue,
  isSettled,
  type ObjectStat,
} from '../utils/storage-fingerprint';

export interface StorageFingerprint {
  version: number;
  value: string;
  settled: boolean;
  verified: boolean;
  object: ObjectStat | null;
  objectMissing: boolean;
  objectUnverifiable: boolean;
  reason?: string;
  trusted: boolean;
}

export interface FingerprintLogger {
  error(message: string, meta?: Record<string, unknown>): void;
  warn(message: string, meta?: Record<string, unknown>): void;
  info?(message: string, meta?: Record<string, unknown>): void;
}

export interface FingerprintDeps {
  repo: DocumentRepository;
  /** The object's size and ETag from the store (see `StorageServiceInterface.statObject`). */
  stat: (document: Document) => Promise<ObjectStat | null>;
  logger: FingerprintLogger;
  /** Test seam: awaited at the named points of a pass (gates in the interleaving tests). */
  point?: (
    name: 'R:afterRow' | 'R:afterStat' | 'R:afterReread',
  ) => Promise<void>;
}

interface Pass {
  row: Document;
  writeSeq: number;
  writers: { t: string; until: number }[];
  serverNow: number;
  stat: ObjectStat | null;
  unverifiable: boolean;
  again: { writeSeq: number; writers: { t: string; until: number }[] };
}

const isArtifactSet = (row: Document): boolean => Boolean(row.artifactSet);

async function onePass(
  deps: FingerprintDeps,
  orgId: string,
  id: string,
): Promise<Pass | null> {
  const first = await deps.repo.getWithFingerprintState(orgId, id);
  if (!first) return null;
  await deps.point?.('R:afterRow');
  let stat: ObjectStat | null = null;
  let unverifiable = false;
  if (!isArtifactSet(first.row)) {
    try {
      stat = await deps.stat(first.row);
    } catch (error) {
      if (error instanceof StorageUrlParseError) {
        unverifiable = true;
      } else {
        deps.logger.error('Could not read the stored object of a document', {
          documentId: id,
          error: error instanceof Error ? error.message : String(error),
        });
        throw new ServiceUnavailableError(
          'Could not read the stored object; try again',
        );
      }
    }
  }
  await deps.point?.('R:afterStat');
  const again = await deps.repo.peekWriteState(orgId, id);
  await deps.point?.('R:afterReread');
  if (!again) return null;
  return {
    row: first.row,
    writeSeq: first.writeSeq,
    writers: first.writers,
    serverNow: first.serverNow,
    stat,
    unverifiable,
    again,
  };
}

function build(p: Pass, verified: boolean): StorageFingerprint {
  const row = p.row;
  const stat = verified ? p.stat : null;
  const settledNow =
    isSettled(p.writers, p.serverNow) &&
    isSettled(p.again.writers, p.serverNow);
  const stable = p.writeSeq === p.again.writeSeq && settledNow;
  const awaiting = row.awaitingDirectUpload === true;
  const artifactSet = isArtifactSet(row);
  const trusted =
    verified &&
    stat !== null &&
    !p.unverifiable &&
    !awaiting &&
    !row.isDeleted &&
    !row.retiredAt &&
    !artifactSet &&
    stable;
  return {
    version: FINGERPRINT_VERSION,
    value: fingerprintValue({
      epoch: fingerprintEpoch(),
      writeSeq: p.writeSeq,
      mutationCount: row.mutationCount ?? 0,
      versionCount: row.versionHistory?.length ?? 0,
      size: stat ? stat.size : row.sizeInBytes,
      etag: stat?.etag,
    }),
    settled: settledNow,
    verified,
    object: stat,
    objectMissing:
      verified &&
      p.stat === null &&
      !p.unverifiable &&
      !artifactSet &&
      !awaiting,
    objectUnverifiable: verified && p.unverifiable,
    ...(verified && p.unverifiable
      ? { reason: 'unparseable_storage_url' }
      : {}),
    trusted,
  };
}

/** A fingerprint that names no counter state: nothing can match it, and it is never trusted. */
function withoutState(
  row: Document,
  verified: boolean,
  reason: string,
): StorageFingerprint {
  return {
    version: FINGERPRINT_VERSION,
    value: fingerprintValue({
      epoch: fingerprintEpoch(),
      writeSeq: -1,
      mutationCount: row.mutationCount ?? 0,
      versionCount: row.versionHistory?.length ?? 0,
      size: row.sizeInBytes,
      etag: undefined,
    }),
    settled: false,
    verified,
    object: null,
    objectMissing: false,
    objectUnverifiable: false,
    reason,
    trusted: false,
  };
}

export interface FingerprintRead {
  document: Document;
  storageFingerprint: StorageFingerprint;
}

/**
 * The document row with its `storageFingerprint`. Without `verify` nothing is asked of the object store: the value follows the
 * counters only and is never trusted. With `verify` the pass above runs. Null when the row is not there (404).
 */
export async function readDocumentWithFingerprint(
  deps: FingerprintDeps,
  orgId: string,
  id: string,
  verify: boolean,
): Promise<FingerprintRead | null> {
  if (!verify) {
    try {
      const first = await deps.repo.getWithFingerprintState(orgId, id);
      if (!first) return null;
      const settled = isSettled(first.writers, first.serverNow);
      const fp: StorageFingerprint = {
        version: FINGERPRINT_VERSION,
        value: fingerprintValue({
          epoch: fingerprintEpoch(),
          writeSeq: first.writeSeq,
          mutationCount: first.row.mutationCount ?? 0,
          versionCount: first.row.versionHistory?.length ?? 0,
          size: first.row.sizeInBytes,
          etag: undefined,
        }),
        settled,
        verified: false,
        object: null,
        objectMissing: false,
        objectUnverifiable: false,
        trusted: false,
      };
      return { document: first.row, storageFingerprint: fp };
    } catch (error) {
      if (!(error instanceof ServerClockUnavailable)) throw error;
      const row = await deps.repo.get(orgId, id);
      return row
        ? {
            document: row,
            storageFingerprint: withoutState(
              row,
              false,
              'server_clock_unavailable',
            ),
          }
        : null;
    }
  }

  try {
    let p = await onePass(deps, orgId, id);
    if (!p) return null;
    if (p.row.awaitingDirectUpload === true && p.stat) {
      // The object arrived: record it (once, whoever gets there first), then run the whole pass again. The answer is built from
      // that second pass only, never from a mix.
      await deps.repo.adoptDirectUpload(orgId, id, p.stat.size);
      p = await onePass(deps, orgId, id);
      if (!p) return null;
    }
    const fp = build(p, true);
    let document = p.row;
    if (p.stat) {
      if (fp.trusted && p.row.sizeInBytes !== p.stat.size) {
        // The stored size follows the object (best effort, compare-and-set, no sequence change).
        try {
          await deps.repo.correctSize(
            orgId,
            id,
            p.row.sizeInBytes,
            p.stat.size,
          );
        } catch (error) {
          deps.logger.warn('Could not correct the stored size of a document', {
            documentId: id,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
      document = { ...p.row, sizeInBytes: p.stat.size };
    }
    return { document, storageFingerprint: fp };
  } catch (error) {
    if (!(error instanceof ServerClockUnavailable)) throw error;
    const row = await deps.repo.get(orgId, id);
    if (!row) return null;
    return {
      document: row,
      storageFingerprint: withoutState(row, true, 'server_clock_unavailable'),
    };
  }
}
