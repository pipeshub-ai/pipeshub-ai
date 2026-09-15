import { createHash } from 'crypto';
import { IncomingHttpHeaders } from 'http';
import {
  BadRequestError,
  UnprocessableEntityError,
} from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { DocumentModel } from '../schema/document.schema';
import { Document, StorageVendor } from '../types/storage.service.types';

export const IDEMPOTENCY_KEY_HEADER = 'idempotency-key';

// Room for a UUID or a composite key, bounded so it cannot bloat the index.
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;

const MONGO_DUPLICATE_KEY = 11000;

// How long a keyed upload attempt owns a document it has not finished storing.
// Longer than any attempt runs; once it lapses, a retry may take over from an
// attempt that died without releasing it.
export const UPLOAD_LEASE_MS = 10 * 60 * 1000;

export interface IdempotentCreate {
  key: string;
  // Identifies the request the key was first used for; see requestFingerprint.
  fingerprint: string;
}

export function getIdempotencyKey(
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
): string | undefined {
  // Tolerates a request without headers: unit tests build bare ones.
  const { headers } = req as { headers?: IncomingHttpHeaders };
  const key = headers?.[IDEMPOTENCY_KEY_HEADER];
  if (key === undefined) {
    return undefined;
  }
  if (typeof key !== 'string' || !IDEMPOTENCY_KEY_PATTERN.test(key)) {
    throw new BadRequestError(
      'Idempotency-Key must be 1-128 letters, digits, ".", "_", ":" or "-"',
    );
  }
  return key;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(',')}]`;
  }
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`);
    return `{${entries.join(',')}}`;
  }
  // JSON.stringify returns undefined (not "null") for undefined, e.g. in an array.
  if (value === undefined) {
    return 'null';
  }
  return JSON.stringify(value);
}

/**
 * A canonical digest of a create request, stored with its key so the key
 * cannot be replayed for a different request.
 */
export function requestFingerprint(
  fields: Record<string, unknown>,
  content?: Buffer,
): string {
  const hash = createHash('sha256').update(canonicalJson(fields));
  if (content !== undefined) {
    hash.update(createHash('sha256').update(content).digest());
  }
  return hash.digest('hex');
}

function replay(
  document: DocumentModel,
  idempotency: IdempotentCreate,
): { document: DocumentModel; replayed: boolean } {
  if (document.idempotencyFingerprint !== idempotency.fingerprint) {
    throw new UnprocessableEntityError(
      'Idempotency-Key was already used for a different request',
    );
  }
  return { document, replayed: true };
}

/**
 * Create a document at most once per (orgId, principal, Idempotency-Key).
 *
 * A client that retries a create after an ambiguous failure (the connection
 * dropped after the server may already have acted) sends the same key and
 * gets back the document its first attempt made instead of a duplicate. The
 * unique index settles two attempts racing each other. Keys are scoped to the
 * principal (initiatorUserId; null for service tokens), so a replay never
 * returns another principal's document. Without a key this is a plain create.
 */
export async function createDocumentOnce(
  documentInfo: Partial<Document>,
  idempotency: IdempotentCreate | undefined,
  // Written by the first attempt only, e.g. an upload's lease.
  firstAttemptFields: Partial<Document> = {},
): Promise<{ document: DocumentModel; replayed: boolean }> {
  if (idempotency === undefined) {
    return {
      document: await DocumentModel.create(documentInfo),
      replayed: false,
    };
  }
  // Indexes build in the background after connect, and the replay below is
  // only sound once the unique index exists. Mongoose caches this promise.
  await DocumentModel.init();
  const scope = {
    orgId: documentInfo.orgId,
    initiatorUserId: documentInfo.initiatorUserId ?? null,
    // $type repeats the index's partial filter: without it the planner cannot
    // tell the query stays inside that index, and scans the org instead.
    idempotencyKey: { $eq: idempotency.key, $type: 'string' },
  };
  const existing = await DocumentModel.findOne(scope);
  if (existing) {
    return replay(existing, idempotency);
  }
  try {
    const document = await DocumentModel.create({
      ...documentInfo,
      ...firstAttemptFields,
      idempotencyKey: idempotency.key,
      idempotencyFingerprint: idempotency.fingerprint,
    });
    return { document, replayed: false };
  } catch (error) {
    if ((error as { code?: number }).code !== MONGO_DUPLICATE_KEY) {
      throw error;
    }
    const winner = await DocumentModel.findOne(scope);
    if (!winner) {
      throw error;
    }
    return replay(winner, idempotency);
  }
}

/**
 * Take over a keyed upload that no attempt is finishing: the last one failed
 * and released it, or died and let its lease run out. Atomic, so of two
 * retries arriving together only one gets it. Returns the claimed document,
 * or null while another attempt still holds it.
 */
export async function claimUpload(
  document: DocumentModel,
  storageVendor: StorageVendor,
  leaseToken: string,
): Promise<DocumentModel | null> {
  const now = Date.now();
  return DocumentModel.findOneAndUpdate(
    {
      _id: document._id,
      [storageVendor]: { $exists: false },
      $or: [
        { uploadLeaseExpiresAt: { $exists: false } },
        { uploadLeaseExpiresAt: { $lte: now } },
      ],
    },
    {
      $set: {
        uploadLeaseToken: leaseToken,
        uploadLeaseExpiresAt: now + UPLOAD_LEASE_MS,
      },
    },
    { new: true },
  );
}

/** A failed attempt: let the next retry take over now, not when the lease lapses. */
export async function releaseUpload(
  documentId: unknown,
  leaseToken: string,
): Promise<void> {
  await DocumentModel.updateOne(
    { _id: documentId, uploadLeaseToken: leaseToken },
    { $unset: { uploadLeaseToken: '', uploadLeaseExpiresAt: '' } },
  );
}

/**
 * Make the next save of an upload's document apply only while this attempt
 * still holds its lease, and clear the lease in that same write. A save that
 * lost the lease to a later attempt fails with DocumentNotFoundError.
 */
export function holdLeaseOnSave(
  document: DocumentModel,
  leaseToken: string,
): void {
  document.$where = { uploadLeaseToken: leaseToken };
  document.uploadLeaseToken = undefined;
  document.uploadLeaseExpiresAt = undefined;
}
