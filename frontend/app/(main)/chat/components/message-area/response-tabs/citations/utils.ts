'use client';

import type {
  CitationData,
  CitationMaps,
  StreamingCitationData,
  ConnectorConfig,
  CitationOrigin,
} from './types';
import type {
  CitationApiResponse,
  MessageRedaction,
  RedactedCitationApiResponse,
  RequestAccessInfo,
} from '@/chat/types';

// ---------------------------------------------------------------------------
// Connector configuration (delegates to centralized ConnectorIcon mapping)
// ---------------------------------------------------------------------------

import {
  getConnectorIconConfig,
  resolveConnectorType,
} from '@/app/components/ui/ConnectorIcon';
import { i18n } from '@/lib/i18n';

/**
 * Backend page/paragraph arrays sometimes contain `null` entries (e.g. a
 * chunk that spans an unnumbered page). Drop them — along with any
 * non-numeric junk from malformed responses — so consumers never render a
 * location badge with no number in it, or crash trying to `.filter()` a
 * value that turned out not to be an array.
 */
function filterValidNumbers(values?: unknown): number[] | undefined {
  if (!Array.isArray(values)) return undefined;
  const filtered = values.filter(
    (v): v is number => typeof v === 'number' && !Number.isNaN(v),
  );
  return filtered.length > 0 ? filtered : undefined;
}

