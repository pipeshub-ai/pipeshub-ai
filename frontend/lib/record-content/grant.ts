/**
 * Single-use permission to reuse a connector record's retained copy without a stream request.
 *
 * A grant is minted by `KnowledgeBaseApi.getRecordDetailsForOpen` and nowhere else: from the
 * response of a details call that carried the content-open header, when that response arrived.
 * Callers cannot build one from a details object or a label string. The cache accepts a grant
 * only by identity (the module-private set below), for the record, user and org it was minted
 * for, once, and for a short time.
 */

export const RECORD_CONTENT_GRANT_TTL_MS = 30_000;

export interface Grant {
  readonly recordId: string;
  /** Label of the connector copy the details answer describes; null when the record is not label-retainable. */
  readonly label: string | null;
  readonly owner: string;
  readonly mintedAt: number;
}

const minted = new WeakSet<object>();
const spent = new WeakSet<object>();

/** Only `getRecordDetailsForOpen` may call this. A source scan in the tests pins that. */
export function mintGrant(recordId: string, label: string | null, owner: string): Grant {
  const grant: Grant = Object.freeze({ recordId, label, owner, mintedAt: Date.now() });
  minted.add(grant);
  return grant;
}

/** True when the grant is genuine, unspent, fresh and for this record and owner; spends it. */
export function consumeGrant(
  grant: unknown,
  recordId: string,
  owner: string,
  ttlMs: number = RECORD_CONTENT_GRANT_TTL_MS,
): Grant | null {
  if (typeof grant !== 'object' || grant === null) return null;
  if (!minted.has(grant) || spent.has(grant)) return null;
  const g = grant as Grant;
  // A grant is used once whether or not it matches: a mismatched try must not stay usable.
  spent.add(grant);
  if (g.recordId !== recordId || g.owner !== owner) return null;
  if (Date.now() - g.mintedAt > ttlMs) return null;
  return g;
}

const STORAGE_SERVED_CONNECTORS: ReadonlySet<string> = new Set(['ATTACHMENTS', 'CODING_SANDBOX']);

/** Label of a connector copy: its revision id and content revision; null when it has neither. */
export function connectorLabel(record: {
  origin?: string;
  recordType?: string | null;
  connectorName?: string | null;
  externalRevisionId?: string | null;
  contentRev?: string | null;
}): string | null {
  if (record.origin !== 'CONNECTOR') return null;
  // Records the stream route serves from artifact storage run refusals the details route does not
  // (`_resolve_record_content_response`), so they are never reused without a stream request.
  if (record.recordType === 'ARTIFACT' || (record.connectorName && STORAGE_SERVED_CONNECTORS.has(record.connectorName))) {
    return null;
  }
  const rev = record.externalRevisionId ?? '';
  const content = record.contentRev ?? '';
  if (!rev && !content) return null;
  return `${rev}|${content}`;
}
