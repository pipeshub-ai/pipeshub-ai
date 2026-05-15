'use client';

import type {
  CitationData,
  CitationMaps,
  StreamingCitationData,
  ConnectorConfig,
  CitationOrigin,
} from './types';
import type { CitationApiResponse } from '@/chat/types';

// ---------------------------------------------------------------------------
// Connector configuration (delegates to centralized ConnectorIcon mapping)
// ---------------------------------------------------------------------------

import {
  getConnectorIconConfig,
  resolveConnectorType,
} from '@/app/components/ui/ConnectorIcon';
import { i18n } from '@/lib/i18n';

/** Resolve connector key → display config. */
export function getConnectorConfig(connector: string): ConnectorConfig {
  const resolved = resolveConnectorType(connector);
  const iconConfig = getConnectorIconConfig(connector);
  const isCollections =
    resolved === 'kb' || resolved === 'knowledge-base';
  return {
    label: isCollections
      ? i18n.t('nav.collections')
      : connector || i18n.t('filter.source'),
    icon: iconConfig.svg || '/icons/connectors/GDrive.svg',
  };
}

// ---------------------------------------------------------------------------
// Build citation maps from the *complete* response (CitationApiResponse[])
// ---------------------------------------------------------------------------

export function buildCitationMapsFromApi(
  rawCitations: CitationApiResponse[]
): CitationMaps {
  const citations: Record<string, CitationData> = {};
  const sources: Record<string, string> = {};
  const sourcesOrder: string[] = [];
  const citationsOrder: Record<number, string> = {};

  for (const raw of rawCitations) {
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
      recordType: metadata.recordType || '',
      webUrl: metadata.webUrl,
      mimeType: metadata.mimeType || '',
      extension: metadata.extension || '',
      pageNum: metadata.pageNum,
      blockNum: metadata.blockNum,
      previewRenderable: metadata.previewRenderable ?? false,
      hideWeburl: (metadata as Record<string, unknown>).hideWeburl as boolean ?? false,
      citationType: data.citationType || '',
      origin: (metadata as Record<string, unknown>).origin as CitationOrigin | undefined,
      boundingBox: (metadata as Record<string, unknown>).bounding_box as Array<{ x: number; y: number }> | undefined,
      updatedAt: data.updatedAt,
    };

    citations[citationId] = normalized;
    citationsOrder[data.chunkIndex] = citationId;

    if (!sources[metadata.recordId]) {
      sources[metadata.recordId] = citationId;
      sourcesOrder.push(metadata.recordId);
    }
  }

  return { citations, sources, sourcesOrder, citationsOrder };
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
      recordType: metadata.recordType || '',
      webUrl: metadata.webUrl,
      mimeType: metadata.mimeType || '',
      extension: metadata.extension || '',
      pageNum: metadata.pageNum,
      blockNum: metadata.blockNum,
      previewRenderable: metadata.previewRenderable ?? false,
      hideWeburl: metadata.hideWeburl ?? false,
      citationType: raw.citationType || '',
      origin: metadata.origin,
      boundingBox: metadata.bounding_box,
      updatedAt: undefined, // not available during streaming
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
  const diff = Date.now() - new Date(isoDate).getTime();
  if (diff < 0) return 'Synced just now';
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return 'Synced just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `Synced ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Synced ${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `Synced ${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `Synced ${months}mo ago`;
  const years = Math.floor(months / 12);
  return `Synced ${years}y ago`;
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
