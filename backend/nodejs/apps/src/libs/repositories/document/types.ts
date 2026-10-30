/**
 * Document repository: the metadata row behind every stored file (KB uploads,
 * connector envelopes, assistant artifacts).
 *
 * Rows leave as plain objects with every id as a string. No driver type appears
 * here. Methods are use cases: the path-prefix, connector-scope and virtual
 * record lookups carry the knowledge of how a row is found, so a caller never
 * builds a filter.
 */
import type {
  Document,
  DocumentVersion,
  FingerprintState,
  StorageVendor,
} from '../../../modules/storage/types/storage.service.types';
import { ServiceUnavailableError } from '../../errors/http.errors';

export type DocumentRow = Document;

/** Name prefixes of the two envelope documents a virtual record owns. */
export const ENVELOPE_PREFIXES = ['record_', 'metadata_'];

/** A row to create; ids are strings. */
export type NewDocument = Partial<Omit<Document, '_id'>>;

/**
 * Fields a caller may change on a stored row. A key present with `undefined`
 * clears the field, as assigning `undefined` on a loaded document did.
 */
export type DocumentPatch = Partial<
  Pick<
    Document,
    | 'documentPath'
    | 'mutationCount'
    | 'sizeInBytes'
    | 's3'
    | 'azureBlob'
    | 'local'
    | 'isDeleted'
    | 'deletedByUserId'
    | 'awaitingDirectUpload'
  >
>;

export interface UpdateOptions {
  /** Versions appended to `versionHistory` in the same write. */
  pushVersions?: DocumentVersion[];
  /**
   * The write applies only while this upload attempt still holds its lease,
   * and clears the lease in the same write.
   */
  holdLease?: string;
}

/** The columns the tree and relocate operations read. */
export interface DocumentTreeRow {
  _id: string;
  documentPath: string;
  documentName: string;
  extension?: string;
  isVersionedFile?: boolean;
  versionHistory?: DocumentVersion[];
  isDeleted?: boolean;
  customMetadata?: { key: string; value?: unknown }[];
}

/** The documents a connector owns: under its path prefixes, or tagged with its id. */
export interface ConnectorScope {
  connectorId: string;
  prefixes: string[];
}

/** The clock and the staleness bound of a delete claim, passed in so both stores judge it the same way. */
export interface ArtifactFence {
  now: number;
  staleMs: number;
}

export interface IdempotencyClaim {
  key: string;
  fingerprint: string;
}

/** The store's own clock could not be read: a write is refused before any byte, a read answers "not trusted". */
export class ServerClockUnavailable extends ServiceUnavailableError {
  constructor() {
    super('The storage clock is unavailable; try again');
  }
}

/** Another writer holds the last free slot of a document: nothing was written. */
export class DocumentBusy extends ServiceUnavailableError {
  constructor() {
    super('The document is busy; try again');
  }
}

export interface DocumentRepository {
  /** By id within the org, deleted or not. */
  get(orgId: string, id: string): Promise<DocumentRow | null>;
  /**
   * By id within the org, not deleted. `lean` skips building a full model
   * (schema defaults are then absent): for readers that only read a few fields.
   */
  getLive(
    orgId: string,
    id: string,
    lean?: boolean,
  ): Promise<DocumentRow | null>;
  create(row: NewDocument): Promise<DocumentRow>;
  /**
   * Creates the row at most once per (org, principal, key). A second attempt,
   * including one racing the first, gets the first attempt's row with
   * `replayed: true`; the caller decides whether it is the same request.
   */
  createIdempotent(
    row: NewDocument,
    idempotency: IdempotencyClaim,
    firstAttemptFields: NewDocument,
  ): Promise<{ document: DocumentRow; replayed: boolean }>;
  /** Null when the row is gone, or (with `holdLease`) the lease was lost. */
  update(
    orgId: string,
    id: string,
    patch: DocumentPatch,
    opts?: UpdateOptions,
  ): Promise<DocumentRow | null>;
  /** Hard delete. Without `orgId` the row is matched by id alone. */
  remove(id: string, orgId?: string): Promise<void>;
  /** Removes a row whose file never reached storage (vendor field absent). */
  removeUnstored(id: string, vendor: StorageVendor): Promise<void>;
  /** Removes a row only while it is still an unfinished direct upload. */
  removeAwaitingDirectUpload(orgId: string, id: string): Promise<boolean>;
  clearAwaitingDirectUpload(orgId: string, id: string): Promise<void>;
  /** Takes over an unfinished keyed upload; null while another attempt holds it. */
  claimUpload(
    id: string,
    vendor: StorageVendor,
    leaseToken: string,
    leaseMs: number,
  ): Promise<DocumentRow | null>;
  releaseUpload(id: string, leaseToken: string): Promise<void>;
  /** Waits until the unique indexes the idempotent create relies on exist. */
  ensureIndexes(): Promise<void>;

