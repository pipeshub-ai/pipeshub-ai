import { getFullDocumentPath } from './utils';

/**
 * The cache kinds (contracts §12.2). A new kind is a contract change, so a
 * path naming anything else never reaches a storage key.
 */
const KINDS = [
  'ocr_page',
  'image_description',
  'table_summary',
  'table_rows',
  'excel_headers',
  'classify',
  'entities',
] as const;

const KIND = `(?:${KINDS.join('|')})`;

/** A cached result: `{kind}/{sha256}.json`. */
const RESULT_PATH = new RegExp(`^${KIND}/[0-9a-f]{64}\\.json$`);

/** A retention manifest: `manifests/{contentRev}/{writer}[.open].json`. */
const MANIFEST_PATH =
  /^manifests\/[A-Za-z0-9_-]{1,64}\/[A-Za-z0-9_-]{1,64}(?:\.open)?\.json$/;

const LIST_PREFIX = new RegExp(
  `^(?:|${KIND}/|manifests/(?:[A-Za-z0-9_-]{1,64}/)?)$`,
);

/**
 * The refresh marker: written by the purge-cutoff route before it takes the cutoff, so its own store timestamp says
 * when the latest refresh of the record began. It sits outside the result and manifest folders, is never listed, and
 * a cutoff purge never removes it. Readable (signed GET) like a result; never written through a signed URL.
 */
export const REFRESH_MARKER_FOLDER = 'refreshed';
export const REFRESH_MARKER_PATH = `${REFRESH_MARKER_FOLDER}/marker.json`;
export const isPaidResultMarkerPath = (p: string): boolean =>
  p === REFRESH_MARKER_PATH;

/** An ETag as the stores report it with the quotes dropped (a hex digest, `digest-N`, or a local synthetic hash). */
export const ETAG_VALUE = /^[A-Za-z0-9._:+=-]{1,128}$/;

export const RECORD_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** A result object: the only thing a `refresh` write may overwrite. */
export const isPaidResultObjectPath = (p: string): boolean =>
  RESULT_PATH.test(p);

/** A manifest: the only thing a `replace` write may overwrite. */
export const isPaidResultManifestPath = (p: string): boolean =>
  MANIFEST_PATH.test(p);

/** Whether a path, relative to a record's folder, is a file the cache may hold. */
export const isPaidResultPath = (p: string): boolean =>
  RESULT_PATH.test(p) || MANIFEST_PATH.test(p);

export const isPaidResultListPrefix = (p: string): boolean =>
  LIST_PREFIX.test(p);

/** Everything the cache holds for one org. Built from the token's org, never from a request body. */
export const paidResultsOrgRoot = (orgId: string): string =>
  getFullDocumentPath(orgId, 'paid-results');

/** Everything the cache holds for one record of an org. */
export const paidResultsRecordRoot = (
  orgId: string,
  recordId: string,
): string => `${paidResultsOrgRoot(orgId)}/${recordId}`;
