import type { IndexingProgressMetrics, IndexingRollup, IndexingStage, IndexingStatus } from '../types';

/**
 * Extraction emits no fine-grained backend metric on opaque parser paths (e.g.
 * external Docling), so its bar is estimated from file size and type. PDFs get
 * a generous minimum duration because page count dominates wall time and is
 * not available client-side — a 750 KB book PDF can still take 30+ minutes.
 * The curve is asymptotic so the bar keeps inching forward on long jobs instead
 * of freezing at the band ceiling. Indexing uses real backend metrics only.
 */
const EXTRACTION_BASE_MS = 12 * 1000;
const BYTES_PER_MB = 1024 * 1024;
/** Floor for PDF/image extraction — matches long Docling runs on book-length PDFs. */
const PDF_MIN_DURATION_MS = 30 * 60 * 1000;
/** Estimate never claims more than this fraction of the extraction band. */
const EXTRACTION_ESTIMATE_BAND_FRACTION = 0.92;

type ExtractionClass = 'ocr' | 'office' | 'text';

/** Estimated extraction throughput (ms per MB) by file-type class. */
const EXTRACTION_MS_PER_MB: Record<ExtractionClass, number> = {
  ocr: 20 * 60 * 1000,
  office: 8 * 1000,
  text: 2 * 1000,
};

const OCR_EXTENSIONS = new Set([
  'pdf', 'png', 'jpg', 'jpeg', 'tiff', 'tif', 'bmp', 'gif', 'webp', 'heic', 'heif',
]);
const OFFICE_EXTENSIONS = new Set([
  'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'odt', 'odp', 'ods', 'rtf',
]);

/** Minimal shape needed to derive a progress view; matches KnowledgeHubNode. */
export interface IndexingProgressInput {
  indexingStatus?: IndexingStatus | null;
  indexingStage?: IndexingStage | null;
  lastActivityTimestamp?: number | null;
  indexingProgress?: IndexingProgressMetrics | null;
  reason?: string | null;
  sizeInBytes?: number | null;
  extension?: string | null;
  mimeType?: string | null;
}

export interface IndexingProgressView {
  /** True while the record can still change on its own (QUEUED or IN_PROGRESS). */
  isActive: boolean;
  /** Short label for the current phase ("Queued", "Extracting content", …). */
  label: string;
  /** 0–100 indicator from substage metrics when available, else an estimate/fallback. */
  percent: number;
  /** Stepper position: 0 Queued, 1 Extract, 2 Index, 3 Done. */
  stepIndex: number;
  /** Optional sub-line; carries the backend progress message when present. */
  detail?: string;
  /** True when percent is a frontend estimate rather than backend-emitted metrics. */
  isEstimated?: boolean;
}

export const PROGRESS_STEPS = ['Queued', 'Extract', 'Index', 'Done'] as const;

const STAGE_LABEL: Record<IndexingStage, string> = {
  QUEUED: 'Queued',
  EXTRACTING: 'Extracting content',
  INDEXING: 'Indexing',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
};

/**
 * Contiguous progress bands so the bar never has a dead zone: queued occupies
 * the first sliver, extraction fills 5→65, indexing 65→98, completion snaps to
 * 100. Each stage's fallback percent is the *start* of its band, so entering a
 * stage continues smoothly from where the previous one ended.
 */
const STAGE_PERCENT: Record<IndexingStage, number> = {
  QUEUED: 5,
  EXTRACTING: 5,
  INDEXING: 65,
  COMPLETED: 100,
  FAILED: 100,
};

const STAGE_PROGRESS_BOUNDS: Partial<Record<IndexingStage, { start: number; end: number }>> = {
  EXTRACTING: { start: STAGE_PERCENT.EXTRACTING, end: 65 },
  INDEXING: { start: STAGE_PERCENT.INDEXING, end: 98 },
};

/** Backend `indexingProgress.phase` expected for each stage's metrics. */
const STAGE_METRIC_PHASE: Partial<Record<IndexingStage, string>> = {
  EXTRACTING: 'extracting',
  INDEXING: 'embedding',
};

