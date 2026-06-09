// Status-aware reindex menu labels and scope rules.
//
// Bulk (folder / recordGroup / parent record with children):
//   - folder & recordGroup: fixed 3 options — Index/Reindex all, Reindex failed, Start indexing (AUTO_INDEX_OFF only)
//   - parent record (indexable): primary opens scope modal (self vs with children)
//   - Filtered rows (FAILED, AUTO_INDEX_OFF descendants): depth 100 + filter, no modal
//
// Leaf record (table): single option, depth 0, no modal.

import { FOLDER_REINDEX_DEPTH, REINDEX_SELF_DEPTH } from '../constants';

export type ReindexAction =
  | 'force-reindex'
  | 'retry-indexing'
  | 'start-indexing'
  | 'reindex'
  | 'unsupported';

export interface ReindexNode {
  nodeType?: string;
  indexingStatus?: string | null;
  hasChildren?: boolean;
}

export type ReindexMenuLabelKey =
  | 'menu.indexAll'
  | 'menu.startIndexing'
  | 'menu.reindexAll'
  | 'menu.reindexFailed'
  | 'menu.forceReindexing'
  | 'menu.retryIndexing'
  | 'menu.reindex';

export type ReindexMenuOption = {
  icon: string;
  labelKey: ReindexMenuLabelKey;
  statusFilters?: ('FAILED' | 'AUTO_INDEX_OFF')[];
  disabled?: boolean;
};

const LABEL_FALLBACKS: Record<ReindexMenuLabelKey, string> = {
  'menu.indexAll': 'Index all',
  'menu.startIndexing': 'Start indexing',
  'menu.reindexAll': 'Reindex all',
  'menu.reindexFailed': 'Reindex failed',
  'menu.forceReindexing': 'Force reindex',
  'menu.retryIndexing': 'Retry indexing',
  'menu.reindex': 'Reindex',
};

/** Resolve menu label with English fallback when i18n key is missing. */
export function getReindexMenuLabel(
  option: ReindexMenuOption,
  t: (key: string) => string,
): string {
  const translated = t(option.labelKey);
  return translated === option.labelKey ? LABEL_FALLBACKS[option.labelKey] : translated;
}

/** Build a reindex node from hub/sidebar API fields (same shape as table rows). */
export function getReindexNodeFromHubItem(item: {
  nodeType?: string;
  indexingStatus?: string | null;
  hasChildren?: boolean;
}): ReindexNode {
  return {
    nodeType: item.nodeType,
    indexingStatus: item.indexingStatus,
    hasChildren: item.hasChildren,
  };
}

export function getReindexMenuState(
  reindexNode: ReindexNode,
  enabled: boolean,
): { reindexNode: ReindexNode; options: ReindexMenuOption[]; showMenu: boolean } {
  const options =
    enabled && canShowReindexMenu(reindexNode) ? getReindexMenuOptions(reindexNode) : [];
  return { reindexNode, options, showMenu: options.length > 0 };
}

/** Map reindex options to ItemActionMenu entries (shared by list/grid and sidebar). */
export function mapReindexOptionsToMenuActions(
  options: ReindexMenuOption[],
  t: (key: string) => string,
  onSelect: (statusFilters?: ('FAILED' | 'AUTO_INDEX_OFF')[]) => void,
): Array<{
  icon: string;
  label: string;
  labelKey: ReindexMenuLabelKey;
  statusFilters?: ('FAILED' | 'AUTO_INDEX_OFF')[];
  disabled?: boolean;
  onClick: () => void;
}> {
  return options.map((option) => ({
    icon: option.icon,
    label: getReindexMenuLabel(option, t),
    labelKey: option.labelKey,
    statusFilters: option.statusFilters,
    disabled: option.disabled,
    onClick: () => onSelect(option.statusFilters),
  }));
}

/** folder / recordGroup — container rows are not indexed as entities. */
export function isNonIndexableContainer(node: ReindexNode): boolean {
  return node.nodeType === 'folder' || node.nodeType === 'recordGroup';
}

/** Indexable record with descendants — primary action opens self vs subtree modal. */
export function needsReindexScopeModal(node: ReindexNode): boolean {
  return node.nodeType === 'record' && node.hasChildren === true;
}

/** Whether this node supports the bulk reindex menu (container with descendants). */
export function supportsBulkReindex(node: ReindexNode): boolean {
  if (node.nodeType === 'app' || node.hasChildren !== true) return false;
  return (
    node.nodeType === 'folder'
    || node.nodeType === 'recordGroup'
    || node.nodeType === 'record'
  );
}

