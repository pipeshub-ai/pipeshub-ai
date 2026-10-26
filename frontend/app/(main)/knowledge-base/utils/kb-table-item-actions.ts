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
 * Whether the user may delete this row: a hub item by its own permission (a shared
 * item can have a lower role than its folder), and a collection only as its OWNER.
 */
export function canDeleteKbTableItem(item: KbTableItem): boolean {
  if (!isKnowledgeHubTableItem(item)) return true;
  if (!(item.permission?.canDelete ?? false)) return false;
  return !(item.nodeType === 'app' && item.permission?.role !== 'OWNER');
}

/**
 * A folder is renamed and deleted within its collection, and any row is moved
 * within one. A search at the Collections root has no collection open, so
 * those actions would fail there.
 */
export function needsCollectionContext(item: KbTableItem, action: 'rename' | 'move' | 'delete'): boolean {
  if (action === 'move') return true;
  return isKnowledgeHubTableItem(item)
    ? item.nodeType === 'folder' || item.nodeType === 'recordGroup'
    : 'type' in item && item.type === 'folder';
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
