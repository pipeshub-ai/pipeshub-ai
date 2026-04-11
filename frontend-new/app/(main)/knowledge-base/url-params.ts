/**
 * URL parameter serialization/deserialization for knowledge-base page filters.
 *
 * Persists filter, sort, pagination, and search state in URL query params
 * so that links can be shared and reopened with the same view state.
 */

import type {
  KnowledgeBaseFilter,
  SortConfig,
  SortField,
  AllRecordsFilter,
  AllRecordsSortConfig,
  AllRecordsSortField,
  RecordType,
  IndexingStatus,
  SizeRange,
  DateFilterType,
  NodeOrigin,
} from './types';
import { useKnowledgeBaseStore } from './store';

// URL param keys
const PARAM = {
  RECORD_TYPES: 'recordTypes',
  INDEXING_STATUS: 'indexingStatus',
  SIZE_RANGES: 'sizeRanges',
  ORIGINS: 'origins',
  CREATED_AFTER: 'createdAfter',
  CREATED_BEFORE: 'createdBefore',
  CREATED_DATE_TYPE: 'createdDateType',
  UPDATED_AFTER: 'updatedAfter',
  UPDATED_BEFORE: 'updatedBefore',
  UPDATED_DATE_TYPE: 'updatedDateType',
  SORT_FIELD: 'sortField',
  SORT_ORDER: 'sortOrder',
  PAGE: 'page',
  LIMIT: 'limit',
  SEARCH: 'search',
} as const;

// Defaults — values matching these are omitted from the URL
const DEFAULTS = {
  SORT_FIELD: 'updatedAt' as const,
  SORT_ORDER: 'desc' as const,
  PAGE: 1,
  LIMIT: 50,
};

// Valid enum values for parsing
const VALID_RECORD_TYPES = new Set<string>(['FILE', 'WEBPAGE', 'MESSAGE', 'EMAIL', 'TICKET']);
const VALID_INDEXING_STATUS = new Set<string>(['COMPLETED', 'IN_PROGRESS', 'FAILED', 'FILE_TYPE_NOT_SUPPORTED']);
const VALID_SIZE_RANGES = new Set<string>(['lt1mb', '1to10mb', '10to100mb']);
const VALID_ORIGINS = new Set<string>(['KB', 'CONNECTOR']);
const VALID_DATE_TYPES = new Set<string>(['on', 'between', 'before', 'after']);
const VALID_SORT_FIELDS = new Set<string>(['name', 'createdAt', 'updatedAt', 'size', 'nodeType']);
const VALID_ALL_RECORDS_SORT_FIELDS = new Set<string>(['name', 'size', 'createdAt', 'updatedAt', 'indexingStatus', 'origin']);

// ============================================================================
// Helpers
// ============================================================================

/** Split comma-separated string and filter by valid values */
function parseCommaSeparated<T extends string>(value: string | null, validSet: Set<string>): T[] | undefined {
  if (!value) return undefined;
  const items = value.split(',').filter((v) => validSet.has(v)) as T[];
  return items.length > 0 ? items : undefined;
}

/** Join array to comma-separated string, or return undefined if empty */
function joinArray(arr: string[] | undefined): string | undefined {
  if (!arr || arr.length === 0) return undefined;
  return arr.join(',');
}

// ============================================================================
// Serialization (Store → URL params)
// ============================================================================

function serializeCommonFilterParams(
  filter: KnowledgeBaseFilter | AllRecordsFilter,
  params: Record<string, string>
): void {
  const rt = joinArray(filter.recordTypes);
  if (rt) params[PARAM.RECORD_TYPES] = rt;

  const is = joinArray(filter.indexingStatus);
  if (is) params[PARAM.INDEXING_STATUS] = is;

  const sz = joinArray(filter.sizeRanges);
  if (sz) params[PARAM.SIZE_RANGES] = sz;

  if (filter.createdAfter) params[PARAM.CREATED_AFTER] = filter.createdAfter;
  if (filter.createdBefore) params[PARAM.CREATED_BEFORE] = filter.createdBefore;
  if (filter.createdDateType) params[PARAM.CREATED_DATE_TYPE] = filter.createdDateType;

  if (filter.updatedAfter) params[PARAM.UPDATED_AFTER] = filter.updatedAfter;
  if (filter.updatedBefore) params[PARAM.UPDATED_BEFORE] = filter.updatedBefore;
  if (filter.updatedDateType) params[PARAM.UPDATED_DATE_TYPE] = filter.updatedDateType;
}

