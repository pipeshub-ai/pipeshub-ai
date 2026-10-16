'use client';

import { create } from 'zustand';
import type {
  ArtifactListItem,
  ArtifactsOwnership,
  ArtifactsOwnershipCounts,
  ArtifactsSortField,
  ArtifactsViewMode,
} from './types';

export const DEFAULT_PAGE_SIZE = 50;

interface ArtifactsState {
  items: ArtifactListItem[];
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  search: string;
  artifactTypes: string[];
  dateFrom?: number;
  dateTo?: number;
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  viewMode: ArtifactsViewMode;
  ownership: ArtifactsOwnership;
  counts: ArtifactsOwnershipCounts;
  isLoading: boolean;
  error: string | null;
  setItems: (
    items: ArtifactListItem[],
    pagination: { page: number; limit: number; totalCount: number; totalPages: number },
    counts?: ArtifactsOwnershipCounts,
  ) => void;
  setSearch: (search: string) => void;
  setArtifactTypes: (artifactTypes: string[]) => void;
  setDateRange: (dateFrom?: number, dateTo?: number) => void;
  setSort: (sortBy: ArtifactsSortField, sortOrder: 'asc' | 'desc') => void;
  setPage: (page: number) => void;
  setViewMode: (viewMode: ArtifactsViewMode) => void;
  setOwnership: (ownership: ArtifactsOwnership) => void;
  setLoading: (isLoading: boolean) => void;
  setError: (error: string | null) => void;
  hydrateFromUrl: (patch: Partial<ArtifactsState>) => void;
}

export const useArtifactsStore = create<ArtifactsState>((set) => ({
  items: [],
  page: 1,
  limit: DEFAULT_PAGE_SIZE,
  totalCount: 0,
  totalPages: 0,
  search: '',
  artifactTypes: [],
  dateFrom: undefined,
  dateTo: undefined,
  sortBy: 'createdAtTimestamp',
  sortOrder: 'desc',
  viewMode: 'list',
  ownership: 'all',
  counts: { all: 0, mine: 0, shared: 0 },
  isLoading: false,
  error: null,
  setItems: (items, pagination, counts) =>
    set((state) => ({
      items,
      page: pagination.page,
      limit: pagination.limit,
      totalCount: pagination.totalCount,
      totalPages: pagination.totalPages,
      counts: counts ?? state.counts,
    })),
  setSearch: (search) => set({ search, page: 1 }),
  setArtifactTypes: (artifactTypes) => set({ artifactTypes, page: 1 }),
  setDateRange: (dateFrom, dateTo) => set({ dateFrom, dateTo, page: 1 }),
  setSort: (sortBy, sortOrder) => set({ sortBy, sortOrder, page: 1 }),
  setPage: (page) => set({ page }),
  setViewMode: (viewMode) => set({ viewMode }),
  setOwnership: (ownership) => set({ ownership, page: 1 }),
  setLoading: (isLoading) => set({ isLoading }),
  setError: (error) => set({ error }),
  hydrateFromUrl: (patch) => set(patch),
}));
