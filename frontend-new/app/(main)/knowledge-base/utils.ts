// Knowledge Base Utility Functions - Filter/Sort Conversion

import type {
  KnowledgeBaseFilter,
  SortConfig,
  AllRecordsFilter,
  AllRecordsSortConfig,
  AllRecordsPagination,
  KnowledgeHubQueryParams,
  SizeRange,
  DateFilterType,
} from './types';

/** Must match backend `MIN_SEARCH_QUERY_LENGTH` in `knowledge_hub_router.py`. */
export const KB_MIN_SEARCH_QUERY_LENGTH = 2;

/**
 * Size range buckets in bytes
 * All ranges include both gte and lte bounds to match API format: "gte:X,lte:Y"
 */
const SIZE_RANGES: Record<SizeRange, { gte: number; lte: number }> = {
  lt1mb: { gte: 0, lte: 1048576 },                           // 0 to 1 MB
  '1to10mb': { gte: 1048576, lte: 10485760 },                // 1 MB to 10 MB
  '10to100mb': { gte: 10485760, lte: 104857600 },            // 10 MB to 100 MB
};

/**
 * Converts size ranges array to API format
 *
 * Example: ['lt1mb', '10to100mb'] -> "gte:0,lte:1048576|gte:10485760,lte:104857600"
 * Multiple ranges are joined with "|" (OR logic)
 * Each range always includes both bounds in "gte:X,lte:Y" format
 *
 * @param sizeRanges Array of size range identifiers
 * @returns API-formatted size filter string or undefined
 */
export function convertSizeRangesToApiFormat(sizeRanges: SizeRange[]): string | undefined {
  if (!sizeRanges || sizeRanges.length === 0) {
    return undefined;
  }

  const rangeStrings = sizeRanges.map((range) => {
    const { gte, lte } = SIZE_RANGES[range];
    return `gte:${gte},lte:${lte}`;
  });

  return rangeStrings.join('|');
}

/**
 * Converts date range to API format (epoch milliseconds)
 *
 * Handles different date filter types:
 * - 'on': Exact date match (full day range from start to end of day)
 * - 'between': Date range (from start of first day to end of last day)
 * - 'before': Dates before the selected date (only lte bound)
 * - 'after': Dates after the selected date (only gte bound)
 *
 * Example:
 * - after: "2024-01-01", before: "2024-12-31", type: 'between'
 * - returns: "gte:1704067200000,lte:1735689599999"
 *
 * @param after ISO date string (inclusive lower bound)
 * @param before ISO date string (inclusive upper bound)
 * @param dateType Type of date filter (on, between, before, after)
 * @returns API-formatted date range string or undefined
 */
export function convertDateRangeToApiFormat(
  after?: string,
  before?: string,
  dateType?: DateFilterType
): string | undefined {
  if (!after && !before) {
    return undefined;
  }

  const parts: string[] = [];

  switch (dateType) {
    case 'on':
      // Exact date: full day range (start of day to end of day)
      if (after) {
        const startOfDay = new Date(after);
        startOfDay.setHours(0, 0, 0, 0);
        const endOfDay = new Date(after);
        endOfDay.setHours(23, 59, 59, 999);
        parts.push(`gte:${startOfDay.getTime()}`);
        parts.push(`lte:${endOfDay.getTime()}`);
      }
      break;

    case 'before':
      // Before date: only lte bound (end of selected day)
      if (before) {
        const endOfDay = new Date(before);
        endOfDay.setHours(23, 59, 59, 999);
        parts.push(`lte:${endOfDay.getTime()}`);
      }
      break;

    case 'after':
      // After date: only gte bound (start of selected day)
      if (after) {
        const startOfDay = new Date(after);
        startOfDay.setHours(0, 0, 0, 0);
        parts.push(`gte:${startOfDay.getTime()}`);
      }
      break;

    case 'between':
    default:
      // Range: gte start of first day, lte end of last day
      if (after) {
        const startOfDay = new Date(after);
        startOfDay.setHours(0, 0, 0, 0);
        parts.push(`gte:${startOfDay.getTime()}`);
      }
      if (before) {
        const endOfDay = new Date(before);
        endOfDay.setHours(23, 59, 59, 999);
        parts.push(`lte:${endOfDay.getTime()}`);
      }
      break;
  }

  return parts.length > 0 ? parts.join(',') : undefined;
}

/**
 * Builds API query parameters from Collections mode filter/sort state
 *
 * Converts UI filter state to KnowledgeHubQueryParams for API calls.
 * Handles array-to-CSV conversion and date/size range formatting.
 *
 * @param filter Collections mode filter state
 * @param sort Collections mode sort configuration
 * @param pagination Optional pagination config (defaults to page 1, limit 50)
 * @returns Complete query parameters object for API
 */