function serializeSortParams(
  field: string,
  order: string,
  params: Record<string, string>
): void {
  if (field !== DEFAULTS.SORT_FIELD) params[PARAM.SORT_FIELD] = field;
  if (order !== DEFAULTS.SORT_ORDER) params[PARAM.SORT_ORDER] = order;
}

function serializePaginationParams(
  page: number,
  limit: number,
  params: Record<string, string>
): void {
  if (page !== DEFAULTS.PAGE) params[PARAM.PAGE] = String(page);
  if (limit !== DEFAULTS.LIMIT) params[PARAM.LIMIT] = String(limit);
}

/** Serialize collections mode filter/sort/pagination/search to URL param entries */
export function serializeCollectionsParams(
  filter: KnowledgeBaseFilter,
  sort: SortConfig,
  pagination: { page: number; limit: number },
  searchQuery: string
): Record<string, string> {
  const params: Record<string, string> = {};
  serializeCommonFilterParams(filter, params);
  serializeSortParams(sort.field, sort.order, params);
  serializePaginationParams(pagination.page, pagination.limit, params);
  if (searchQuery) params[PARAM.SEARCH] = searchQuery;
  return params;
}

/** Serialize all-records mode filter/sort/pagination/search to URL param entries */
export function serializeAllRecordsParams(
  filter: AllRecordsFilter,
  sort: AllRecordsSortConfig,
  pagination: { page: number; limit: number },
  searchQuery: string
): Record<string, string> {
  const params: Record<string, string> = {};
  serializeCommonFilterParams(filter, params);

  // Origins is specific to all-records mode
  const ori = joinArray(filter.origins);
  if (ori) params[PARAM.ORIGINS] = ori;

  serializeSortParams(sort.field, sort.order, params);
  serializePaginationParams(pagination.page, pagination.limit, params);
  if (searchQuery) params[PARAM.SEARCH] = searchQuery;
  return params;
}

// ============================================================================
// Deserialization (URL params → Store state)
// ============================================================================

function parseCommonFilter(searchParams: URLSearchParams): {
  recordTypes?: RecordType[];
  indexingStatus?: IndexingStatus[];
  sizeRanges?: SizeRange[];
  createdAfter?: string;
  createdBefore?: string;
  createdDateType?: DateFilterType;
  updatedAfter?: string;
  updatedBefore?: string;
  updatedDateType?: DateFilterType;
} {
  const result: Record<string, unknown> = {};

  const recordTypes = parseCommaSeparated<RecordType>(searchParams.get(PARAM.RECORD_TYPES), VALID_RECORD_TYPES);
  if (recordTypes) result.recordTypes = recordTypes;

  const indexingStatus = parseCommaSeparated<IndexingStatus>(searchParams.get(PARAM.INDEXING_STATUS), VALID_INDEXING_STATUS);
  if (indexingStatus) result.indexingStatus = indexingStatus;

  const sizeRanges = parseCommaSeparated<SizeRange>(searchParams.get(PARAM.SIZE_RANGES), VALID_SIZE_RANGES);
  if (sizeRanges) result.sizeRanges = sizeRanges;

  const ca = searchParams.get(PARAM.CREATED_AFTER);
  if (ca) result.createdAfter = ca;
  const cb = searchParams.get(PARAM.CREATED_BEFORE);
  if (cb) result.createdBefore = cb;
  const cdt = searchParams.get(PARAM.CREATED_DATE_TYPE);
  if (cdt && VALID_DATE_TYPES.has(cdt)) result.createdDateType = cdt as DateFilterType;

  const ua = searchParams.get(PARAM.UPDATED_AFTER);
  if (ua) result.updatedAfter = ua;
  const ub = searchParams.get(PARAM.UPDATED_BEFORE);
  if (ub) result.updatedBefore = ub;
  const udt = searchParams.get(PARAM.UPDATED_DATE_TYPE);
  if (udt && VALID_DATE_TYPES.has(udt)) result.updatedDateType = udt as DateFilterType;

  return result;
}

/** Parse URL params into collections mode state */
export function parseCollectionsParams(searchParams: URLSearchParams): {
  filter: KnowledgeBaseFilter;
  sort: SortConfig;
  page: number;
  limit: number;
  searchQuery: string;
} {
  const filter = parseCommonFilter(searchParams) as KnowledgeBaseFilter;

  const sfRaw = searchParams.get(PARAM.SORT_FIELD);
  const sf = sfRaw && VALID_SORT_FIELDS.has(sfRaw) ? (sfRaw as SortField) : DEFAULTS.SORT_FIELD;
  const soRaw = searchParams.get(PARAM.SORT_ORDER);
  const so = soRaw === 'asc' || soRaw === 'desc' ? soRaw : DEFAULTS.SORT_ORDER;

  const pgRaw = searchParams.get(PARAM.PAGE);
  const page = pgRaw ? Math.max(1, parseInt(pgRaw, 10) || 1) : DEFAULTS.PAGE;

  const lmRaw = searchParams.get(PARAM.LIMIT);
  const limit = lmRaw ? Math.max(1, Math.min(200, parseInt(lmRaw, 10) || DEFAULTS.LIMIT)) : DEFAULTS.LIMIT;

  const searchQuery = searchParams.get(PARAM.SEARCH) || '';

  return {
    filter,
    sort: { field: sf, order: so },
    page,
    limit,
    searchQuery,
  };
}