  /**
   * A virtual record's `record_` and `metadata_` rows, wherever filed, plus its flat-path rows
   * and every content revision (artifact set) stored for it.
   */
  listByVirtualRecord(
    orgId: string,
    virtualRecordId: string,
    flatPath: string,
  ): Promise<DocumentRow[]>;
  /**
   * Same rows, only the columns a relocation needs. Content revisions are left out: their
   * files are keyed by virtual record, not by connector.
   */
  listForRelocate(
    orgId: string,
    virtualRecordId: string,
    flatPath: string,
  ): Promise<DocumentTreeRow[]>;
  /**
   * Live `record_`/`metadata_` rows of a virtual record, newest first, with the columns a writer
   * needs to reuse them. With `olderThanSeconds` (finite, >= 0), only rows created that long ago
   * by the database's own clock; rows without a numeric `createdAt` are then left out.
   */
  listEnvelopes(
    orgId: string,
    virtualRecordId: string,
    opts?: { olderThanSeconds?: number },
  ): Promise<
    {
      _id: string;
      documentName?: string;
      documentPath?: string;
      createdAt?: number;
      retiredAt?: number | null;
    }[]
  >;
  /**
   * Marks a live row retired (later in-place buffer writes are refused; reads are unaffected).
   * Idempotent: the first mark's time is kept. Null when there is no such live row.
   */
  retire(
    orgId: string,
    id: string,
    now: number,
  ): Promise<{ retiredAt: number | null } | null>;
  /**
   * One page (`_id` ascending, after `after`) of live envelope rows still filed at the flat
   * `<flatPrefix><virtualRecordId>` path older records used.
   */
  pageFlatEnvelopes(
    orgId: string,
    flatPrefix: string,
    after: string | undefined,
    limit: number,
  ): Promise<{ _id: string; documentName?: string; documentPath?: string }[]>;
  /** Rows filed exactly at one of these folder paths. */
  listByFolders(orgId: string, folderPaths: string[]): Promise<DocumentRow[]>;
  /** Of these ids, the ones that exist, in the org, not deleted. */
  findExistingIds(orgId: string, ids: string[]): Promise<string[]>;

  /** Rows at `path` or anywhere beneath it (whole segments), deleted ones included. */
  subtree(orgId: string, path: string): Promise<DocumentTreeRow[]>;
  /**
   * One write that swaps the `oldPath` prefix for `newPath` on every listed row
   * and applies each row's own field changes.
   */
  rewriteTree(
    oldPath: string,
    newPath: string,
    rows: { id: string; set: Record<string, unknown> }[],
  ): Promise<void>;
  /** `$set`-style change of named (dotted) fields on one row. */
  setFields(id: string, set: Record<string, unknown>): Promise<void>;
  /** `setFields` for many rows in one round trip; each row names its own changes. */
  setFieldsMany(
    rows: { id: string; set: Record<string, unknown> }[],
  ): Promise<void>;

