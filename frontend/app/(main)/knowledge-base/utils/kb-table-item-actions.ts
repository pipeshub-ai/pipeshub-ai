/**
 * Shared behavior for Knowledge Base list/grid table rows (menu actions, etc.).
 * Add related helpers here to keep kb-list-view / kb-grid-view free of duplicated logic.
 */

import type { AllRecordItem, KnowledgeBaseItem, KnowledgeHubNode } from '../types';

export type KbTableItem = KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem;

function isKnowledgeHubTableItem(item: KbTableItem): item is KnowledgeHubNode {
  return 'nodeType' in item && 'origin' in item;
}

/**
 * Matches legacy all-records DataGrid: do not render indexing status for internal hub records.
 */
export function shouldHideIndexingStatusForHubRecord(item: KbTableItem): boolean {
  return (
    isKnowledgeHubTableItem(item) &&
    item.nodeType === 'record' &&
    // Placeholder stubs are never indexed, so they carry no meaningful status.
    (item.isInternal === true || item.isPlaceholder === true)
  );
}

/**
 * Placeholder stub records (out-of-scope ancestors backfilled during sync) are
 * metadata-only: they have no content, so content actions must be suppressed.
 * They stay navigable so their in-scope descendants remain reachable.
 */
export function isPlaceholderHubRecord(item: KbTableItem): boolean {
  return (
    isKnowledgeHubTableItem(item) &&
    item.nodeType === 'record' &&
    item.isPlaceholder === true
  );
}

/**
 * Row ⋮ menu Download: collection file records only (not connector-sourced records).
 */
export function shouldShowDownloadForTableItem(item: KbTableItem): boolean {
  if (isPlaceholderHubRecord(item)) {
    return false;
  }
  if (!isKnowledgeHubTableItem(item)) {
    return item.type === 'file';
  }
  return item.nodeType === 'record' && item.origin === 'COLLECTION';
}

/**
 * Row ⋮ menu "Open": open preview for Hub/legacy file records when `onPreview` exists;
 * otherwise use the same handler as primary row click (navigate into containers, etc.).
 */
export function runItemMenuOpenFromMenu(
  item: KbTableItem,
  onItemClick: (item: KbTableItem) => void,
  onPreview?: (item: KbTableItem) => void,
): void {
  const usePreview =
    !isPlaceholderHubRecord(item) &&
    ((isKnowledgeHubTableItem(item) && item.nodeType === 'record') ||
      (!isKnowledgeHubTableItem(item) && item.type === 'file'));
  if (usePreview && onPreview) onPreview(item);
  else onItemClick(item);
}
