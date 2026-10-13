/** What one delete removed from a collection; restoring it brings back all of it. */
export interface TrashItem {
  id: string;
  name: string | null;
  recordType?: string | null;
  isFolder: boolean;
  mimeType?: string | null;
  sizeInBytes?: number | null;
  /** The folder it was in; null at the collection's top level. */
  parentId?: string | null;
  parentName?: string | null;
  /** That folder is in the trash too, so it has to be restored first. */
  parentInTrash: boolean;
  /** Records this delete removed, all of which a restore brings back. */
  itemCount: number;
  /** Items selected in the delete: more than one for a multi-select. */
  rootCount?: number;
  /** Names of some of the other items selected with this one. */
  otherRootNames?: string[];
  deletedAtTimestamp: number;
  deletedBy?: { name?: string | null; email?: string | null } | null;
  /** From when the scheduled cleanup may remove it for good; null when unknown. */
  removableAfterTimestamp?: number | null;
}

export interface TrashListResponse {
  success: boolean;
  items: TrashItem[];
  pagination: { page: number; limit: number; totalCount: number; totalPages: number };
  retention?: { minAgeMs: number } | null;
}

/** The answer of a single restore (POST /record/:id/restore). */
export interface RestoreResponse {
  success: boolean;
  message?: string;
  batchId?: string | null;
  restoredRecords?: Array<{ recordId: string; name?: string | null; renamedFrom?: string | null }>;
  reindexPending?: boolean;
  reindexPendingReason?: string;
  renamePendingRecordIds?: string[];
  renamePendingReason?: string;
}

/** One id's outcome in a bulk restore (POST /records/restore). */
export interface RestoreResultEntry extends RestoreResponse {
  recordId: string;
  code?: number;
  reason?: string;
}

export interface BulkRestoreResponse {
  success: boolean;
  restoredCount: number;
  failedCount: number;
  results: RestoreResultEntry[];
}