  /**
   * The row standing for one content revision of a virtual record, created on first use.
   * Racing callers settle on one row (unique per org, virtual record and revision).
   *
   * Every call moves the row's write stamp (`updatedAt`) to `now`, so a delete that listed the
   * revision earlier sees this write. A revision a delete claimed (`artifactSet.retiringAt`) less
   * than `staleMs` ago is refused with a `ConflictError`: its files may already be going. An older
   * claim is from a delete that died and is cleared by this write.
   */
  createArtifactSetOnce(
    row: NewDocument,
    fence: ArtifactFence,
  ): Promise<DocumentRow>;
  /**
   * Claims a revision for deletion: sets `artifactSet.retiringAt = now` without moving the write
   * stamp, and returns the row. With `ifUnchangedSince` the claim also requires the write stamp to
   * be no newer than it. Refused while another delete holds a claim younger than `staleMs`.
   * `null` when there is no such revision; `'changed'` when it exists but the stamp or another
   * delete's claim stands in the way (nothing was claimed).
   */
  claimArtifactSetForDelete(
    orgId: string,
    id: string,
    claim: { now: number; staleMs: number; ifUnchangedSince?: number },
  ): Promise<DocumentRow | 'changed' | null>;
  /**
   * Moves a delete's claim to `next`, only while the claim is still one of `known` (values this
   * delete wrote). A writer that took the claim back is not overridden.
   */
  renewArtifactSetClaim(
    orgId: string,
    id: string,
    known: number[],
    next: number,
  ): Promise<void>;
  /** Hard delete of a claimed revision, only while its claim is one of `claims` (this delete's own values). */
  removeClaimedArtifactSet(
    id: string,
    orgId: string,
    claims: number[],
  ): Promise<void>;
  /** Live revisions of a virtual record, oldest first. */
  listArtifactSets(
    orgId: string,
    virtualRecordId: string,
  ): Promise<DocumentRow[]>;

  /** Keyset page (`_id` ascending) of a connector's rows. */
  pageByConnector(
    orgId: string,
    scope: ConnectorScope,
    after: string | undefined,
    limit: number,
  ): Promise<{ _id: string; documentName?: string; documentPath?: string }[]>;
  /** Rows tagged with the connector but filed outside its prefixes, streamed. */
  streamTaggedOutsidePrefixes(
    orgId: string,
    scope: ConnectorScope,
  ): AsyncIterable<{ _id: string; documentPath?: string }>;
  deleteByConnector(orgId: string, scope: ConnectorScope): Promise<number>;

  /**
   * Write state (STOR-FP). `beginWrite`, `renewWrite`, `endWrite` and `bumpSeq` never take part in a caller's unit of work:
   * they commit on their own, so a token is visible to every reader at once and survives a rollback of the caller. Time is
   * the store's clock, never the pod's.
   *
   * Registers a writer (a random `token`, alive for `windowMs`) and bumps the sequence, in one atomic write. Expired tokens of
   * others are pruned; live ones are kept. Null when the row is gone; throws `DocumentBusy` when 64 writers are in flight and
   * `ServerClockUnavailable` when the clock cannot be read.
   */
  beginWrite(
    orgId: string,
    id: string,
    token: string,
    windowMs: number,
  ): Promise<{ writeSeq: number } | null>;
  /** Heartbeat: extends this token (never shortens it, never brings back one that ended). Does not change the sequence. */
  renewWrite(
    orgId: string,
    id: string,
    token: string,
    windowMs: number,
  ): Promise<void>;
  /** Removes this token only and bumps the sequence. Idempotent per token; a missing row is not an error. */
  endWrite(orgId: string, id: string, token: string): Promise<void>;
  /** One atomic increment of the sequence. Null when the row is gone. */
  bumpSeq(orgId: string, id: string): Promise<{ writeSeq: number } | null>;
  /**
   * Completes an unfinished direct upload: clears the flag, stores the size the object really has and bumps the sequence.
   * True for the one caller that did it.
   */
  adoptDirectUpload(orgId: string, id: string, size: number): Promise<boolean>;
  /** Compare-and-set of `sizeInBytes` (no sequence change). */
  correctSize(
    orgId: string,
    id: string,
    from: number | null | undefined,
    to: number,
  ): Promise<void>;
  /** A direct-upload link was issued: path, flag, vendor URL and the sequence move in one write. Null when the row is gone. */
  markAwaitingDirectUpload(
    orgId: string,
    id: string,
    patch: DocumentPatch,
  ): Promise<{ row: DocumentRow; writeSeq: number } | null>;
  /**
   * The row and its write state from one read (the row carries none of the state), with the store's clock taken BEFORE the
   * row was read (`serverNow`, epoch ms): a later clock could only call a live token expired.
   */
  getWithFingerprintState(
    orgId: string,
    id: string,
  ): Promise<
    ({ row: DocumentRow; serverNow: number } & FingerprintState) | null
  >;
  /** Only the write state (the re-read of a verified pass). */
  peekWriteState(orgId: string, id: string): Promise<FingerprintState | null>;
  /** The store's clock in epoch ms. Throws `ServerClockUnavailable`. */
  serverClockMs(): Promise<number>;
}