function extractionClass(input: IndexingProgressInput): ExtractionClass {
  const ext = (input.extension ?? '').toLowerCase().replace(/^\./, '');
  const mime = (input.mimeType ?? '').toLowerCase();
  if (OCR_EXTENSIONS.has(ext) || mime.includes('pdf') || mime.startsWith('image/')) {
    return 'ocr';
  }
  if (
    OFFICE_EXTENSIONS.has(ext) ||
    mime.includes('officedocument') ||
    mime.includes('msword') ||
    mime.includes('ms-excel') ||
    mime.includes('ms-powerpoint')
  ) {
    return 'office';
  }
  return 'text';
}

/** Expected extraction duration, derived from the record's size and file type. */
export function extractionDurationMs(input: IndexingProgressInput): number {
  const bytes = input.sizeInBytes && input.sizeInBytes > 0 ? input.sizeInBytes : 0;
  const mb = bytes / BYTES_PER_MB;
  const cls = extractionClass(input);
  const scaled = EXTRACTION_BASE_MS + mb * EXTRACTION_MS_PER_MB[cls];
  if (cls === 'ocr') {
    return Math.max(PDF_MIN_DURATION_MS, scaled);
  }
  return scaled;
}

/** Sub-status under one uploaded file — never say "documents" (that means chunks). */
export function formatProgressDetail(
  stage: IndexingStage | null,
  metrics?: IndexingProgressMetrics | null,
): string | undefined {
  if (!metrics || !(metrics.total > 0)) {
    return metrics?.message?.trim() || undefined;
  }
  const current = Math.max(0, Math.min(metrics.current, metrics.total));
  const { total, phase, unit } = metrics;

  if (phase === 'embedding' || stage === 'INDEXING' || unit === 'chunks' || unit === 'documents') {
    return `Embedding chunk ${current} of ${total}`;
  }
  if (phase === 'extracting' || stage === 'EXTRACTING') {
    if (unit === 'pages' || unit === 'page') {
      return `Extracting page ${current} of ${total}`;
    }
  }

  const message = metrics.message?.trim();
  return message || undefined;
}

function percentFromMetrics(stage: IndexingStage, metrics?: IndexingProgressMetrics | null): number | null {
  if (!metrics || typeof metrics !== 'object' || !(metrics.total > 0)) return null;
  const bounds = STAGE_PROGRESS_BOUNDS[stage];
  if (!bounds) return null;
  // Guard against a stale metric from the previous stage bleeding into this one.
  const expectedPhase = STAGE_METRIC_PHASE[stage];
  if (expectedPhase && metrics.phase && metrics.phase !== expectedPhase) return null;
  const ratio = Math.max(0, Math.min(metrics.current / metrics.total, 1));
  return Math.round(bounds.start + (bounds.end - bounds.start) * ratio);
}

function estimateExtractionPercent(
  input: IndexingProgressInput,
  startedAt: number | null,
  now: number,
): number | null {
  if (startedAt == null) return null;
  const bounds = STAGE_PROGRESS_BOUNDS.EXTRACTING;
  if (!bounds) return null;
  const elapsed = Math.max(0, now - startedAt);
  const duration = extractionDurationMs(input);
  // Asymptotic: keeps moving on long opaque parses; never hits the band end.
  const ratio = 1 - Math.exp(-elapsed / duration);
  return Math.round(
    bounds.start +
      (bounds.end - bounds.start) * EXTRACTION_ESTIMATE_BAND_FRACTION * ratio,
  );
}

/**
 * A status is "terminal" when it will not change without user action. Only
 * QUEUED and IN_PROGRESS are non-terminal; everything else (COMPLETED, FAILED,
 * EMPTY, AUTO_INDEX_OFF, …) is settled. Used to gate polling.
 */
export function isActiveIndexingStatus(status?: IndexingStatus | string | null): boolean {
  return status === 'IN_PROGRESS' || status === 'QUEUED';
}

/**
 * True if any row is still in flight — either a leaf record that is QUEUED/
 * IN_PROGRESS, or a container whose subtree rollup is still active. The latter
 * keeps polling alive while descendants index even when no visible leaf row is
 * itself active (e.g. browsing a collection whose child folders are indexing).
 */