export function buildFilterParams(
  filter: KnowledgeBaseFilter,
  sort: SortConfig,
  pagination?: { page: number; limit: number }
): KnowledgeHubQueryParams {
  const params: KnowledgeHubQueryParams = {
    page: pagination?.page ?? 1,
    limit: pagination?.limit ?? 50,
    include: 'counts,permissions,breadcrumbs,availableFilters',
  };

  // Search query (omit `q` until long enough — backend returns 400 otherwise)
  const searchTrimmed = filter.searchQuery?.trim();
  if (searchTrimmed && searchTrimmed.length >= KB_MIN_SEARCH_QUERY_LENGTH) {
    params.q = searchTrimmed;
  }

  // Node types (CSV string)
  if (filter.nodeTypes?.length) {
    params.nodeTypes = filter.nodeTypes.join(',');
  }

  // Record types (CSV string)
  if (filter.recordTypes?.length) {
    params.recordTypes = filter.recordTypes.join(',');
  }

  // Indexing status (CSV string)
  if (filter.indexingStatus?.length) {
    params.indexingStatus = filter.indexingStatus.join(',');
  }

  // Origins (CSV string)
  if (filter.origins?.length) {
    params.origins = filter.origins.join(',');
  }

  // Connector IDs (CSV string)
  if (filter.connectorIds?.length) {
    params.connectorIds = filter.connectorIds.join(',');
  }

  // KB IDs (CSV string)
  if (filter.kbIds?.length) {
    params.kbIds = filter.kbIds.join(',');
  }

  // Size ranges
  if (filter.sizeRanges?.length) {
    params.size = convertSizeRangesToApiFormat(filter.sizeRanges);
  }

  // Date ranges (with date type support)
  if (filter.createdAfter || filter.createdBefore) {
    params.createdAt = convertDateRangeToApiFormat(
      filter.createdAfter,
      filter.createdBefore,
      filter.createdDateType
    );
  }

  if (filter.updatedAfter || filter.updatedBefore) {
    params.updatedAt = convertDateRangeToApiFormat(
      filter.updatedAfter,
      filter.updatedBefore,
      filter.updatedDateType
    );
  }

  // Sorting
  params.sortBy = sort.field;
  params.sortOrder = sort.order;

  return params;
}

/**
 * Builds API query parameters from All Records mode filter/sort state
 *
 * Similar to buildFilterParams but handles All Records specific filters
 * like collection IDs and connector IDs.
 *
 * @param filter All Records mode filter state
 * @param sort All Records mode sort configuration
 * @param pagination Pagination state with page and limit
 * @returns Complete query parameters object for API
 */
export function buildAllRecordsFilterParams(
  filter: AllRecordsFilter,
  sort: AllRecordsSortConfig,
  pagination: AllRecordsPagination
): KnowledgeHubQueryParams {
  const params: KnowledgeHubQueryParams = {
    page: pagination.page,
    limit: pagination.limit,
    include: 'counts,permissions,breadcrumbs,availableFilters',
  };

  const searchTrimmedAll = filter.searchQuery?.trim();
  if (searchTrimmedAll && searchTrimmedAll.length >= KB_MIN_SEARCH_QUERY_LENGTH) {
    params.q = searchTrimmedAll;
  }

  // Node types (CSV string)
  if (filter.nodeTypes?.length) {
    params.nodeTypes = filter.nodeTypes.join(',');
  }

  // Record types (CSV string)
  if (filter.recordTypes?.length) {
    params.recordTypes = filter.recordTypes.join(',');
  }

  // Indexing status (CSV string)
  if (filter.indexingStatus?.length) {
    params.indexingStatus = filter.indexingStatus.join(',');
  }

  // Origins (CSV string)
  if (filter.origins?.length) {
    params.origins = filter.origins.join(',');
  }

  // Collection IDs -> maps to kbIds parameter (CSV string)
  if (filter.collectionIds?.length) {
    params.kbIds = filter.collectionIds.join(',');
  }

  // Connector IDs (CSV string)
  if (filter.connectorIds?.length) {
    params.connectorIds = filter.connectorIds.join(',');
  }

  // Size ranges
  if (filter.sizeRanges?.length) {
    params.size = convertSizeRangesToApiFormat(filter.sizeRanges);
  }

  // Date ranges (with date type support)
  if (filter.createdAfter || filter.createdBefore) {
    params.createdAt = convertDateRangeToApiFormat(
      filter.createdAfter,
      filter.createdBefore,
      filter.createdDateType
    );
  }

  if (filter.updatedAfter || filter.updatedBefore) {
    params.updatedAt = convertDateRangeToApiFormat(
      filter.updatedAfter,
      filter.updatedBefore,
      filter.updatedDateType
    );
  }

  // Sorting
  params.sortBy = sort.field;
  params.sortOrder = sort.order;

  return params;
}
