// All Records Transformation Utilities

import type {
  KnowledgeHubNode,
  ConnectorType,
  AllRecordsSidebarSelection,
  AllRecordsFilter,
  AllRecordsSortConfig,
  AllRecordsPagination,
  KnowledgeHubQueryParams,
  AppNodeGroup,
} from '../types';
import { resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { KB_MIN_SEARCH_QUERY_LENGTH } from '../utils';

/**
 * Connector group for sidebar display
 */
export interface ConnectorGroup {
  type: ConnectorType;
  name: string;
  icon: string;
  items: KnowledgeHubNode[];
}

/**
 * Map API connector string to ConnectorType (delegates to centralized resolver)
 */
export function mapConnectorType(connectorString: string): ConnectorType {
  return resolveConnectorType(connectorString);
}

/** Hub app for org Collections (KB) — `connector` / `subType` casing may vary from the API. */
export function isKbCollectionsHubApp(node: { connector?: string; subType?: string }): boolean {
  const c = (node.connector ?? '').toString().trim().toUpperCase();
  if (c === 'KB') return true;
  return (node.subType ?? '').toString().trim().toUpperCase() === 'KB';
}

/**
 * Get display name for connector
 */
export function getConnectorDisplayName(connectorString: string): string {
  return connectorString;
}

/**
 * Group app nodes by connector type for sidebar display
 */
export function groupNodesByConnector(nodes: KnowledgeHubNode[]): ConnectorGroup[] {
  const connectorMap = new Map<string, KnowledgeHubNode[]>();

  // Group nodes by their connector field
  nodes.forEach((node) => {
    if (node.nodeType === 'app' && node.connector) {
      if (!connectorMap.has(node.connector)) {
        connectorMap.set(node.connector, []);
      }
      connectorMap.get(node.connector)!.push(node);
    }
  });

  // Transform to ConnectorGroup array
  return Array.from(connectorMap.entries()).map(([connectorString, items]) => ({
    type: mapConnectorType(connectorString),
    name: getConnectorDisplayName(connectorString),
    icon: mapConnectorType(connectorString),
    items,
  }));
}

/**
 * Group app nodes by connector for sidebar display (returns AppNodeGroup[])
 * Used in All Records mode sidebar to display connected apps grouped by connector
 */
export function groupAppNodesByConnector(nodes: KnowledgeHubNode[]): AppNodeGroup[] {
  const connectorMap = new Map<string, KnowledgeHubNode[]>();

  // Group nodes by their connector field
  nodes.forEach((node) => {
    if (node.nodeType === 'app' && node.connector) {
      if (!connectorMap.has(node.connector)) {
        connectorMap.set(node.connector, []);
      }
      connectorMap.get(node.connector)!.push(node);
    }
  });

  // Transform to AppNodeGroup array
  return Array.from(connectorMap.entries()).map(([connectorName, apps]) => ({
    id: connectorName.toLowerCase().replace(/\s+/g, '-'),
    connectorName,
    displayName: getConnectorDisplayName(connectorName),
    type: mapConnectorType(connectorName),
    apps,
  }));
}

/**
 * Get source display info from node
 */
export function getSourceDisplay(
  node: KnowledgeHubNode,
  kbLookup: Map<string, string>
): { sourceName: string; sourceType: 'collection' | ConnectorType; sourceIcon: string } {
  if (node.origin === 'COLLECTION' || isKbCollectionsHubApp(node)) {
    // For KB items, extract KB name from webUrl or use lookup
    const kbId = extractKbIdFromNode(node);
    const kbName = kbId ? kbLookup.get(kbId) || 'Collection' : 'Collection';

    return {
      sourceName: kbName,
      sourceType: 'collection',
      sourceIcon: 'folder',
    };
  }

  // For connector items, use the connector field
  const connectorType = node.connector ? mapConnectorType(node.connector) : 'google-drive';
  const sourceName = node.connector || 'Connector';

  return {
    sourceName,
    sourceType: connectorType,
    sourceIcon: connectorType,
  };
}

/**
 * Extract KB ID from node webUrl
 * webUrl format: "/kb/{kbId}" or "/kb/{kbId}/folder/{folderId}"
 */
function extractKbIdFromNode(node: KnowledgeHubNode): string | null {
  if (!node.webUrl) return null;

  const match = node.webUrl.match(/\/kb\/([^/]+)/);
  return match ? match[1] : null;
}

/**
 * Build query params from filter state and sidebar selection
 */
export function buildAllRecordsQueryParams(
  sidebarSelection: AllRecordsSidebarSelection,
  filter: AllRecordsFilter,
  sort: AllRecordsSortConfig,
  pagination: AllRecordsPagination,
  searchQuery?: string
): KnowledgeHubQueryParams {
  const params: KnowledgeHubQueryParams = {
    page: pagination.page,
    limit: pagination.limit,
    sortBy: sort.field, // Note: 'source' is not a valid sort field (use 'origin' or 'name')
    sortOrder: sort.order,
  };

  const sq = searchQuery?.trim();
  if (sq && sq.length >= KB_MIN_SEARCH_QUERY_LENGTH) {
    params.q = sq;
  }

  // Sidebar selection drives primary filtering
  if (sidebarSelection.type === 'collection') {
    params.kbIds = sidebarSelection.id;
  } else if (sidebarSelection.type === 'connector') {
    if (sidebarSelection.itemId) {
      // Specific connector item selected - filter by connector ID
      params.connectorIds = sidebarSelection.itemId;
    }
    // Note: If only connector type is selected, we'll handle this via navigation stack
  }

  // Apply filter bar filters
  if (filter.nodeTypes && filter.nodeTypes.length > 0) {
    params.nodeTypes = filter.nodeTypes.join(',');
  }

  if (filter.recordTypes && filter.recordTypes.length > 0) {
    params.recordTypes = filter.recordTypes.join(',');
  }

  if (filter.indexingStatus && filter.indexingStatus.length > 0) {
    params.indexingStatus = filter.indexingStatus.join(',');
  }

  if (filter.connectorIds && filter.connectorIds.length > 0) {
    params.connectorIds = filter.connectorIds.join(',');
  }

  // Note: Size and date filters will be applied client-side for now
  // as the API may not support these filters yet

  return params;
}

/**
 * Build KB lookup map from flat collections
 */
export function buildKbLookup(flatCollections: Array<{ id: string; name: string; nodeType: string }>): Map<string, string> {
  const lookup = new Map<string, string>();

  flatCollections.forEach((node) => {
    if (node.nodeType === 'kb') {
      lookup.set(node.id, node.name);
    }
  });

  return lookup;
}

/**
 * Filter items by size range (client-side)
 */
export function filterBySize(items: KnowledgeHubNode[], sizeRanges?: string[]): KnowledgeHubNode[] {
  if (!sizeRanges || sizeRanges.length === 0) return items;

  return items.filter((item) => {
    if (item.sizeInBytes === null || item.sizeInBytes === undefined) return false;

    const sizeInBytes = item.sizeInBytes;

    return sizeRanges.some((range) => {
      switch (range) {
        case 'lt1mb':
          return sizeInBytes < 1024 * 1024;
        case '1to10mb':
          return sizeInBytes >= 1024 * 1024 && sizeInBytes < 10 * 1024 * 1024;
        case '10to100mb':
          return sizeInBytes >= 10 * 1024 * 1024 && sizeInBytes < 100 * 1024 * 1024;
        default:
          return false;
      }
    });
  });
}

/**
 * Apply client-side filters (size only — date filters are handled server-side)
 */
export function applyClientSideFilters(
  items: KnowledgeHubNode[],
  filter: AllRecordsFilter
): KnowledgeHubNode[] {
  let filtered = items;

  // Size filter
  if (filter.sizeRanges && filter.sizeRanges.length > 0) {
    filtered = filterBySize(filtered, filter.sizeRanges);
  }

  return filtered;
}
