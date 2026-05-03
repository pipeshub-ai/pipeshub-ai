'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { enableMapSet } from 'immer';
import type { Group, GroupsFilter, GroupsSort } from './types';

enableMapSet();

// ========================================
// State
// ========================================

interface GroupsState {
  /** Group list data */
  groups: Group[];
  /** Selected group IDs */
  selectedGroups: Set<string>;

  /** Pagination */
  page: number;
  limit: number;
  totalCount: number;

  /** Search */
  searchQuery: string;

  /** Filters */
  filters: GroupsFilter;

  /** Sort */
  sort: GroupsSort;

  /** Loading */
  isLoading: boolean;

  /** Error message */
  error: string | null;

  // ── Create group panel ──
  isCreatePanelOpen: boolean;
  createGroupName: string;
  createGroupDescription: string;
  createGroupUserIds: string[];
  isCreating: boolean;

  // ── Detail / Edit group panel ──
  isDetailPanelOpen: boolean;
  detailGroup: Group | null;
  isEditMode: boolean;
  editGroupName: string;
  editGroupDescription: string;
  editAddUserIds: string[];
  isSavingEdit: boolean;
}

// ========================================
// Actions
// ========================================

interface GroupsActions {
  setGroups: (groups: Group[], totalCount?: number) => void;
  setSelectedGroups: (ids: Set<string>) => void;
  toggleSelectGroup: (id: string) => void;
  setPage: (page: number) => void;
  setLimit: (limit: number) => void;
  setSearchQuery: (query: string) => void;
  setFilters: (filters: Partial<GroupsFilter>) => void;
  clearFilters: () => void;
  setSort: (sort: GroupsSort) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;

  // ── Create group panel actions ──
  openCreatePanel: () => void;
  closeCreatePanel: () => void;
  setCreateGroupName: (name: string) => void;
  setCreateGroupDescription: (desc: string) => void;
  setCreateGroupUserIds: (ids: string[]) => void;
  setIsCreating: (loading: boolean) => void;
  resetCreateForm: () => void;

  // ── Detail / Edit group panel actions ──
  openDetailPanel: (group: Group) => void;
  closeDetailPanel: () => void;
  setDetailGroup: (group: Group | null) => void;
  enterEditMode: () => void;
  exitEditMode: () => void;
  setEditGroupName: (name: string) => void;
  setEditGroupDescription: (desc: string) => void;
  setEditAddUserIds: (ids: string[]) => void;
  setIsSavingEdit: (loading: boolean) => void;
  resetEditForm: () => void;
}

type GroupsStore = GroupsState & GroupsActions;

// ========================================
// Initial state
// ========================================

const initialCreateState = {
  isCreatePanelOpen: false,
  createGroupName: '',
  createGroupDescription: '',
  createGroupUserIds: [] as string[],
  isCreating: false,
};

const initialDetailState = {
  isDetailPanelOpen: false,
  detailGroup: null as Group | null,
  isEditMode: false,
  editGroupName: '',
  editGroupDescription: '',
  editAddUserIds: [] as string[],
  isSavingEdit: false,
};

const initialState: GroupsState = {
  groups: [],
  selectedGroups: new Set<string>(),
  page: 1,
  limit: 25,
  totalCount: 0,
  searchQuery: '',
  filters: {},
  sort: { field: 'name', order: 'asc' },
  isLoading: false,
  error: null,
  ...initialCreateState,
  ...initialDetailState,
};

// ========================================
// Store
// ========================================

export const useGroupsStore = create<GroupsStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setGroups: (groups, totalCount) =>
        set((state) => {
          state.groups = groups;
          if (totalCount !== undefined) {
            state.totalCount = totalCount;
          }
        }),

      setSelectedGroups: (ids) =>
        set((state) => {
          state.selectedGroups = ids;
        }),

      toggleSelectGroup: (id) =>
        set((state) => {
          if (state.selectedGroups.has(id)) {
            state.selectedGroups.delete(id);
          } else {
            state.selectedGroups.add(id);
          }
        }),

      setPage: (page) =>
        set((state) => {
          state.page = page;
          state.selectedGroups = new Set();
        }),

      setLimit: (limit) =>
        set((state) => {
          state.limit = limit;
          state.page = 1;
          state.selectedGroups = new Set();
        }),

      setSearchQuery: (query) =>
        set((state) => {
          state.searchQuery = query;
          state.page = 1;
        }),

      setFilters: (filters) =>
        set((state) => {
          state.filters = { ...state.filters, ...filters };
          state.page = 1;
        }),

      clearFilters: () =>
        set((state) => {
          state.filters = {};
          state.page = 1;
        }),

      setSort: (sort) =>
        set((state) => {
          state.sort = sort;
        }),

      setLoading: (loading) =>
        set((state) => {
          state.isLoading = loading;
        }),

      setError: (error) =>
        set((state) => {
          state.error = error;
        }),

      reset: () => set(() => ({ ...initialState, selectedGroups: new Set<string>() })),

      // ── Create group panel actions ──
      openCreatePanel: () =>
        set((state) => {
          state.isCreatePanelOpen = true;
        }),

      closeCreatePanel: () =>
        set((state) => {
          state.isCreatePanelOpen = false;
        }),

      setCreateGroupName: (name) =>
        set((state) => {
          state.createGroupName = name;
        }),

      setCreateGroupDescription: (desc) =>
        set((state) => {
          state.createGroupDescription = desc;
        }),

      setCreateGroupUserIds: (ids) =>
        set((state) => {
          state.createGroupUserIds = ids;
        }),

      setIsCreating: (loading) =>
        set((state) => {
          state.isCreating = loading;
        }),

      resetCreateForm: () =>
        set((state) => {
          state.createGroupName = '';
          state.createGroupDescription = '';
          state.createGroupUserIds = [];
          state.isCreating = false;
        }),

      // ── Detail / Edit group panel actions ──
      openDetailPanel: (group) =>
        set((state) => {
          state.isDetailPanelOpen = true;
          state.detailGroup = group;
          state.isEditMode = false;
          state.editGroupName = group.name;
          state.editGroupDescription = '';
          state.editAddUserIds = [];
        }),

      closeDetailPanel: () =>
        set((state) => {
          state.isDetailPanelOpen = false;
          state.detailGroup = null;
          state.isEditMode = false;
          state.editGroupName = '';
          state.editGroupDescription = '';
          state.editAddUserIds = [];
          state.isSavingEdit = false;
        }),

      setDetailGroup: (group) =>
        set((state) => {
          state.detailGroup = group;
        }),

      enterEditMode: () =>
        set((state) => {
          if (state.detailGroup) {
            state.isEditMode = true;
            state.editGroupName = state.detailGroup.name;
            state.editGroupDescription = '';
            state.editAddUserIds = [];
          }
        }),

      exitEditMode: () =>
        set((state) => {
          state.isEditMode = false;
          state.editAddUserIds = [];
        }),

      setEditGroupName: (name) =>
        set((state) => {
          state.editGroupName = name;
        }),

      setEditGroupDescription: (desc) =>
        set((state) => {
          state.editGroupDescription = desc;
        }),

      setEditAddUserIds: (ids) =>
        set((state) => {
          state.editAddUserIds = ids;
        }),

      setIsSavingEdit: (loading) =>
        set((state) => {
          state.isSavingEdit = loading;
        }),

      resetEditForm: () =>
        set((state) => {
          state.editGroupName = state.detailGroup?.name ?? '';
          state.editGroupDescription = '';
          state.editAddUserIds = [];
          state.isSavingEdit = false;
        }),
    })),
    { name: 'GroupsStore' }
  )
);
