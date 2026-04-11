'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { enableMapSet } from 'immer';
import type { Team, TeamsFilter, TeamsSort } from './types';

enableMapSet();

// ========================================
// State
// ========================================

interface TeamsState {
  /** Team list data */
  teams: Team[];
  /** Selected team IDs */
  selectedTeams: Set<string>;

  /** Pagination */
  page: number;
  limit: number;
  totalCount: number;

  /** Search */
  searchQuery: string;

  /** Filters */
  filters: TeamsFilter;

  /** Sort */
  sort: TeamsSort;

  /** Loading */
  isLoading: boolean;

  /** Error message */
  error: string | null;

  // ── Create team panel ──
  isCreatePanelOpen: boolean;
  createTeamName: string;
  createTeamDescription: string;
  createTeamUserIds: string[];
  isCreating: boolean;

  // ── Detail / Edit team panel ──
  isDetailPanelOpen: boolean;
  detailTeam: Team | null;
  isEditMode: boolean;
  editTeamName: string;
  editTeamDescription: string;
  editAddUserIds: string[];
  isSavingEdit: boolean;
}

// ========================================
// Actions
// ========================================

interface TeamsActions {
  setTeams: (teams: Team[], totalCount?: number) => void;
  setSelectedTeams: (ids: Set<string>) => void;
  toggleSelectTeam: (id: string) => void;
  setPage: (page: number) => void;
  setLimit: (limit: number) => void;
  setSearchQuery: (query: string) => void;
  setFilters: (filters: Partial<TeamsFilter>) => void;
  clearFilters: () => void;
  setSort: (sort: TeamsSort) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;

  // ── Create team panel actions ──
  openCreatePanel: () => void;
  closeCreatePanel: () => void;
  setCreateTeamName: (name: string) => void;
  setCreateTeamDescription: (desc: string) => void;
  setCreateTeamUserIds: (ids: string[]) => void;
  setIsCreating: (loading: boolean) => void;
  resetCreateForm: () => void;

  // ── Detail / Edit team panel actions ──
  openDetailPanel: (team: Team) => void;
  closeDetailPanel: () => void;
  setDetailTeam: (team: Team | null) => void;
  enterEditMode: () => void;
  exitEditMode: () => void;
  setEditTeamName: (name: string) => void;
  setEditTeamDescription: (desc: string) => void;
  setEditAddUserIds: (ids: string[]) => void;
  setIsSavingEdit: (loading: boolean) => void;
  resetEditForm: () => void;
}

type TeamsStore = TeamsState & TeamsActions;

// ========================================
// Initial state
// ========================================

const initialCreateState = {
  isCreatePanelOpen: false,
  createTeamName: '',
  createTeamDescription: '',
  createTeamUserIds: [] as string[],
  isCreating: false,
};

const initialDetailState = {
  isDetailPanelOpen: false,
  detailTeam: null as Team | null,
  isEditMode: false,
  editTeamName: '',
  editTeamDescription: '',
  editAddUserIds: [] as string[],
  isSavingEdit: false,
};

const initialState: TeamsState = {
  teams: [],
  selectedTeams: new Set<string>(),
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

export const useTeamsStore = create<TeamsStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setTeams: (teams, totalCount) =>
        set((state) => {
          state.teams = teams;
          if (totalCount !== undefined) {
            state.totalCount = totalCount;
          }
        }),

      setSelectedTeams: (ids) =>
        set((state) => {
          state.selectedTeams = ids;
        }),

      toggleSelectTeam: (id) =>
        set((state) => {
          if (state.selectedTeams.has(id)) {
            state.selectedTeams.delete(id);
          } else {
            state.selectedTeams.add(id);
          }
        }),

      setPage: (page) =>
        set((state) => {
          state.page = page;
          state.selectedTeams = new Set();
        }),

      setLimit: (limit) =>
        set((state) => {
          state.limit = limit;
          state.page = 1;
          state.selectedTeams = new Set();
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

      reset: () => set(() => ({ ...initialState, selectedTeams: new Set<string>() })),

      // ── Create team panel actions ──
      openCreatePanel: () =>
        set((state) => {
          state.isCreatePanelOpen = true;
        }),

      closeCreatePanel: () =>
        set((state) => {
          state.isCreatePanelOpen = false;
        }),

      setCreateTeamName: (name) =>
        set((state) => {
          state.createTeamName = name;
        }),

      setCreateTeamDescription: (desc) =>
        set((state) => {
          state.createTeamDescription = desc;
        }),

      setCreateTeamUserIds: (ids) =>
        set((state) => {
          state.createTeamUserIds = ids;
        }),

      setIsCreating: (loading) =>
        set((state) => {
          state.isCreating = loading;
        }),

      resetCreateForm: () =>
        set((state) => {
          state.createTeamName = '';
          state.createTeamDescription = '';
          state.createTeamUserIds = [];
          state.isCreating = false;
        }),

      // ── Detail / Edit team panel actions ──
      openDetailPanel: (team) =>
        set((state) => {
          state.isDetailPanelOpen = true;
          state.detailTeam = team;
          state.isEditMode = false;
          state.editTeamName = team.name;
          state.editTeamDescription = team.description ?? '';
          state.editAddUserIds = [];
        }),

      closeDetailPanel: () =>
        set((state) => {
          state.isDetailPanelOpen = false;
          state.detailTeam = null;
          state.isEditMode = false;
          state.editTeamName = '';
          state.editTeamDescription = '';
          state.editAddUserIds = [];
          state.isSavingEdit = false;
        }),

      setDetailTeam: (team) =>
        set((state) => {
          state.detailTeam = team;
        }),

      enterEditMode: () =>
        set((state) => {
          if (state.detailTeam) {
            state.isEditMode = true;
            state.editTeamName = state.detailTeam.name;
            state.editTeamDescription = state.detailTeam.description ?? '';
            state.editAddUserIds = [];
          }
        }),

      exitEditMode: () =>
        set((state) => {
          state.isEditMode = false;
          state.editAddUserIds = [];
        }),

      setEditTeamName: (name) =>
        set((state) => {
          state.editTeamName = name;
        }),

      setEditTeamDescription: (desc) =>
        set((state) => {
          state.editTeamDescription = desc;
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
          state.editTeamName = state.detailTeam?.name ?? '';
          state.editTeamDescription = state.detailTeam?.description ?? '';
          state.editAddUserIds = [];
          state.isSavingEdit = false;
        }),
    })),
    { name: 'TeamsStore' }
  )
);
