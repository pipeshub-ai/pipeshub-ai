import type { ArtifactsSortField, ArtifactsViewMode } from './types';

export interface ArtifactsUrlState {
  search: string;
  artifactTypes: string[];
  dateFrom?: number;
  dateTo?: number;
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  page: number;
  viewMode: ArtifactsViewMode;
}

const DEFAULT_STATE: ArtifactsUrlState = {
  search: '',
  artifactTypes: [],
  sortBy: 'createdAtTimestamp',
  sortOrder: 'desc',
  page: 1,
  viewMode: 'list',
};

function parsePositiveInt(value: string | null, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function parseArtifactsParams(searchParams: URLSearchParams): ArtifactsUrlState {
  const types = (searchParams.get('types') || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  const dateFromRaw = searchParams.get('from');
  const dateToRaw = searchParams.get('to');
  const sortBy = searchParams.get('sortBy');
  const sortOrder = searchParams.get('sortOrder');
  const view = searchParams.get('view');
  return {
    search: searchParams.get('q') || '',
    artifactTypes: types,
    dateFrom: dateFromRaw ? Number(dateFromRaw) : undefined,
    dateTo: dateToRaw ? Number(dateToRaw) : undefined,
    sortBy:
      sortBy === 'name' ||
      sortBy === 'createdAtTimestamp' ||
      sortBy === 'updatedAtTimestamp' ||
      sortBy === 'artifactType'
        ? sortBy
        : DEFAULT_STATE.sortBy,
    sortOrder: sortOrder === 'asc' ? 'asc' : 'desc',
    page: parsePositiveInt(searchParams.get('page'), 1),
    viewMode: view === 'grid' ? 'grid' : 'list',
  };
}

export function serializeArtifactsParams(state: ArtifactsUrlState): string {
  const params = new URLSearchParams();
  if (state.search) params.set('q', state.search);
  if (state.artifactTypes.length) params.set('types', state.artifactTypes.join(','));
  if (state.dateFrom != null) params.set('from', String(state.dateFrom));
  if (state.dateTo != null) params.set('to', String(state.dateTo));
  if (state.sortBy !== DEFAULT_STATE.sortBy) params.set('sortBy', state.sortBy);
  if (state.sortOrder !== DEFAULT_STATE.sortOrder) params.set('sortOrder', state.sortOrder);
  if (state.page > 1) params.set('page', String(state.page));
  if (state.viewMode !== DEFAULT_STATE.viewMode) params.set('view', state.viewMode);
  return params.toString();
}
