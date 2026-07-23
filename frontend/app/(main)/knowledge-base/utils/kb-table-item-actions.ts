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
 * Web connector URL path segments are stored as internal HTML records so they can
 * sit in the same parent/child graph as pages, but in the browser they behave as
 * containers. Treat them like folders for icons, actions, and rollup status.
 */
export function isWebPathPlaceholder(item: KbTableItem): item is KnowledgeHubNode {
  return (
    isKnowledgeHubTableItem(item) &&
    item.nodeType === 'record' &&
    item.isInternal === true &&
    item.connector?.toUpperCase() === 'WEB' &&
    item.hasChildren === true
  );
}

export function isFolderLikeTableItem(item: KbTableItem): boolean {
  if (!isKnowledgeHubTableItem(item)) {
    return item.type === 'folder';
  }
  return (
    ['kb', 'app', 'folder', 'recordGroup'].includes(item.nodeType) ||
    isWebPathPlaceholder(item)
  );
}

/**
 * Matches legacy all-records DataGrid: do not render indexing status for internal hub records.
 */
export function shouldHideIndexingStatusForHubRecord(item: KbTableItem): boolean {
  return (
    isKnowledgeHubTableItem(item) &&
    item.nodeType === 'record' &&
    item.isInternal === true &&
    !isWebPathPlaceholder(item)
  );
}

/**
 * Row ⋮ menu Download: collection file records only (not connector-sourced records).
 */
export function shouldShowDownloadForTableItem(item: KbTableItem): boolean {
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
    (isKnowledgeHubTableItem(item) && item.nodeType === 'record' && !isWebPathPlaceholder(item)) ||
    (!isKnowledgeHubTableItem(item) && item.type === 'file');
  if (usePreview && onPreview) onPreview(item);
  else onItemClick(item);
}
