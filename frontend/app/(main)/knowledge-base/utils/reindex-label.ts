// Status-aware label / disable / toast wording for the row-level reindex action.
//
// Bulk menu (3 options): folders, recordGroups, KB roots, and connector parent
// records with hasChildren === true.
//
// Single menu option: leaf records in table/list/grid (nodeType record, hasChildren !== true).
//
// No menu: app nodes, empty containers, leaf records in sidebar.
//
// Toast wording distinguishes:
//   - "indexing"   — first attempt (NOT_STARTED, AUTO_INDEX_OFF)
//   - "reindexing" — any subsequent attempt (COMPLETED, FAILED, IN_PROGRESS, etc.)

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
  | 'menu.startIndexing'
  | 'menu.reindexAll'
  | 'menu.reindexFailed'
  | 'menu.reindexManual'
  | 'menu.startManualIndex'
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
  'menu.startIndexing': 'Start indexing',
  'menu.reindexAll': 'Reindex all',
  'menu.reindexFailed': 'Reindex failed',
  'menu.reindexManual': 'Reindex manual index',
  'menu.startManualIndex': 'Start manual index',
  'menu.forceReindexing': 'Force reindexing',
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

/** Whether this node supports the 3-option bulk reindex menu. */
export function supportsBulkReindex(node: ReindexNode): boolean {
  if (node.nodeType === 'app' || node.hasChildren !== true) return false;
  return (
    node.nodeType === 'folder'
    || node.nodeType === 'recordGroup'
    || node.nodeType === 'kb'
    || node.nodeType === 'record'
  );
}

function isBulkReindexContainer(node: ReindexNode): boolean {
  return (
    node.nodeType === 'folder'
    || node.nodeType === 'recordGroup'
    || node.nodeType === 'kb'
    || (node.nodeType === 'record' && node.hasChildren === true)
  );
}

function actionFromIndexingStatus(indexingStatus?: string | null): ReindexAction {
  switch (indexingStatus) {
    case 'COMPLETED':
    case 'IN_PROGRESS':
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
    default:
      return 'reindex';
  }
}

/** Determine the reindex action this node should trigger. */
export function getReindexAction(node: ReindexNode): ReindexAction {
  if (isBulkReindexContainer(node)) {
    // Containers without their own status default to first-time bulk labels.
    if (node.indexingStatus != null) {
      return actionFromIndexingStatus(node.indexingStatus);
    }
    return 'start-indexing';
  }

  return actionFromIndexingStatus(node.indexingStatus);
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
    case 'unsupported':
      return 'File not supported';
    case 'reindex':
    default:
      return 'Reindex';
  }
}

/** Material icon name for the reindex menu item. */
export function getReindexIcon(node: ReindexNode): string {
  return getReindexAction(node) === 'force-reindex' ? 'redo' : 'refresh';
}

/** Whether all reindex menu items should be disabled (legacy row-level guard). */
export function isReindexDisabled(node: ReindexNode): boolean {
  return getReindexAction(node) === 'unsupported';
}

/** Whether reindex menu items should be shown (connector app nodes and empty containers excluded). */
export function canShowReindexMenu(node: ReindexNode): boolean {
  if (node.nodeType === 'app') return false;
  const isEmptyContainer =
    (node.nodeType === 'folder' || node.nodeType === 'recordGroup' || node.nodeType === 'kb')
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
    const action = getReindexAction(node);
    if (action === 'unsupported') return [];

    return [{
      icon: getReindexIcon(node),
      labelKey: leafReindexLabelKey(action),
      disabled: false,
    }];
  }

  const action = getReindexAction(node);
  const useStartLabels = action === 'start-indexing';

  return [
    {
      icon: 'refresh',
      labelKey: useStartLabels
        ? 'menu.startIndexing'
        : action === 'force-reindex'
          ? 'menu.forceReindexing'
          : 'menu.reindexAll',
    },
    {
      icon: 'error_outline',
      labelKey: 'menu.reindexFailed',
      statusFilters: ['FAILED'],
    },
    {
      icon: 'pause_circle_outline',
      labelKey: 'menu.startManualIndex',
      statusFilters: ['AUTO_INDEX_OFF'],
    },
  ];
}

export function getReindexNodeForTableItem(
  item: { nodeType?: string; indexingStatus?: string | null; hasChildren?: boolean },
  isHubNode: boolean,
): ReindexNode {
  if (!isHubNode) return {};
  return getReindexNodeFromHubItem(item);
}

/** Loading toast title for a queued reindex/index job. */
export function getReindexLoadingTitle(node: ReindexNode): string {
  return getReindexAction(node) === 'start-indexing' ? 'Indexing...' : 'Re-indexing...';
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
    case 'reindex':
    default:
      return 'Successfully queued for reindexing';
  }
}