function leafActionFromStatus(indexingStatus?: string | null): ReindexAction {
  switch (indexingStatus) {
    case 'COMPLETED':
      return 'force-reindex';
    case 'IN_PROGRESS':
      return 'unsupported';
    case 'FAILED':
    case 'EMPTY':
    case 'ENABLE_MULTIMODAL_MODELS':
    case 'CONNECTOR_DISABLED':
      return 'retry-indexing';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'unsupported';
    case 'AUTO_INDEX_OFF':
    case 'NOT_STARTED':
      return 'start-indexing';
    default:
      return 'reindex';
  }
}

/** Unfiltered subtree action for folder / recordGroup (not indexed as entities). */
function nonIndexableContainerPrimaryLabelKey(node: ReindexNode): ReindexMenuLabelKey {
  const status = node.indexingStatus;
  if (status === 'NOT_STARTED' || status == null) {
    return 'menu.indexAll';
  }
  return 'menu.reindexAll';
}

/** folder / recordGroup — always Index/Reindex all, Reindex failed, Start indexing (AUTO_INDEX_OFF only). */
function getContainerBulkReindexMenuOptions(node: ReindexNode): ReindexMenuOption[] {
  return [
    { icon: 'refresh', labelKey: nonIndexableContainerPrimaryLabelKey(node) },
    REINDEX_FAILED_OPTION,
    START_INDEXING_AUTO_INDEX_OFF_OPTION,
  ];
}

/** Label for bulk menu option 1. */
function bulkPrimaryLabelKey(node: ReindexNode): ReindexMenuLabelKey {
  if (needsReindexScopeModal(node)) {
    return leafReindexLabelKey(leafActionFromStatus(node.indexingStatus));
  }
  return nonIndexableContainerPrimaryLabelKey(node);
}

/** Label key for the generic (unfiltered) primary menu row — used in scope modal title. */
export function getPrimaryReindexMenuLabelKey(node: ReindexNode): ReindexMenuLabelKey {
  if (!supportsBulkReindex(node)) {
    return leafReindexLabelKey(leafActionFromStatus(node.indexingStatus));
  }
  return bulkPrimaryLabelKey(node);
}

const REINDEX_FAILED_OPTION: ReindexMenuOption = {
  icon: 'error_outline',
  labelKey: 'menu.reindexFailed',
  statusFilters: ['FAILED'],
};

const START_INDEXING_AUTO_INDEX_OFF_OPTION: ReindexMenuOption = {
  icon: 'pause_circle_outline',
  labelKey: 'menu.startIndexing',
  statusFilters: ['AUTO_INDEX_OFF'],
};

/** Leaf record already waiting in the indexing pipeline — no self reindex action. */
function isQueuedLeaf(node: ReindexNode): boolean {
  return node.indexingStatus === 'QUEUED' && !supportsBulkReindex(node);
}

function getQueuedParentFilteredOptions(): ReindexMenuOption[] {
  return [
    REINDEX_FAILED_OPTION,
    START_INDEXING_AUTO_INDEX_OFF_OPTION,
  ];
}

function getBulkReindexMenuOptions(node: ReindexNode): ReindexMenuOption[] {
  if (isNonIndexableContainer(node)) {
    return getContainerBulkReindexMenuOptions(node);
  }

  if (node.indexingStatus === 'QUEUED' && needsReindexScopeModal(node)) {
    return getQueuedParentFilteredOptions();
  }

  if (node.indexingStatus === 'AUTO_INDEX_OFF' && needsReindexScopeModal(node)) {
    return [
      { icon: 'refresh', labelKey: leafReindexLabelKey(leafActionFromStatus(node.indexingStatus)) },
      REINDEX_FAILED_OPTION,
    ];
  }

  const primaryKey = bulkPrimaryLabelKey(node);
  const options: ReindexMenuOption[] = [
    { icon: 'refresh', labelKey: primaryKey },
    REINDEX_FAILED_OPTION,
  ];

  if (primaryKey !== 'menu.startIndexing') {
    options.push(START_INDEXING_AUTO_INDEX_OFF_OPTION);
  }

  return options;
}

/** Toast variant from chosen depth / filters. */
export function getReindexToastKind(
  node: ReindexNode,
  depth: number,
  statusFilters?: string[],
): 'indexing' | 'reindexing' {
  if (statusFilters?.length) return 'reindexing';
  if (depth === FOLDER_REINDEX_DEPTH) {
    const status = node.indexingStatus;
    if (isNonIndexableContainer(node) && (status === 'NOT_STARTED' || status == null)) {
      return 'indexing';
    }
    return 'reindexing';
  }
  if (depth === REINDEX_SELF_DEPTH) {
    const action = leafActionFromStatus(node.indexingStatus);
    return action === 'start-indexing' ? 'indexing' : 'reindexing';
  }
  return 'reindexing';
}