export function hasActiveIndexing(
  nodes: ReadonlyArray<{
    indexingStatus?: IndexingStatus | string | null;
    indexingRollup?: IndexingRollup | null;
  }>,
): boolean {
  return nodes.some(
    (n) => isActiveIndexingStatus(n.indexingStatus) || n.indexingRollup?.isActive === true,
  );
}

export interface RollupProgressView {
  /** True while descendants are still queued or in progress. */
  isActive: boolean;
  /** Weighted completion percentage (0–100). */
  percent: number;
  /** e.g. "42 of 96 indexed". */
  label: string;
  /** Optional secondary line for failed/skipped counts, when any. */
  detail?: string;
  /** True once nothing is active but some records failed. */
  hasErrors: boolean;
}

/** Settled container breakdown: "10 records: 7 indexed · 2 failed · 1 skipped". */
export function formatRollupFailureDetail(
  rollup: Pick<IndexingRollup, 'completed' | 'total' | 'failed' | 'skipped'>,
): string {
  const noun = rollup.total === 1 ? 'record' : 'records';
  const parts = [`${rollup.total} ${noun}: ${rollup.completed} indexed`];
  if (rollup.failed > 0) parts.push(`${rollup.failed} failed`);
  if (rollup.skipped > 0) parts.push(`${rollup.skipped} skipped`);
  return parts.join(' · ');
}

/** Build the container-row display model from an aggregated rollup. */
export function getRollupProgressView(rollup: IndexingRollup): RollupProgressView {
  const hasNonIndexedResults = rollup.failed > 0 || rollup.skipped > 0;
  const hasErrors = !rollup.isActive && hasNonIndexedResults;

  const detail =
    !rollup.isActive && hasErrors
      ? formatRollupFailureDetail(rollup)
      : undefined;
  return {
    isActive: rollup.isActive,
    percent: Math.max(0, Math.min(100, rollup.percent)),
    label: rollup.isActive
      ? `${rollup.completed} of ${rollup.total} indexed`
      : hasNonIndexedResults
        ? `${rollup.completed} of ${rollup.total} indexed`
        : `${rollup.total} indexed`,
    detail,
    hasErrors,
  };
}

/**
 * Derive the in-progress display for a record. Terminal records return
 * `isActive: false` and an empty label — callers keep rendering those purely
 * from `indexingStatus` (unchanged behaviour). The stage is only consulted
 * while IN_PROGRESS, so a stale stage left over from a prior run is ignored.
 */
export function getIndexingProgressView(
  node: IndexingProgressInput,
  now: number = Date.now(),
): IndexingProgressView {
  const status = node.indexingStatus ?? null;

  if (status === 'QUEUED') {
    return { isActive: true, label: 'Queued', percent: STAGE_PERCENT.QUEUED, stepIndex: 0 };
  }

  if (status === 'IN_PROGRESS') {
    const stage = node.indexingStage ?? null;
    const beat = node.lastActivityTimestamp ?? null;
    const inFlightStage = stage === 'EXTRACTING' || stage === 'INDEXING' ? stage : null;
    const label = inFlightStage ? STAGE_LABEL[inFlightStage] : 'Processing';
    const stepIndex = stage === 'INDEXING' ? 2 : 1;

    // Both stages prefer real backend metrics. Extraction additionally has a
    // size/type-based estimate as a cold-start fallback; indexing never guesses.
    const metricPercent = inFlightStage
      ? percentFromMetrics(inFlightStage, node.indexingProgress)
      : null;
    const estimatedPercent =
      metricPercent == null && inFlightStage === 'EXTRACTING'
        ? estimateExtractionPercent(node, beat, now)
        : null;

    const percent =
      metricPercent ??
      estimatedPercent ??
      (inFlightStage ? STAGE_PERCENT[inFlightStage] : STAGE_PERCENT.EXTRACTING);
    const isEstimated = metricPercent == null && estimatedPercent != null;
    const detail = formatProgressDetail(inFlightStage, node.indexingProgress);

    return { isActive: true, label, percent, stepIndex, detail, isEstimated };
  }

  return {
    isActive: false,
    label: '',
    percent: status === 'COMPLETED' ? 100 : 0,
    stepIndex: status === 'COMPLETED' ? 3 : 0,
  };
}
