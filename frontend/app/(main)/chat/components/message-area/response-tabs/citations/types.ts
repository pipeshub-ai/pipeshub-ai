'use client';

/**
 * Origin type for citations — indicates where the record came from
 */
export type CitationOrigin = 'UPLOAD' | 'CONNECTOR';

/**
 * Normalized citation data — a flat, easy-to-consume shape derived from either
 * the SSE `answer_chunk` citations (streaming) or the `complete` / GET response
 * citations (historical).
 */
export interface CitationData {
  /** Real citationId from backend, or temp `streaming-{chunkIndex}` during SSE */
  citationId: string;
  /** The cited text content / snippet */
  content: string;
  /** The `[N]` number that appears in the answer markdown */
  chunkIndex: number;
  /** Record (file/document) ID */
  recordId: string;
  /** Human-readable file/document name */
  recordName: string;
  /** Connector key e.g. "ONEDRIVE", "SHAREPOINT ONLINE", "SLACK" */
  connector: string;
  /** Record type e.g. "FILE" */
  recordType: string;
  /** Direct URL to the source document */
  webUrl?: string;
  /** MIME type e.g. "application/pdf" */
  mimeType: string;
  /** File extension without dot e.g. "pdf" */
  extension: string;
  /** Page numbers where the citation appears */
  pageNum?: number[];
  /** Block / paragraph numbers */
  blockNum?: number[];
  /** Whether the file can be rendered in preview */
  previewRenderable: boolean;
  /** Whether the webUrl should be hidden from the user */
  hideWeburl: boolean;
  /** Citation type from backend e.g. "vectordb|document" */
  citationType: string;
  /** Origin of the record — UPLOAD for KB/collection records, CONNECTOR for external sources */
  origin?: CitationOrigin;
  /** Bounding box for highlighting the cited region (normalized 0-1 coordinates) */
  boundingBox?: Array<{ x: number; y: number }>;
  /** ISO timestamp of when the citation chunk was last updated (used for "Synced X ago") */
  updatedAt?: string;
}

/**
 * The four derived maps that make lookups fast.
 *
 * Built once per message (or rebuilt on each streaming chunk)
 * and passed through metadata / props.
 */
export interface CitationMaps {
  /** Keyed by citationId → full citation data */
  citations: Record<string, CitationData>;
  /** Keyed by recordId → the **first** citationId for that record */
  sources: Record<string, string>;
  /** recordIds in the order they first appear across citations */
  sourcesOrder: string[];
  /** Keyed by chunkIndex (the `[N]` number) → citationId */
  citationsOrder: Record<number, string>;
}

/**
 * Raw citation shape received in SSE `answer_chunk` events.
 * These do NOT have a citationId — only a chunkIndex.
 */
export interface StreamingCitationData {
  content: string;
  chunkIndex: number;
  metadata: {
    orgId: string;
    recordId: string;
    virtualRecordId?: string;
    recordName: string;
    recordType: string;
    recordVersion: number;
    origin: CitationOrigin;
    connector: string;
    blockText: string;
    blockType: string;
    bounding_box?: Array<{ x: number; y: number }>;
    pageNum?: number[];
    extension: string;
    mimeType: string;
    blockNum?: number[];
    webUrl?: string;
    previewRenderable: boolean;
    hideWeburl: boolean;
  };
  citationType: string;
}

/**
 * Connector display configuration for icons and labels.
 */
export interface ConnectorConfig {
  /** Friendly display name */
  label: string;
  /** Path to the connector SVG icon */
  icon: string;
}

/**
 * Callbacks for citation interactions.
 */
export interface CitationCallbacks {
  onPreview?: (citation: CitationData, citationMaps?: CitationMaps) => void;
  onOpenInCollection?: (citation: CitationData) => void;
}
