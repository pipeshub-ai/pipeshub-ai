'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { enableMapSet } from 'immer';
import type { User, UsersFilter, UsersSort } from './types';
import type { TagItem } from '../components/tag-input';

enableMapSet();

// ========================================
// State
// ========================================

interface UsersState {
  /** User list data */
  users: User[];
  /** Selected user IDs */
  selectedUsers: Set<string>;

  /** All users cache — used by dropdowns across workspace panels (not paginated) */
  allUsers: User[];
  isLoadingAllUsers: boolean;

  /** Pagination */
  page: number;
  limit: number;
  totalCount: number;

  /** Search */
  searchQuery: string;

  /** Filters */
  filters: UsersFilter;

  /** Sort */
  sort: UsersSort;

  /** Loading */
  isLoading: boolean;

  /** Error message */
  error: string | null;

  // ── Invite panel state ──
  isInvitePanelOpen: boolean;
  inviteEmails: TagItem[];
  inviteRole: string | null;
  inviteGroupIds: string[];
  isInviting: boolean;
  /** When editing a pending invite, holds the user being edited */
  editingInviteUser: User | null;

  // ── Profile panel state ──
  isProfilePanelOpen: boolean;
  profileUser: User | null;
}

// ========================================
// Actions
// ========================================

interface UsersActions {
  setUsers: (users: User[], totalCount?: number) => void;
  setSelectedUsers: (ids: Set<string>) => void;
  setAllUsers: (users: User[]) => void;
  setIsLoadingAllUsers: (loading: boolean) => void;
  toggleSelectUser: (id: string) => void;
  toggleSelectAll: () => void;
  setPage: (page: number) => void;
  setLimit: (limit: number) => void;
  setSearchQuery: (query: string) => void;
  setFilters: (filters: Partial<UsersFilter>) => void;
  clearFilters: () => void;
  setSort: (sort: UsersSort) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;

  // ── Invite panel actions ──
  openInvitePanel: () => void;
  closeInvitePanel: () => void;
  /** Open the invite panel pre-populated for editing a pending user's invite */
  openInvitePanelForEdit: (user: User) => void;
  setInviteEmails: (emails: TagItem[]) => void;
  setInviteRole: (role: string | null) => void;
  setInviteGroupIds: (ids: string[]) => void;
  setIsInviting: (loading: boolean) => void;
  resetInviteForm: () => void;

  // ── Profile panel actions ──
  openProfilePanel: (user: User) => void;
  closeProfilePanel: () => void;
}

type UsersStore = UsersState & UsersActions;

// ========================================
// Initial state
// ========================================

const initialInviteState = {
  isInvitePanelOpen: false,
  inviteEmails: [] as TagItem[],
  inviteRole: null as string | null,
  inviteGroupIds: [] as string[],
  isInviting: false,
  editingInviteUser: null as User | null,
};

const initialProfileState = {
  isProfilePanelOpen: false,
  profileUser: null as User | null,
};

const initialState: UsersState = {
  users: [],
  selectedUsers: new Set<string>(),
  allUsers: [],
  isLoadingAllUsers: false,
  page: 1,
  limit: 25,
  totalCount: 0,
  searchQuery: '',
  filters: {},
  sort: { field: 'name', order: 'asc' },
  isLoading: false,
  error: null,
  ...initialInviteState,
  ...initialProfileState,
};

// ========================================
// Store
// ========================================

export const useUsersStore = create<UsersStore>()(
  devtools(
    immer((set, _get) => ({
      ...initialState,

      setUsers: (users, totalCount) =>
        set((state) => {
          state.users = users;
          if (totalCount !== undefined) {
            state.totalCount = totalCount;
          }
        }),

      setAllUsers: (users) =>
        set((state) => {
          state.allUsers = users;
        }),

      setIsLoadingAllUsers: (loading) =>
        set((state) => {
          state.isLoadingAllUsers = loading;
        }),

      setSelectedUsers: (ids) =>
        set((state) => {
          state.selectedUsers = ids;
        }),

      toggleSelectUser: (id) =>
        set((state) => {
          if (state.selectedUsers.has(id)) {
            state.selectedUsers.delete(id);
          } else {
            state.selectedUsers.add(id);
          }
        }),

      toggleSelectAll: () =>
        set((state) => {
          if (state.selectedUsers.size === state.users.length) {
            state.selectedUsers = new Set();
          } else {
            state.selectedUsers = new Set(state.users.map((u) => u.id));
          }
        }),

      setPage: (page) =>
        set((state) => {
          state.page = page;
          state.selectedUsers = new Set();
        }),

      setLimit: (limit) =>
        set((state) => {
          state.limit = limit;
          state.page = 1;
          state.selectedUsers = new Set();
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

      reset: () => set(() => ({ ...initialState, selectedUsers: new Set<string>() })),

      // ── Invite panel actions ──
      openInvitePanel: () =>
        set((state) => {
          state.isInvitePanelOpen = true;
          state.editingInviteUser = null;
        }),

      closeInvitePanel: () =>
        set((state) => {
          state.isInvitePanelOpen = false;
        }),

      openInvitePanelForEdit: (user: User) =>
        set((state) => {
          state.editingInviteUser = user;
          state.inviteEmails = [
            { id: user.userId, value: user.email || '', isValid: true },
          ];
          state.inviteRole = user.role || 'Member';
          // Group IDs are resolved by name in the sidebar after groups are fetched
          // (user.userGroups only contains { name, type }, not _id)
          state.inviteGroupIds = [];
          state.isInvitePanelOpen = true;
        }),

      setInviteEmails: (emails) =>
        set((state) => {
          state.inviteEmails = emails;
        }),

      setInviteRole: (role) =>
        set((state) => {
          state.inviteRole = role;
        }),

      setInviteGroupIds: (ids) =>
        set((state) => {
          state.inviteGroupIds = ids;
        }),

      setIsInviting: (loading) =>
        set((state) => {
          state.isInviting = loading;
        }),

      resetInviteForm: () =>
        set((state) => {
          state.inviteEmails = [];
          state.inviteRole = null;
          state.inviteGroupIds = [];
          state.isInviting = false;
          state.editingInviteUser = null;
        }),

      // ── Profile panel actions ──
      openProfilePanel: (user) =>
        set((state) => {
          state.isProfilePanelOpen = true;
          state.profileUser = user;
        }),

      closeProfilePanel: () =>
        set((state) => {
          state.isProfilePanelOpen = false;
          state.profileUser = null;
        }),
    })),
    { name: 'UsersStore' }
  )
);