/** Determine the reindex action for legacy guards. */
export function getReindexAction(node: ReindexNode): ReindexAction {
  if (needsReindexScopeModal(node)) {
    return leafActionFromStatus(node.indexingStatus);
  }
  if (supportsBulkReindex(node)) {
    const status = node.indexingStatus;
    if (status === 'NOT_STARTED' || status === 'AUTO_INDEX_OFF' || status == null) {
      return 'start-indexing';
    }
    return 'reindex';
  }
  return leafActionFromStatus(node.indexingStatus);
}

/** Whether the user must confirm before starting a force-reindex (not filtered submenu actions). */
export function requiresForceReindexConfirmation(
  node: ReindexNode,
  statusFilters?: string[],
): boolean {
  if (statusFilters?.length) return false;
  return getReindexAction(node) === 'force-reindex';
}

/** Human-readable label for the menu item / button. */
export function getReindexLabel(node: ReindexNode): string {
  switch (getReindexAction(node)) {
    case 'force-reindex':
      return 'Force reindex';
    case 'retry-indexing':
      return 'Retry indexing';
    case 'start-indexing':
      return 'Start indexing';
    case 'unsupported':
      return 'File not supported';
    case 'reindex':
    default:
      return 'Reindex';
  }
}

function iconForAction(action: ReindexAction): string {
  return action === 'force-reindex' ? 'redo' : 'refresh';
}

/** Material icon name for the reindex menu item. */
export function getReindexIcon(node: ReindexNode): string {
  return iconForAction(getReindexAction(node));
}

/** Whether all reindex menu items should be disabled (legacy row-level guard). */
export function isReindexDisabled(node: ReindexNode): boolean {
  if (!supportsBulkReindex(node)) {
    return leafActionFromStatus(node.indexingStatus) === 'unsupported';
  }
  return false;
}

/** Whether reindex menu items should be shown (connector app nodes and empty containers excluded). */
export function canShowReindexMenu(node: ReindexNode): boolean {
  if (node.nodeType === 'app') return false;
  if (node.indexingStatus === 'IN_PROGRESS') return false;
  if (isQueuedLeaf(node)) return false;
  const isEmptyContainer =
    (node.nodeType === 'folder' || node.nodeType === 'recordGroup')
    && node.hasChildren !== true;
  return !isEmptyContainer;
}

function leafReindexLabelKey(action: ReindexAction): ReindexMenuLabelKey {
  switch (action) {
    case 'start-indexing':
      return 'menu.startIndexing';
    case 'force-reindex':
      return 'menu.forceReindexing';
    case 'retry-indexing':
      return 'menu.retryIndexing';
    case 'unsupported':
      return 'menu.reindex';
    case 'reindex':
    default:
      return 'menu.reindex';
  }
}

/** Status-aware reindex menu options for a table row or sidebar node. */
export function getReindexMenuOptions(node: ReindexNode): ReindexMenuOption[] {
  if (!canShowReindexMenu(node)) return [];

  if (!supportsBulkReindex(node)) {
    const action = leafActionFromStatus(node.indexingStatus);
    if (action === 'unsupported') return [];

    return [{
      icon: iconForAction(action),
      labelKey: leafReindexLabelKey(action),
      disabled: false,
    }];
  }

  return getBulkReindexMenuOptions(node);
}

export function getReindexNodeForTableItem(
  item: { nodeType?: string; indexingStatus?: string | null; hasChildren?: boolean },
  isHubNode: boolean,
): ReindexNode {
  if (!isHubNode) return {};
  return getReindexNodeFromHubItem(item);
}

/** Loading toast title for a queued reindex/index job. */
export function getReindexLoadingTitle(
  node: ReindexNode,
  depth: number = FOLDER_REINDEX_DEPTH,
  statusFilters?: string[],
): string {
  return getReindexToastKind(node, depth, statusFilters) === 'indexing'
    ? 'Indexing...'
    : 'Re-indexing...';
}

/** Success-toast title for a queued reindex/index job. */
export function getReindexSuccessTitle(
  node: ReindexNode,
  depth: number = FOLDER_REINDEX_DEPTH,
  statusFilters?: string[],
): string {
  return getReindexToastKind(node, depth, statusFilters) === 'indexing'
    ? 'Successfully queued for indexing'
    : 'Successfully queued for reindexing';
}
