// Status-aware label / disable / toast wording for the row-level reindex action.
//
// Mirrors the legacy frontend's getReindexButtonText / getReindexTooltip in
// frontend/src/sections/knowledgebase/components/buttons.tsx, plus the nodeType
// rules from all-records-view.tsx (folders and recordGroups always show
// "Start indexing").
//
// The toast wording distinguishes:
//   - "indexing"   — the very first attempt for this document
//                    (NOT_STARTED, AUTO_INDEX_OFF, folders, recordGroups)
//   - "reindexing" — any subsequent attempt
//                    (COMPLETED, FAILED, QUEUED, PAUSED, EMPTY, etc.)

export type ReindexAction =
  | 'force-reindex'
  | 'retry-indexing'
  | 'start-indexing'
  | 'resume-indexing'
  | 'reindex'
  | 'unsupported'
  | 'in-progress';

interface ReindexNode {
  nodeType?: string;
  indexingStatus?: string | null;
}

/** Determine the reindex action this node should trigger. */
export function getReindexAction(node: ReindexNode): ReindexAction {
  // Folders and recordGroups always behave as "Start indexing" regardless of status
  if (node.nodeType === 'folder' || node.nodeType === 'recordGroup') {
    return 'start-indexing';
  }

  switch (node.indexingStatus) {
    case 'COMPLETED':
      return 'force-reindex';
    case 'FAILED':
    case 'QUEUED':
    case 'EMPTY':
    case 'ENABLE_MULTIMODAL_MODELS':
    case 'CONNECTOR_DISABLED':
      return 'retry-indexing';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'unsupported';
    case 'AUTO_INDEX_OFF':
    case 'NOT_STARTED':
      return 'start-indexing';
    case 'PAUSED':
      return 'resume-indexing';
    case 'IN_PROGRESS':
      return 'in-progress';
    default:
      return 'reindex';
  }
}

/** Human-readable label for the menu item / button. */
export function getReindexLabel(node: ReindexNode): string {
  switch (getReindexAction(node)) {
    case 'force-reindex':
      return 'Force reindexing';
    case 'retry-indexing':
      return 'Retry indexing';
    case 'start-indexing':
      return 'Start indexing';
    case 'resume-indexing':
      return 'Resume indexing';
    case 'unsupported':
      return 'File not supported';
    case 'in-progress':
    case 'reindex':
    default:
      return 'Reindex';
  }
}

/** Material icon name for the reindex menu item. */
export function getReindexIcon(node: ReindexNode): string {
  return getReindexAction(node) === 'force-reindex' ? 'redo' : 'refresh';
}

/** Whether the reindex menu item should be disabled. */
export function isReindexDisabled(node: ReindexNode): boolean {
  const action = getReindexAction(node);
  return action === 'unsupported' || action === 'in-progress';
}

/**
 * Success-toast title for a queued reindex/index job.
 *
 *  - "indexing"   when the document/folder/recordGroup is being indexed for
 *                 the first time
 *  - "reindexing" otherwise
 */
export function getReindexSuccessTitle(node: ReindexNode): string {
  switch (getReindexAction(node)) {
    case 'start-indexing':
      return 'Successfully queued for indexing';
    case 'force-reindex':
    case 'retry-indexing':
    case 'resume-indexing':
    case 'reindex':
    default:
      return 'Successfully queued for reindexing';
  }
}
