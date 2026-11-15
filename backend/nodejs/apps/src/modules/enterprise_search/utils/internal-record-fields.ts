/**
 * Fields a stored record carries for the services' own use. The query service
 * leaves them out of the records it returns, and so does this API: a saved
 * search keeps copies of records, and the history routes return them.
 *
 * The names mirror `INTERNAL_RECORD_FIELDS` in
 * backend/python/app/services/graph_db/common/utils.py; a Python test compares
 * the two lists.
 */
export const INTERNAL_RECORD_FIELDS: ReadonlySet<string> = new Set([
  'deletedAtTimestamp',
  'deleteSource',
  'deleteBatchId',
  'purgeAttempts',
  'purgeLastError',
  'trashedExternalRecordId',
  'rateLimitedSince',
  'lastRepublishedAt',
  'republishCount',
  'firstQueuedAt',
  'failedAt',
  'indexingAttempts',
  'pageCount',
  'duplicateReconcilePending',
  'duplicateReconcileAttempts',
  'extractionDeferred',
  'duplicateReconcileDueAt',
  'pushOrigin',
  'recordProps',
  'propsSet',
  'propsGen',
  'propsSrc',
  'propsVer',
  'embedVrid',
  'embedRev',
  'embedKey',
  'embedDigest',
  'embedTotal',
  'embedDeliveryId',
  'embedStartedMs',
  'embedSeconds',
  'embedUncounted',
  'embedDone',
]);

/** A projected property value on a Neo4j record node (`p_<name>`). */
const PROJECTED_PROPERTY_KEY = /^p_[a-zA-Z0-9_]{1,64}$/;

/** The record as the record endpoint returns it: without the internal fields. */
export function apiRecord<T>(record: T): T {
  if (record === null || typeof record !== 'object' || Array.isArray(record)) {
    return record;
  }
  return Object.fromEntries(
    Object.entries(record as Record<string, unknown>).filter(
      ([key]) =>
        !INTERNAL_RECORD_FIELDS.has(key) && !PROJECTED_PROPERTY_KEY.test(key),
    ),
  ) as T;
}

/**
 * A saved search's `records` (a map or object of record id to the record as a
 * JSON string, or to the record itself) without the internal fields.
 */
export function apiStoredRecords(records: unknown): unknown {
  if (records === null || typeof records !== 'object') return records;
  const entries: Array<[string, unknown]> =
    records instanceof Map
      ? Array.from(records as Map<string, unknown>)
      : Object.entries(records as Record<string, unknown>);
  return Object.fromEntries(
    entries.map(([key, value]) => {
      if (typeof value !== 'string') return [key, apiRecord(value)];
      try {
        return [key, JSON.stringify(apiRecord(JSON.parse(value)))];
      } catch {
        return [key, value];
      }
    }),
  );
}