/** Parse URL params into all-records mode state */
export function parseAllRecordsParams(searchParams: URLSearchParams): {
  filter: AllRecordsFilter;
  sort: AllRecordsSortConfig;
  page: number;
  limit: number;
  searchQuery: string;
} {
  const commonFilter = parseCommonFilter(searchParams);
  const filter: AllRecordsFilter = { ...commonFilter };

  const origins = parseCommaSeparated<NodeOrigin>(searchParams.get(PARAM.ORIGINS), VALID_ORIGINS);
  if (origins) filter.origins = origins;

  const sfRaw = searchParams.get(PARAM.SORT_FIELD);
  const sf = sfRaw && VALID_ALL_RECORDS_SORT_FIELDS.has(sfRaw) ? (sfRaw as AllRecordsSortField) : DEFAULTS.SORT_FIELD;
  const soRaw = searchParams.get(PARAM.SORT_ORDER);
  const so = soRaw === 'asc' || soRaw === 'desc' ? soRaw : DEFAULTS.SORT_ORDER;

  const pgRaw = searchParams.get(PARAM.PAGE);
  const page = pgRaw ? Math.max(1, parseInt(pgRaw, 10) || 1) : DEFAULTS.PAGE;

  const lmRaw = searchParams.get(PARAM.LIMIT);
  const limit = lmRaw ? Math.max(1, Math.min(200, parseInt(lmRaw, 10) || DEFAULTS.LIMIT)) : DEFAULTS.LIMIT;

  const searchQuery = searchParams.get(PARAM.SEARCH) || '';

  return {
    filter,
    sort: { field: sf, order: so },
    page,
    limit,
    searchQuery,
  };
}

// ============================================================================
// URL Building
// ============================================================================

/** Build a full knowledge-base URL from navigation params + filter params */
export function buildFilterUrl(
  baseParams: Record<string, string>,
  filterParams: Record<string, string>
): string {
  const urlParams = new URLSearchParams();

  // Add base/navigation params first (view, nodeType, nodeId, etc.)
  Object.entries(baseParams).forEach(([key, value]) => {
    if (value) urlParams.set(key, value);
  });

  // Add filter params
  Object.entries(filterParams).forEach(([key, value]) => {
    if (value) urlParams.set(key, value);
  });

  const queryString = urlParams.toString();
  return queryString ? `/knowledge-base?${queryString}` : '/knowledge-base';
}

/**
 * Build a navigation URL that preserves view mode and carries forward
 * current filter/sort/pagination params from the store.
 *
 * @param params - Navigation params (nodeType, nodeId, kbId, etc.)
 * @param isAllRecordsMode - Whether the page is in all-records mode
 * @param debouncedSearchQuery - Debounced collections search query
 * @param debouncedAllRecordsSearchQuery - Debounced all-records search query
 */
export function buildNavUrl(
  params: Record<string, string>,
  isAllRecordsMode: boolean,
  debouncedSearchQuery: string,
  debouncedAllRecordsSearchQuery: string
): string {
  const baseParams: Record<string, string> = {};
  if (isAllRecordsMode) baseParams.view = 'all-records';
  Object.entries(params).forEach(([key, value]) => {
    if (value) baseParams[key] = value;
  });

  const store = useKnowledgeBaseStore.getState();
  const filterParams = isAllRecordsMode
    ? serializeAllRecordsParams(
        store.allRecordsFilter,
        store.allRecordsSort,
        { page: store.allRecordsPagination.page, limit: store.allRecordsPagination.limit },
        debouncedAllRecordsSearchQuery
      )
    : serializeCollectionsParams(
        store.filter,
        store.sort,
        { page: store.collectionsPagination.page, limit: store.collectionsPagination.limit },
        debouncedSearchQuery
      );

  return buildFilterUrl(baseParams, filterParams);
}

/** Check if a filter object has any active filters */
export function hasFilterParams(filter: KnowledgeBaseFilter | AllRecordsFilter): boolean {
  return Object.keys(filter).length > 0;
}
