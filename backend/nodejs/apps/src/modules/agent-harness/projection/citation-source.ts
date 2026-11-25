/**
 * Where a citation's content came from (P1b C-18): the one classification
 * the shared stream and the conversation GET both use, producing the typed
 * sources the query service decides (`PipeshubCitationSource`).
 *
 * Python stamps `sourceKind` only where the metadata cannot tell: a file a
 * tool read straight from a connector is `live`, with the connector kind and
 * the item's external id. The other kinds follow from metadata every
 * citation, stored or new, already carries; so do legacy citations saved
 * before C-18. A citation naming nothing that can be checked is
 * `unclassified`, and hidden from a shared viewer.
 */
import type { PipeshubCitationSource } from '../contracts/generated/ext/pipeshub';

type Json = Record<string, unknown>;

/** A source the query service decides: a record or a live item. */
export type CheckedSource = Exclude<PipeshubCitationSource, { kind: 'web' }>;

export type CitationSource = PipeshubCitationSource | { kind: 'unclassified' };

/** The id prefix of the temporary record a live tool builds; never a record key. */
export const LIVE_RECORD_ID_PREFIX = 'live:';

const EXPLICIT_RECORD_KINDS = new Set(['indexed', 'attachment', 'generated']);
const ATTACHMENT_CONNECTOR = 'ATTACHMENTS';
const GENERATING_CONNECTORS = new Set([
  'CODING_SANDBOX',
  'DATABASE_SANDBOX',
  'IMAGE_GENERATION',
]);

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/** A citation in the finalizer's shape (`{content, chunkIndex, metadata, citationType}`). */
export function citationSource(citation: unknown): CitationSource {
  if (!isObject(citation)) return { kind: 'unclassified' };
  const metadata = isObject(citation['metadata']) ? citation['metadata'] : {};
  const explicit = metadata['sourceKind'];
  const type = citation['citationType'];
  if (
    explicit === 'web' ||
    (typeof type === 'string' && type.startsWith('web')) ||
    metadata['origin'] === 'WEB_SEARCH'
  ) {
    return { kind: 'web' };
  }
  const recordId = text(metadata['recordId']);
  if (explicit === 'live' || recordId?.startsWith(LIVE_RECORD_ID_PREFIX)) {
    return liveSource(metadata);
  }
  if (recordId === undefined) return { kind: 'unclassified' };
  if (typeof explicit === 'string' && EXPLICIT_RECORD_KINDS.has(explicit)) {
    return {
      kind: explicit as 'indexed' | 'attachment' | 'generated',
      recordId,
    };
  }
  const connector = text(metadata['connector'])?.toUpperCase() ?? '';
  if (connector === ATTACHMENT_CONNECTOR)
    return { kind: 'attachment', recordId };
  if (
    metadata['recordType'] === 'ARTIFACT' ||
    GENERATING_CONNECTORS.has(connector)
  ) {
    return { kind: 'generated', recordId };
  }
  return { kind: 'indexed', recordId };
}

/** A live citation without the item it read cannot be verified: unclassified. */
function liveSource(metadata: Json): CitationSource {
  const connector = text(metadata['connector']);
  const externalRecordId = text(metadata['externalRecordId']);
  if (connector === undefined || externalRecordId === undefined) {
    return { kind: 'unclassified' };
  }
  const connectorId = text(metadata['connectorId']);
  return {
    kind: 'live',
    connector,
    ...(connectorId !== undefined ? { connectorId } : {}),
    externalRecordId,
  };
}

export const isChecked = (source: CitationSource): source is CheckedSource =>
  source.kind !== 'web' && source.kind !== 'unclassified';

/**
 * One key per thing the viewer's access is decided on: a record whatever
 * its kind (one ACL), or a live item by connector kind and external id.
 */
export function sourceKey(source: CheckedSource): string {
  if (source.kind === 'live') {
    return `live:${source.connector}\u0000${source.externalRecordId}`;
  }
  const record =
    source.virtualRecordId === undefined
      ? `record:${source.recordId}`
      : `record:${source.recordId}\u0000${source.virtualRecordId}`;
  // A record that reached a turn through a publication (P2.13) is decided by
  // that publication's audience, not only the record's ACL: its own key.
  return source.kind === 'indexed' && source.publicationId !== undefined
    ? `${record}\u0001${source.publicationId}`
    : record;
}

/** The distinct checked sources of finalizer-shaped citations, first seen first. */
export function checkedSources(citations: unknown): CheckedSource[] {
  if (!Array.isArray(citations)) return [];
  const sources = new Map<string, CheckedSource>();
  for (const citation of citations) {
    const source = citationSource(citation);
    if (isChecked(source) && !sources.has(sourceKey(source))) {
      sources.set(sourceKey(source), source);
    }
  }
  return [...sources.values()];
}

/** The conversation shape's citation data (`citations[].citationData`). */
export function citationDataOf(message: unknown): unknown[] {
  if (!isObject(message) || !Array.isArray(message['citations'])) return [];
  return (message['citations'] as unknown[]).map((entry) =>
    isObject(entry) ? entry['citationData'] : undefined,
  );
}

/** The distinct checked sources cited by conversation messages. */
export function messageSources(messages: unknown): CheckedSource[] {
  return Array.isArray(messages)
    ? checkedSources(messages.flatMap(citationDataOf))
    : [];
}
