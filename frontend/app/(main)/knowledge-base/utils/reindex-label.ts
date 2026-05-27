// Status-aware label / disable / toast wording for the row-level reindex action.
//
// Bulk menu: folders, recordGroups, and parent records with hasChildren === true.
//   - AUTO_INDEX_OFF on current node → Start indexing + Reindex failed (2 options)
//   - COMPLETED with children → Reindex all + Reindex failed + Start indexing (AUTO_INDEX_OFF filter)
//   - NOT_STARTED / null → Start indexing + Reindex failed (no duplicate Start indexing)
//   - COMPLETED / other bulk → Reindex all + Reindex failed + Start indexing (AUTO_INDEX_OFF filter)
//
// Single menu: leaf records in table/list/grid (no children).
//   - Force reindexing only when COMPLETED / IN_PROGRESS (no children)
//
// No menu: app nodes, empty containers, leaf records in sidebar.

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

/** Primary bulk action for toasts (not used for menu labels). */
function bulkPrimaryAction(node: ReindexNode): ReindexAction {
  const status = node.indexingStatus;
  if (status === 'AUTO_INDEX_OFF' || status === 'NOT_STARTED' || status == null) {
    return 'start-indexing';
  }
  if (node.hasChildren === true) {
    return 'reindex';
  }
  return leafActionFromStatus(status);
}

/** Label key for bulk menu option 1 (all descendants). */
function bulkPrimaryLabelKey(node: ReindexNode): ReindexMenuLabelKey {
  const status = node.indexingStatus;
  if (status === 'NOT_STARTED' || status == null) {
    return 'menu.startIndexing';
  }
  if (status === 'COMPLETED' && node.hasChildren === true) {
    return 'menu.reindexAll';
  }
  if (status === 'AUTO_INDEX_OFF') {
    return 'menu.startIndexing';
  }
  return 'menu.reindexAll';
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

function getBulkReindexMenuOptions(node: ReindexNode): ReindexMenuOption[] {
  if (node.indexingStatus === 'AUTO_INDEX_OFF') {
    return [
      { icon: 'refresh', labelKey: 'menu.startIndexing' },
      REINDEX_FAILED_OPTION,
    ];
  }

  const primaryKey = bulkPrimaryLabelKey(node);
  const options: ReindexMenuOption[] = [
    { icon: 'refresh', labelKey: primaryKey },
    REINDEX_FAILED_OPTION,
  ];

  // Third option only when primary is Reindex all — avoids two "Start indexing" rows.
  if (primaryKey !== 'menu.startIndexing') {
    options.push(START_INDEXING_AUTO_INDEX_OFF_OPTION);
  }

  return options;
}

/** Determine the reindex action for toasts / legacy guards. */
export function getReindexAction(node: ReindexNode): ReindexAction {
  if (supportsBulkReindex(node)) {
    return bulkPrimaryAction(node);
  }
  return leafActionFromStatus(node.indexingStatus);
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
