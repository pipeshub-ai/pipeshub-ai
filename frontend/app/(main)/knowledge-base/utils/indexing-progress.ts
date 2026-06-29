import type { IndexingStage, IndexingStatus } from '../types';

/**
 * Default heartbeat age after which an IN_PROGRESS record is treated as stalled.
 * Generous on purpose: a slow-but-healthy job (e.g. a large PDF going through
 * VLM OCR with many model calls) only updates its heartbeat at coarse pipeline
 * checkpoints, so a short threshold would produce false "stuck" warnings.
 */
export const STALL_THRESHOLD_MS = 3 * 60 * 1000;

/** Minimal shape needed to derive a progress view; matches KnowledgeHubNode. */
export interface IndexingProgressInput {
  indexingStatus?: IndexingStatus | null;
  indexingStage?: IndexingStage | null;
  lastActivityTimestamp?: number | null;
  reason?: string | null;
}

export interface IndexingProgressView {
  /** True while the record can still change on its own (QUEUED or IN_PROGRESS). */
  isActive: boolean;
  /** True when IN_PROGRESS and the heartbeat has not advanced for a while. */
  isStalled: boolean;
  /** Short label for the current phase ("Queued", "Extracting content", …). */
  label: string;
  /** Coarse 0–100 indicator derived from the stage (stage-weighted, not linear). */
  percent: number;
  /** Optional sub-line; carries the reason when stalled. */
  detail?: string;
}

const STAGE_LABEL: Record<IndexingStage, string> = {
  QUEUED: 'Queued',
  EXTRACTING: 'Extracting content',
  INDEXING: 'Indexing',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
};

const STAGE_PERCENT: Record<IndexingStage, number> = {
  QUEUED: 5,
  EXTRACTING: 35,
  INDEXING: 75,
  COMPLETED: 100,
  FAILED: 100,
};

/**
 * A status is "terminal" when it will not change without user action. Only
 * QUEUED and IN_PROGRESS are non-terminal; everything else (COMPLETED, FAILED,
 * EMPTY, AUTO_INDEX_OFF, …) is settled. Used to gate polling and stall checks.
 */
export function isActiveIndexingStatus(status?: IndexingStatus | string | null): boolean {
  return status === 'IN_PROGRESS' || status === 'QUEUED';
}

/** True if any record in the list is still in flight (drives gated polling). */
export function hasActiveIndexing(
  nodes: ReadonlyArray<{ indexingStatus?: IndexingStatus | string | null }>,
): boolean {
  return nodes.some((n) => isActiveIndexingStatus(n.indexingStatus));
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
  stallThresholdMs: number = STALL_THRESHOLD_MS,
): IndexingProgressView {
  const status = node.indexingStatus ?? null;

  if (status === 'QUEUED') {
    return { isActive: true, isStalled: false, label: 'Queued', percent: STAGE_PERCENT.QUEUED };
  }

  if (status === 'IN_PROGRESS') {
    const stage = node.indexingStage ?? null;
    const beat = node.lastActivityTimestamp ?? null;
    const isStalled = beat != null && now - beat > stallThresholdMs;
    const inFlightStage = stage === 'EXTRACTING' || stage === 'INDEXING' ? stage : null;
    const label = inFlightStage ? STAGE_LABEL[inFlightStage] : 'Processing';
    const percent = inFlightStage ? STAGE_PERCENT[inFlightStage] : STAGE_PERCENT.EXTRACTING;

    if (isStalled) {
      return {
        isActive: true,
        isStalled: true,
        label: 'Stalled',
        percent,
        detail: node.reason?.trim() || 'No recent progress — the file may be slow or stuck',
      };
    }
    return { isActive: true, isStalled: false, label, percent };
  }

  return {
    isActive: false,
    isStalled: false,
    label: '',
    percent: status === 'COMPLETED' ? 100 : 0,
  };
}