/** "SHAREPOINT ONLINE" → "Sharepoint Online" */
function titleCaseConnectorKey(connector: string): string {
  return connector.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

// Names the older, single knowledge-base app carried: not a collection a person named.
const GENERIC_COLLECTION_APP_NAMES = new Set(['knowledge base', 'kb', 'collections']);

function namedSource(instanceName: string | undefined, isCollections: boolean): string | undefined {
  const name = instanceName?.trim();
  if (!name) return undefined;
  if (isCollections && GENERIC_COLLECTION_APP_NAMES.has(name.toLowerCase())) return undefined;
  return name;
}

/**
 * Resolve connector key → display config. `instanceName` is the connector's own
 * name, stamped on citations by the backend; older citations lack it. Each
 * collection is its own app named after the collection, so it is named the same way.
 */
export function getConnectorConfig(connector: string, instanceName?: string): ConnectorConfig {
  const resolved = resolveConnectorType(connector);
  const iconConfig = getConnectorIconConfig(connector);
  const isCollections =
    resolved === 'kb' || resolved === 'knowledge-base';
  const isLocalFs = resolved === 'local-fs' || resolved === 'localfs';
  let label = connector ? titleCaseConnectorKey(connector) : i18n.t('filter.source');
  if (isCollections) label = i18n.t('nav.collections');
  if (isLocalFs) label = i18n.t('connectorTypes.localFs');
  return {
    label,
    sourceLabel: namedSource(instanceName, isCollections) || label,
    icon: iconConfig.svg || '/icons/connectors/GDrive.svg',
  };
}

// ---------------------------------------------------------------------------
// Build citation maps from the *complete* response (CitationApiResponse[])
// ---------------------------------------------------------------------------

export function buildCitationMapsFromApi(
  rawCitations: Array<CitationApiResponse | RedactedCitationApiResponse>,
  redaction?: MessageRedaction,
  detailsHidden?: boolean,
): CitationMaps {
  const citations: Record<string, CitationData> = {};
  const sources: Record<string, string> = {};
  const sourcesOrder: string[] = [];
  const citationsOrder: Record<number, string> = {};
  const redactedSources = new Map<string, RequestAccessInfo>();

  for (const raw of rawCitations) {
    if ('redacted' in raw) {
      redactedSources.set(raw.requestAccess.recordId, raw.requestAccess);
      continue;
    }
    const citationId = raw.citationId;
    const data = raw.citationData;
    if (!data) continue;

    const metadata = data.metadata;
    if (!metadata) continue;

    const normalized: CitationData = {
      citationId,
      content: data.content,
      chunkIndex: data.chunkIndex,
      recordId: metadata.recordId,
      recordName: metadata.recordName || 'Untitled Document',
      connector: metadata.connector || '',
      connectorId: metadata.connectorId || undefined,
      connectorInstanceName: metadata.connectorInstanceName || undefined,
      recordType: metadata.recordType || '',
      webUrl: metadata.webUrl,
      mimeType: metadata.mimeType || '',
      extension: metadata.extension || '',
      pageNum: filterValidNumbers(metadata.pageNum),
      blockNum: filterValidNumbers(metadata.blockNum),
      previewRenderable: metadata.previewRenderable ?? false,
      hideWeburl: (metadata as Record<string, unknown>).hideWeburl as boolean ?? false,
      citationType: data.citationType || '',
      origin: (metadata as Record<string, unknown>).origin as CitationOrigin | undefined,
      boundingBox: (metadata as Record<string, unknown>).bounding_box as Array<{ x: number; y: number }> | undefined,
      // Not data.updatedAt: that is when the answer was saved, not when the record last synced.
      updatedAt: metadata.updatedAt || undefined,
    };

    citations[citationId] = normalized;
    citationsOrder[data.chunkIndex] = citationId;

    if (!sources[metadata.recordId]) {
      sources[metadata.recordId] = citationId;
      sourcesOrder.push(metadata.recordId);
    }
  }

  return {
    citations,
    sources,
    sourcesOrder,
    citationsOrder,
    ...(detailsHidden ? { detailsHidden } : {}),
    ...(redaction ? { redaction: { ...redaction, sources: [...redactedSources.values()] } } : {}),
  };
}

// ---------------------------------------------------------------------------
// Build citation maps from *streaming* chunk citations
// ---------------------------------------------------------------------------

export function buildCitationMapsFromStreaming(
  rawCitations: StreamingCitationData[]
): CitationMaps {
  const citations: Record<string, CitationData> = {};
  const sources: Record<string, string> = {};
  const sourcesOrder: string[] = [];
  const citationsOrder: Record<number, string> = {};

  for (const raw of rawCitations) {
    const tempId = `streaming-${raw.chunkIndex}`;
    const metadata = raw.metadata;
    if (!metadata) continue;

    const normalized: CitationData = {
      citationId: tempId,
      content: raw.content,
      chunkIndex: raw.chunkIndex,
      recordId: metadata.recordId,
      recordName: metadata.recordName || 'Untitled Document',
      connector: metadata.connector || '',
      connectorId: metadata.connectorId || undefined,
      connectorInstanceName: metadata.connectorInstanceName || undefined,
      recordType: metadata.recordType || '',
      webUrl: metadata.webUrl,
      mimeType: metadata.mimeType || '',
      extension: metadata.extension || '',
      pageNum: filterValidNumbers(metadata.pageNum),
      blockNum: filterValidNumbers(metadata.blockNum),
      previewRenderable: metadata.previewRenderable ?? false,
      hideWeburl: metadata.hideWeburl ?? false,
      citationType: raw.citationType || '',
      origin: metadata.origin,
      boundingBox: metadata.bounding_box,
      updatedAt: metadata.updatedAt || undefined,
    };

    citations[tempId] = normalized;
    citationsOrder[raw.chunkIndex] = tempId;

    if (!sources[metadata.recordId]) {
      sources[metadata.recordId] = tempId;
      sourcesOrder.push(metadata.recordId);
    }
  }

  return { citations, sources, sourcesOrder, citationsOrder };
}

// ---------------------------------------------------------------------------
// Empty / default maps
// ---------------------------------------------------------------------------

export function emptyCitationMaps(): CitationMaps {
  return {
    citations: {},
    sources: {},
    sourcesOrder: [],
    citationsOrder: {},
  };
}

/**
 * URL for a real `<a href>` so the browser context menu offers "Copy link" /
 * "Open in new tab" (modifier-click). Returns undefined when the backend hides
 * the URL or none is present.
 */
export function getCitationCopyHref(citation: CitationData): string | undefined {
  if (citation.hideWeburl) return undefined;
  const raw = citation.webUrl?.trim();
  return raw ? raw : undefined;
}

// ---------------------------------------------------------------------------
// Derived helpers
// ---------------------------------------------------------------------------

/**
 * Format an ISO date string as a relative "Synced X ago" label.
 */
export function formatSyncLabel(isoDate?: string): string | undefined {
  if (!isoDate) return undefined;
  const time = new Date(isoDate).getTime();
  if (Number.isNaN(time)) return undefined;
  const diff = Date.now() - time;
  const synced = (age: string) => i18n.t('chat.recordActions.syncedAgo', { age });
  if (diff < 0) return i18n.t('chat.recordActions.syncedJustNow');
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return i18n.t('chat.recordActions.syncedJustNow');
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return synced(`${minutes}m`);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return synced(`${hours}h`);
  const days = Math.floor(hours / 24);
  if (days < 30) return synced(`${days}d`);
  const months = Math.floor(days / 30);
  if (months < 12) return synced(`${months}mo`);
  const years = Math.floor(months / 12);
  return synced(`${years}y`);
}

/**
 * Count how many citations reference each source (recordId).
 * Returns a map of recordId → count.
 */
export function getCitationCountBySource(
  maps: CitationMaps
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const citation of Object.values(maps.citations)) {
    counts[citation.recordId] = (counts[citation.recordId] || 0) + 1;
  }
  return counts;
}
