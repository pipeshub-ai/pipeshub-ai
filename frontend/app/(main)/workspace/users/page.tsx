'use client';

import React, { useEffect, useMemo, useCallback, useRef, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { formatDate } from '@/lib/utils/formatters';
import { FilterDropdown, DateRangePicker } from '@/app/components/ui';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { DateFilterType } from '@/app/components/ui/date-range-picker';
import {
  EntityPageHeader,
  EntityFilterBar,
  EntityDataTable,
  EntityPagination,
  EntityRowActionMenu,
  EntityBulkActionBar,
  AvatarCell,
  StatusBadge,
  ConfirmationDialog,
  DestructiveTypedConfirmationDialog,
} from '../components';
import type { BulkAction } from '../components';
import type { ColumnConfig } from '../components';
import type { FilterChipConfig } from '../components/entity-filter-bar';
import type { RowAction } from '../components/entity-row-action-menu';
import { USER_ROLES, ALL_ROLE_OPTIONS } from '../constants';
import { GroupType, type Group } from '../groups/types';
import { useUsersStore } from './store';
import { UsersApi } from './api';
import { GroupsApi } from '../groups/api';
import type { User } from './types';
import { InviteUsersSidebar, UserProfileSidebar } from './components';
import { usePaginatedFilterOptions } from '../hooks/use-paginated-filter-options';

// ========================================
// Constants
// ========================================

const USERS_FILTER_CHIPS: FilterChipConfig[] = [
  // { key: 'role', label: 'Role', icon: 'person' },
  { key: 'group', label: 'Group', icon: 'group' },
  { key: 'status', label: 'Status', icon: 'radio_button_checked' },
  { key: 'lastActive', label: 'Last Active', icon: 'schedule' },
  { key: 'dateJoined', label: 'Date Joined', icon: 'calendar_today' },
];

const ROLE_OPTIONS = [
  { value: USER_ROLES.ADMIN, label: 'Admin', icon: 'admin_panel_settings' },
  { value: USER_ROLES.MEMBER, label: 'Member', icon: 'person' },
];

const STATUS_OPTIONS = [
  { value: 'Active', label: 'Active', icon: 'check_circle', iconColor: 'var(--accent-11)' },
  { value: 'Pending', label: 'Pending', icon: 'schedule', iconColor: 'var(--amber-11)' },
  { value: 'Blocked', label: 'Blocked', icon: 'block', iconColor: 'var(--red-11)' },
];

// ========================================
// Helpers
// ========================================

/** Check if a timestamp (ms) falls within a date range */
function isInDateRange(
  timestampMs: number | undefined,
  afterDate?: string,
  beforeDate?: string,
  dateType?: DateFilterType
): boolean {
  if (!timestampMs) return false;
  if (!afterDate && !beforeDate) return true;

  const itemDate = new Date(timestampMs);
  itemDate.setHours(0, 0, 0, 0);

  if (dateType === 'on' && afterDate) {
    const target = new Date(afterDate);
    target.setHours(0, 0, 0, 0);
    return itemDate.getTime() === target.getTime();
  }
  if (dateType === 'before' && beforeDate) {
    const before = new Date(beforeDate);
    before.setHours(0, 0, 0, 0);
    return itemDate < before;
  }
  if (dateType === 'after' && afterDate) {
    const after = new Date(afterDate);
    after.setHours(0, 0, 0, 0);
    return itemDate > after;
  }
  // between
  if (afterDate && beforeDate) {
    const after = new Date(afterDate);
    after.setHours(0, 0, 0, 0);
    const before = new Date(beforeDate);
    before.setHours(23, 59, 59, 999);
    return itemDate >= after && itemDate <= before;
  }
  return true;
}

// ========================================
// Page Component
// ========================================

function UsersPageContent() {
  const { t } = useTranslation();
  const currentUser = useAuthStore((s) => s.user);
  const addToast = useToastStore((s) => s.addToast);
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  // Remove user confirmation state
  const [removeTarget, setRemoveTarget] = useState<User | null>(null);
  const [isRemoving, setIsRemoving] = useState(false);
  const [cancelInviteTarget, setCancelInviteTarget] = useState<User | null>(null);
  const [isCancellingInvite, setIsCancellingInvite] = useState(false);
  const [unblockTarget, setUnblockTarget] = useState<User | null>(null);
  const [isUnblocking, setIsUnblocking] = useState(false);
  // Admin group ref for role changes
  const adminGroupRef = useRef<Group | null>(null);

  const {
    users,
    selectedUsers,
    page,
    limit,
    totalCount,
    searchQuery,
    filters,
    isLoading,
    error: _error,
    setUsers,
    setSelectedUsers,
    setPage,
    setLimit,
    setSearchQuery,
    setFilters,
    setLoading,
    setError,
    openInvitePanel,
    closeInvitePanel,
    isInvitePanelOpen,
    isProfilePanelOpen,
    openProfilePanel,
    closeProfilePanel,
  } = useUsersStore();

  // ── Paginated group filter ──
  const groupsRef = useRef<Group[]>([]);
  const groupFilter = usePaginatedFilterOptions<Group>({
    fetcher: async (search, page, limit) => {
      const { groups, totalCount } = await GroupsApi.listGroups({ page, limit, search });
      return { items: groups, totalCount };
    },
    mapOption: (g) => ({
      value: g._id,
      label: g.name.charAt(0).toUpperCase() + g.name.slice(1),
      icon: 'group',
    }),
    onFetched: (groups, page, search) => {
      // Cache the admin group for role changes (from first page, no search)
      if (!search && page === 1) {
        groupsRef.current = groups;
        adminGroupRef.current = groups.find((g) => g.type === GroupType.ADMIN) ?? null;
      } else {
        groupsRef.current = [...groupsRef.current, ...groups];
      }
    },
  });
  // Filter out "everyone" group from displayed options
  const groupOptions = useMemo(
    () => groupFilter.options.filter((o) => {
      const group = groupsRef.current.find((g) => g._id === o.value);
      return !group || group.type !== GroupType.EVERYONE;
    }),
    [groupFilter.options]
  );

  // Capture userId from initial URL load so we can restore the profile panel
  // after fetchUsers completes (users list is empty on first render).
  // undefined = not yet read, string = pending restore, null = resolved
  const pendingProfileUserIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const panel = searchParams.get('panel');
    const userId = searchParams.get('userId');
    pendingProfileUserIdRef.current = (panel === 'profile' && userId) ? userId : null;
  }, []);

  // ── Fetch users (server-paginated + server-filtered) ──────────────────
  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Build server-side filter params
      const params: Parameters<typeof UsersApi.fetchMergedUsers>[0] = {
        page,
        limit,
        search: searchQuery || undefined,
      };
      // Status filter → hasLoggedIn and/or isBlocked flags
      if (filters.statuses?.length) {
        const selected = new Set(filters.statuses);
        const hasActive = selected.has('Active');
        const hasPending = selected.has('Pending');
        const hasBlocked = selected.has('Blocked');

        if (hasActive && hasPending && hasBlocked) {
          // All selected => no status filters.
        } else {
          // Active xor Pending => filter by login state; both => include both.
          if (hasActive !== hasPending) {
            params.hasLoggedIn = hasActive ? 'true' : 'false';
          }
          // If blocked is selected, include blocked users (OR logic in backend);
          // otherwise constrain to non-blocked when any non-blocked status is selected.
          if (hasBlocked) {
            params.isBlocked = 'true';
          } else if (hasActive || hasPending) {
            params.isBlocked = 'false';
          }
        }
      }
      // Group filter → comma-separated group IDs
      if (filters.groups?.length) {
        params.groupIds = filters.groups.join(',');
      }

      const result = await UsersApi.fetchMergedUsers(params);
      setUsers(result.users, result.totalCount);

      // Restore profile panel when navigating directly to ?panel=profile&userId=xxx
      const pendingId = pendingProfileUserIdRef.current;
      if (pendingId) {
        const match = result.users.find((u) => u.userId === pendingId);
        if (match) openProfilePanel(match);
        // Always clear so subsequent fetches don't re-open
        pendingProfileUserIdRef.current = null;
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load users';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [page, limit, searchQuery, filters, setUsers, setLoading, setError, openProfilePanel]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  // ── URL ↔ Store panel sync ──────────────────────────────────────────
  // Same pattern as groups page — see docs/url-driven-panel-state.md
  const pendingUrlRef = useRef<string | null>(null);
  const initialUrlProcessed = useRef(false);

  // URL → Store
  useEffect(() => {
    const panel = searchParams.get('panel');
    const urlKey = panel ?? '';

    if (pendingUrlRef.current === urlKey) {
      pendingUrlRef.current = null;
      return;
    }

    const store = useUsersStore.getState();

    if (panel === 'invite') {
      if (!store.isInvitePanelOpen) openInvitePanel();
      if (store.isProfilePanelOpen) closeProfilePanel();
    } else if (panel === 'profile') {
      if (store.isInvitePanelOpen) closeInvitePanel();
      // Restore profile panel from URL if we have users loaded and a userId param
      const userId = searchParams.get('userId');
      if (userId && !store.isProfilePanelOpen) {
        const allUsers = useUsersStore.getState().users;
        const match = allUsers.find((u) => u.userId === userId);
        if (match) openProfilePanel(match);
      }
    } else if (initialUrlProcessed.current) {
      if (store.isInvitePanelOpen) closeInvitePanel();
      if (store.isProfilePanelOpen) closeProfilePanel();
    }

    initialUrlProcessed.current = true;
  }, [searchParams]);

  // Store → URL
  useEffect(() => {
    if (!initialUrlProcessed.current) return;

    const profileUser = useUsersStore.getState().profileUser;
    const targetPanel = isInvitePanelOpen ? 'invite' : isProfilePanelOpen ? 'profile' : null;

    // Build the full URL key (panel + userId for profile)
    const targetUrl = targetPanel === 'profile' && profileUser
      ? `/workspace/users/?panel=profile&userId=${profileUser.userId}`
      : targetPanel
        ? `/workspace/users/?panel=${targetPanel}`
        : '/workspace/users/';

    const currentUrl = window.location.pathname + window.location.search;

    if (targetUrl !== currentUrl) {
      // Don't redirect away from a profile URL while waiting for users to load
      // (pendingProfileUserIdRef being non-null means restoration is in flight)
      if (pendingProfileUserIdRef.current) return;
      pendingUrlRef.current = targetPanel ?? '';
      router.replace(targetUrl);
    }
  }, [isInvitePanelOpen, isProfilePanelOpen, searchParams, router]);

  // ── URL-based panel navigation helpers ──
  const navigateToInvitePanel = useCallback(() => {
    router.push('/workspace/users/?panel=invite');
  }, [router]);

  const navigateToProfilePanel = useCallback(
    (user: User) => {
      router.push(`/workspace/users/?panel=profile&userId=${user.userId}`);
    },
    [router]
  );

  // ── Filter chips with translated labels ──
  const filterChips = useMemo<FilterChipConfig[]>(
    () =>
      USERS_FILTER_CHIPS.map((chip) => ({
        ...chip,
        label: t(`workspace.filters.${chip.key}`) || chip.label,
      })),
    [t]
  );

  // ── Render individual filter components ──
  const renderFilter = useCallback(
    (filter: FilterChipConfig) => {
      switch (filter.key) {
        // case 'role':
        //   return (
        //     <FilterDropdown
        //       label={filter.label}
        //       icon={filter.icon}
        //       options={ROLE_OPTIONS}
        //       selectedValues={filters.roles || []}
        //       onSelectionChange={(values) => setFilters({ roles: values })}
        //     />
        //   );
        case 'group':
          return (
            <FilterDropdown
              label={filter.label}
              icon={filter.icon}
              options={groupOptions}
              selectedValues={filters.groups || []}
              onSelectionChange={(values) => setFilters({ groups: values })}
              searchable
              onSearch={groupFilter.onSearch}
              onLoadMore={groupFilter.onLoadMore}
              isLoadingMore={groupFilter.isLoading}
              hasMore={groupFilter.hasMore}
            />
          );
        case 'status':
          return (
            <FilterDropdown
              label={filter.label}
              icon={filter.icon}
              options={STATUS_OPTIONS}
              selectedValues={filters.statuses || []}
              onSelectionChange={(values) =>
                setFilters({ statuses: values as ('Active' | 'Pending' | 'Blocked')[] })
              }
            />
          );
        case 'lastActive':
          return (
            <DateRangePicker
              label={filter.label}
              icon={filter.icon}
              startDate={filters.lastActiveAfter}
              endDate={filters.lastActiveBefore}
              dateType={filters.lastActiveDateType}
              onApply={(startDate, endDate, dateType) =>
                setFilters({
                  lastActiveAfter: dateType === 'before' ? undefined : startDate,
                  lastActiveBefore: dateType === 'after' ? undefined
                    : dateType === 'on' ? startDate
                    : endDate || startDate,
                  lastActiveDateType: dateType,
                })
              }
              onClear={() =>
                setFilters({
                  lastActiveAfter: undefined,
                  lastActiveBefore: undefined,
                  lastActiveDateType: undefined,
                })
              }
              defaultDateType="between"
            />
          );
        case 'dateJoined':
          return (
            <DateRangePicker
              label={filter.label}
              icon={filter.icon}
              startDate={filters.dateJoinedAfter}
              endDate={filters.dateJoinedBefore}
              dateType={filters.dateJoinedDateType}
              onApply={(startDate, endDate, dateType) =>
                setFilters({
                  dateJoinedAfter: dateType === 'before' ? undefined : startDate,
                  dateJoinedBefore: dateType === 'after' ? undefined
                    : dateType === 'on' ? startDate
                    : endDate || startDate,
                  dateJoinedDateType: dateType,
                })
              }
              onClear={() =>
                setFilters({
                  dateJoinedAfter: undefined,
                  dateJoinedBefore: undefined,
                  dateJoinedDateType: undefined,
                })
              }
              defaultDateType="between"
            />
          );
        default:
          return null;
      }
    },
    [filters, setFilters, groupOptions, groupFilter]
  );

  // ── Client-side date filters (role/group/status are server-side) ──
  const filteredUsers = useMemo(() => {
    let result = users;

    // Last Active date filter (client-side)
    if (filters.lastActiveAfter || filters.lastActiveBefore) {
      result = result.filter((u) =>
        isInDateRange(
          u.updatedAtTimestamp,
          filters.lastActiveAfter,
          filters.lastActiveBefore,
          filters.lastActiveDateType
        )
      );
    }

    // Date Joined filter (client-side)
    if (filters.dateJoinedAfter || filters.dateJoinedBefore) {
      result = result.filter((u) =>
        isInDateRange(
          u.createdAtTimestamp,
          filters.dateJoinedAfter,
          filters.dateJoinedBefore,
          filters.dateJoinedDateType
        )
      );
    }

    return result;
  }, [users, filters]);

  // Server already returns the correct page; client-side filters may further narrow the set
  const paginatedUsers = filteredUsers;
  const effectiveTotalCount = totalCount;

  const hasActiveFilters = !!(
    searchQuery.trim() ||
    filters.statuses?.length ||
    filters.groups?.length ||
    filters.lastActiveAfter ||
    filters.lastActiveBefore ||
    filters.dateJoinedAfter ||
    filters.dateJoinedBefore
  );
  const isEmptyFiltered = !isLoading && paginatedUsers.length === 0 && hasActiveFilters;

  // ── Bulk action logic ─────────────────────────────────────────
  const selectedUsersList = useMemo(
    () => paginatedUsers.filter((u) => selectedUsers.has(u.id)),
    [paginatedUsers, selectedUsers]
  );

  /** True when every selected user is a pending invite (not logged in, still active) */
  const allSelectedArePending = useMemo(
    () =>
      selectedUsersList.length > 0 &&
      selectedUsersList.every((u) => !u.hasLoggedIn && u.isActive),
    [selectedUsersList]
  );

  const handleBulkRemove = useCallback(async () => {
    const userIds = selectedUsersList.map((u) => u.userId);
    const { succeeded, failed } = await UsersApi.bulkRemoveUsers(userIds);
    if (succeeded > 0) {
      addToast({
        variant: 'success',
        title: t('workspace.users.bulk.removeSuccess', {
          count: succeeded,
          defaultValue: `${succeeded} user(s) removed from workspace`,
        }),
        duration: 3000,
      });
    }
    if (failed > 0) {
      addToast({
        variant: 'error',
        title: t('workspace.users.bulk.removePartialError', {
          count: failed,
          defaultValue: `Failed to remove ${failed} user(s)`,
        }),
        duration: 5000,
      });
    }
    setSelectedUsers(new Set());
    fetchUsers();
  }, [selectedUsersList, addToast, t, setSelectedUsers, fetchUsers]);

  const handleBulkResendInvite = useCallback(async () => {
    const userIds = selectedUsersList.map((u) => u.userId);
    const { succeeded, failed } = await UsersApi.bulkResendInvites(userIds);
    if (succeeded > 0) {
      addToast({
        variant: 'success',
        title: t('workspace.users.bulk.resendSuccess', {
          count: succeeded,
          defaultValue: `Invite resent to ${succeeded} user(s)`,
        }),
        duration: 3000,
      });
    }
    if (failed > 0) {
      addToast({
        variant: 'error',
        title: t('workspace.users.bulk.resendPartialError', {
          count: failed,
          defaultValue: `Failed to resend invite to ${failed} user(s)`,
        }),
        duration: 5000,
      });
    }
    setSelectedUsers(new Set());
  }, [selectedUsersList, addToast, t, setSelectedUsers]);

  const handleBulkCancelInvite = useCallback(async () => {
    const userIds = selectedUsersList.map((u) => u.userId);
    const { succeeded, failed } = await UsersApi.bulkCancelInvites(userIds);
    if (succeeded > 0) {
      addToast({
        variant: 'success',
        title: t('workspace.users.bulk.cancelSuccess', {
          count: succeeded,
          defaultValue: `${succeeded} invite(s) cancelled`,
        }),
        duration: 3000,
      });
    }
    if (failed > 0) {
      addToast({
        variant: 'error',
        title: t('workspace.users.bulk.cancelPartialError', {
          count: failed,
          defaultValue: `Failed to cancel ${failed} invite(s)`,
        }),
        duration: 5000,
      });
    }
    setSelectedUsers(new Set());
    fetchUsers();
  }, [selectedUsersList, addToast, t, setSelectedUsers, fetchUsers]);

  /** Bulk actions shown in the floating bar — varies by selection composition */
  const hasAdminSelected = useMemo(
    () => selectedUsersList.some((u) => u.role === 'Admin'),
    [selectedUsersList]
  );

  const bulkActions = useMemo<BulkAction[]>(() => {
    if (allSelectedArePending) {
      // All selected are invited (pending) users
      return [
        {
          key: 'resend-invite',
          label: t('workspace.users.bulk.resendInvite', 'Resend Invite'),
          icon: 'send',
          variant: 'default',
          onClick: handleBulkResendInvite,
        },
        {
          key: 'cancel-invite',
          label: t('workspace.users.bulk.cancelInvite', 'Cancel Invite'),
          icon: 'cancel_schedule_send',
          variant: 'danger',
          onClick: handleBulkCancelInvite,
        },
      ];
    }

    // Mixed set or all active users
    return [
      {
        key: 'remove-from-workspace',
        label: hasAdminSelected
          ? t('workspace.users.bulk.adminCantBeRemoved', 'Admin users cannot be removed')
          : t('workspace.users.bulk.removeFromWorkspace', 'Remove from Workplace'),
        icon: hasAdminSelected ? 'block' : 'person_remove',
        variant: 'danger',
        disabled: hasAdminSelected,
        onClick: handleBulkRemove,
      },
    ];
  }, [allSelectedArePending, hasAdminSelected, t, handleBulkResendInvite, handleBulkCancelInvite, handleBulkRemove]);

  // ── Column definitions ──────────────────

  const columns = useMemo<ColumnConfig<User>[]>(
    () => [
      {
        key: 'user',
        label: t('workspace.users.columns.user'),
        minWidth: '220px',
        render: (user) => (
          <AvatarCell
            name={user.name || user.email || '-'}
            email={user.name ? user.email : undefined}
            isSelf={currentUser?.id === user.id || currentUser?.email === user.email}
            profilePicture={user.profilePicture}
          />
        ),
      },
      {
        key: 'role',
        label: t('workspace.users.columns.role'),
        width: '112px',
        render: (user) => (
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {user.role || 'Member'}
          </Text>
        ),
      },
      {
        key: 'groups',
        label: t('workspace.users.columns.groups'),
        width: '100px',
        render: (user) => (
          <Badge variant="soft" color="gray" size="1">
            {user.groupCount ?? 0}
          </Badge>
        ),
      },
      {
        key: 'status',
        label: t('workspace.users.columns.status'),
        width: '110px',
        render: (user) => (
          <StatusBadge
            status={
              user.isBlocked ? 'Blocked' : user.hasLoggedIn ? 'Active' : 'Pending'
            }
          />
        ),
      },
      {
        key: 'lastActive',
        label: t('workspace.users.columns.lastActive'),
        width: '130px',
        render: (user) => (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {user.updatedAtTimestamp ? formatDate(user.updatedAtTimestamp) : '-'}
          </Text>
        ),
      },
      {
        key: 'dateJoined',
        label: t('workspace.users.columns.dateJoined'),
        width: '130px',
        render: (user) => (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {user.createdAtTimestamp ? formatDate(user.createdAtTimestamp) : '-'}
          </Text>
        ),
      },
    ],
    [t, currentUser]
  );

  // ── Row actions ────────────
  const handleRemoveUser = useCallback(async () => {
    if (!removeTarget) return;
    setIsRemoving(true);
    try {
      await UsersApi.deleteUser(removeTarget.userId);
      addToast({
        variant: 'success',
        title: t('workspace.users.actions.removeSuccess'),
        description: t('workspace.users.actions.removeSuccessDescription', {
          name: removeTarget.name || removeTarget.email,
        }),
        duration: 3000,
      });
      setRemoveTarget(null);
      fetchUsers();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.users.actions.removeError'),
        duration: 5000,
      });
    } finally {
      setIsRemoving(false);
    }
  }, [removeTarget, fetchUsers, addToast, t]);

  const handleCancelInvite = useCallback(async () => {
    if (!cancelInviteTarget) return;
    setIsCancellingInvite(true);
    try {
      await UsersApi.deleteUser(cancelInviteTarget.userId);
      addToast({
        variant: 'success',
        title: t('workspace.users.actions.cancelInviteSuccess'),
        description: t('workspace.users.actions.cancelInviteSuccessDescription', {
          email: cancelInviteTarget.email || cancelInviteTarget.name || '',
        }),
        duration: 3000,
      });
      setCancelInviteTarget(null);
      fetchUsers();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.users.actions.cancelInviteError'),
        duration: 5000,
      });
    } finally {
      setIsCancellingInvite(false);
    }
  }, [cancelInviteTarget, fetchUsers, addToast, t]);

  const handleConfirmUnblock = useCallback(async () => {
    if (!unblockTarget) return;
    setIsUnblocking(true);
    try {
      await UsersApi.unblockUser(unblockTarget.userId);
      addToast({
        variant: 'success',
        title: t('workspace.users.actions.unblockSuccess', 'User unblocked'),
        duration: 3000,
      });
      setUnblockTarget(null);
      fetchUsers();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.users.actions.unblockError', 'Failed to unblock user'),
        duration: 5000,
      });
    } finally {
      setIsUnblocking(false);
    }
  }, [unblockTarget, addToast, t, fetchUsers]);

  const unblockConfirmKeyword =
    unblockTarget?.name?.trim() || unblockTarget?.email || '';

  // Shared "coming soon" toast helper
  const showComingSoon = useCallback(() => {
    addToast({
      variant: 'info',
      title: t('workspace.users.actions.comingSoonTitle'),
      description: t('workspace.users.actions.comingSoonDescription'),
      duration: 3000,
    });
  }, [addToast, t]);

  // ── Change Role handler ──
  const handleChangeRole = useCallback(
    async (user: User, newRole: string) => {
      const currentRole = user.role || 'Member';
      if (newRole === currentRole) return;

      const adminGroup = adminGroupRef.current;
      if (!adminGroup) {
        addToast({
          variant: 'error',
          title: t('workspace.users.actions.changeRoleError', 'Failed to change role'),
          description: 'Admin group not found',
          duration: 5000,
        });
        return;
      }

      try {
        if (newRole === 'Admin') {
          // Add user to admin group
          await GroupsApi.addUsersToGroups([user.userId], [adminGroup._id]);
        } else {
          // Remove user from admin group
          await GroupsApi.removeUsersFromGroups([user.userId], [adminGroup._id]);
        }

        addToast({
          variant: 'success',
          title: t('workspace.users.actions.changeRoleSuccess', 'Role updated'),
          description: t(
            'workspace.users.actions.changeRoleSuccessDescription',
            {
              name: user.name || user.email,
              role: newRole,
              defaultValue: `${user.name || user.email} is now ${newRole}`,
            }
          ),
          duration: 3000,
        });

        // Refresh users list to reflect the change
        fetchUsers();
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.users.actions.changeRoleError', 'Failed to change role'),
          duration: 5000,
        });
      }
    },
    [addToast, t, fetchUsers]
  );

  // ── Resend Invite handler ──
  const handleResendInvite = useCallback(
    async (user: User) => {
      try {
        await UsersApi.resendInvite(user.userId);
        addToast({
          variant: 'success',
          title: t('workspace.users.actions.resendInviteSuccess', 'Invite resent'),
          description: t(
            'workspace.users.actions.resendInviteSuccessDescription',
            {
              email: user.email,
              defaultValue: `Invite has been resent to ${user.email}`,
            }
          ),
          duration: 3000,
        });
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.users.actions.resendInviteError', 'Failed to resend invite'),
          duration: 5000,
        });
      }
    },
    [addToast, t]
  );

  // ── Edit Invite handler ──
  const handleEditInvite = useCallback(
    (user: User) => {
      // Open the invite panel in edit mode, pre-populated with the pending user's data
      useUsersStore.getState().openInvitePanelForEdit(user);
    },
    []
  );

  // Role options for the sub-menu — sourced from shared constants,
  // with i18n-translated labels and descriptions.
  const ROLE_SUB_MENU_OPTIONS = useMemo(
    () =>
      ALL_ROLE_OPTIONS.map((role) => ({
        value: role.value,
        label: t(`workspace.users.roles.${role.value.toLowerCase()}`, role.label),
        description: t(
          `workspace.users.roles.${role.value.toLowerCase()}Description`,
          role.description
        ),
      })),
    [t]
  );

  const renderRowActions = useCallback(
    (user: User) => {
      // Pending: invited but never logged in, invite still active
      const isPending = !user.hasLoggedIn && user.isActive;
      // Expired: invited but never logged in and invite is no longer active
      const isPendingExpired = !user.hasLoggedIn && !user.isActive;
      // Deactivated: has logged in before but account is now inactive
      const isDeactivated = user.hasLoggedIn && !user.isActive;
      // Active: logged in and currently active
      const isActive = user.hasLoggedIn && user.isActive;
      const currentRole = user.role || 'Member';

      let actions: (RowAction | false)[];

      if (user.isBlocked) {
        actions = [
          {
            icon: 'visibility',
            label: t('workspace.users.actions.viewProfile'),
            onClick: () => navigateToProfilePanel(user),
          },
          {
            icon: 'lock_open',
            label: t('workspace.users.actions.unblock', 'Unblock'),
            onClick: () => setUnblockTarget(user),
          },
        ];
      } else if (isPending) {
        // Pending invite — invite management actions
        actions = [
          {
            icon: 'send',
            label: t('workspace.users.actions.resendInvite'),
            onClick: () => handleResendInvite(user),
          },
          {
            icon: 'edit',
            label: t('workspace.users.actions.editInvite'),
            onClick: () => handleEditInvite(user),
          },
          {
            icon: 'cancel_schedule_send',
            label: t('workspace.users.actions.cancelInvite'),
            variant: 'danger' as const,
            separatorBefore: true,
            onClick: () => setCancelInviteTarget(user),
          },
        ];
      } else if (isPendingExpired) {
        // Expired invite — same as pending: resend or cancel
        actions = [
          {
            icon: 'send',
            label: t('workspace.users.actions.resendInvite'),
            onClick: () => handleResendInvite(user),
          },
          {
            icon: 'cancel_schedule_send',
            label: t('workspace.users.actions.cancelInvite'),
            variant: 'danger' as const,
            separatorBefore: true,
            onClick: () => setCancelInviteTarget(user),
          },
        ];
      } else if (isDeactivated) {
        // TODO: Handle deactivated user — e.g. Reactivate, Remove from Workspace
        actions = [];
      } else if (isActive && currentRole === 'Admin') {
        // Active Admin — View Profile only
        actions = [
          {
            icon: 'visibility',
            label: t('workspace.users.actions.viewProfile'),
            onClick: () => navigateToProfilePanel(user),
          },
          // {
          //   icon: 'manage_accounts',
          //   label: t('workspace.users.actions.changeRole'),
          //   subMenu: {
          //     type: 'radio' as const,
          //     value: currentRole,
          //     onValueChange: (newRole: string) => handleChangeRole(user, newRole),
          //     options: ROLE_SUB_MENU_OPTIONS,
          //   },
          // },
        ];
      } else if (isActive) {
        // Active Member/Guest — management actions
        actions = [
          {
            icon: 'visibility',
            label: t('workspace.users.actions.viewProfile'),
            onClick: () => navigateToProfilePanel(user),
          },
          // {
          //   icon: 'manage_accounts',
          //   label: t('workspace.users.actions.changeRole'),
          //   subMenu: {
          //     type: 'radio' as const,
          //     value: currentRole,
          //     onValueChange: (newRole: string) => handleChangeRole(user, newRole),
          //     options: ROLE_SUB_MENU_OPTIONS,
          //   },
          // },
          {
            icon: 'person_off',
            label: t('workspace.users.actions.deactivate'),
            onClick: showComingSoon,
          },
          {
            icon: 'person_remove',
            label: t('workspace.users.actions.removeFromWorkspace'),
            variant: 'danger' as const,
            separatorBefore: true,
            onClick: () => setRemoveTarget(user),
          },
        ];
      } else {
        actions = [];
      }

      return <EntityRowActionMenu actions={actions} />;
    },
    [
      t,
      navigateToProfilePanel,
      showComingSoon,
      handleChangeRole,
      handleResendInvite,
      handleEditInvite,
      ROLE_SUB_MENU_OPTIONS,
    ]
  );

  // ── Redirect non-admin users ──────────────────────────────
  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // Prevent rendering (and running data-fetching effects) while profile is
  // unresolved or before the redirect fires for confirmed non-admin users.
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── Render ──────────────────────────────

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingLeft: '40px',
        paddingRight: '40px',
      }}
    >
      {/* Header */}
      <EntityPageHeader
        title={t('workspace.users.title')}
        subtitle={t('workspace.users.subtitle')}
        searchPlaceholder={t('workspace.users.searchPlaceholder')}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        ctaLabel={t('workspace.users.inviteButton')}
        ctaIcon="person_add_alt"
        onCtaClick={navigateToInvitePanel}
      />

      {/* Content: filter bar + table + pagination */}
      <Flex
        direction="column"
        style={{
          flex: 1,
          overflow: 'hidden',
          border: '1px solid var(--slate-6)',
          borderRadius: 'var(--radius-3)',
        }}
      >
        {/* Filter bar */}
        <EntityFilterBar filters={filterChips} renderFilter={renderFilter} />

        {isEmptyFiltered ? (
          <Flex
            direction="column"
            align="center"
            justify="center"
            gap="2"
            style={{ flex: 1, padding: 'var(--space-6)' }}
          >
            <MaterialIcon name="filter_list_off" size={32} color="var(--slate-8)" />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.users.noFilterResults', 'No users match the applied filters')}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {t('workspace.users.noFilterResultsHint', 'Try adjusting or clearing the filters above')}
            </Text>
          </Flex>
        ) : (
          <>
            {/* Data table */}
            <EntityDataTable<User>
              columns={columns}
              data={paginatedUsers}
              getItemId={(u) => u.id}
              selectedIds={selectedUsers}
              onSelectionChange={setSelectedUsers}
              renderRowActions={renderRowActions}
              isLoading={isLoading}
              onRowClick={(user) => navigateToProfilePanel(user)}
            />
          </>
        )}

        {/* Footer: pagination + bulk action bar */}
        <Flex
          align="center"
          style={{
            position: 'relative',
            flexShrink: 0,
            width: '100%',
          }}
        >
          <EntityPagination
            page={page}
            limit={limit}
            totalCount={effectiveTotalCount}
            onPageChange={setPage}
            onLimitChange={setLimit}
          />

          {/* Bulk action bar — overlays pagination when items selected */}
          <EntityBulkActionBar
            selectedCount={selectedUsers.size}
            itemLabel={t('workspace.users.bulkLabel', 'Users')}
            actions={bulkActions}
            visible={selectedUsers.size > 0}
          />
        </Flex>
      </Flex>

      {/* Invite Users Sidebar */}
      <InviteUsersSidebar onInviteSuccess={fetchUsers} />

      {/* User Profile Sidebar */}
      <UserProfileSidebar />

      {/* Remove User Confirmation Dialog */}
      <ConfirmationDialog
        open={!!removeTarget}
        onOpenChange={(open) => {
          if (!open) setRemoveTarget(null);
        }}
        title={t('workspace.users.actions.removeUserTitle')}
        message={t('workspace.users.actions.removeUserMessage', {
          email: removeTarget?.email || removeTarget?.name || '',
          workspace: 'Pipeshub',
        })}
        confirmLabel={t('workspace.users.actions.removeButton')}
        cancelLabel={t('workspace.users.actions.cancelButton')}
        confirmVariant="danger"
        isLoading={isRemoving}
        onConfirm={handleRemoveUser}
      />

      {/* Cancel Invite Confirmation Dialog */}
      <ConfirmationDialog
        open={!!cancelInviteTarget}
        onOpenChange={(open) => {
          if (!open) setCancelInviteTarget(null);
        }}
        title={t('workspace.users.actions.cancelInviteConfirmTitle')}
        message={t('workspace.users.actions.cancelInviteConfirmMessage', {
          email: cancelInviteTarget?.email || cancelInviteTarget?.name || '',
        })}
        confirmLabel={t('workspace.users.actions.cancelInvite')}
        cancelLabel={t('workspace.users.actions.keepInviteButton')}
        confirmVariant="danger"
        isLoading={isCancellingInvite}
        onConfirm={handleCancelInvite}
      />

      <DestructiveTypedConfirmationDialog
        open={!!unblockTarget}
        onOpenChange={(open) => {
          if (!open) setUnblockTarget(null);
        }}
        heading={t('workspace.users.actions.unblockTypedConfirmTitle', {
          name: unblockTarget?.name || unblockTarget?.email || '',
          defaultValue: 'Unblock {{name}}?',
        })}
        body={
          <>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.users.actions.unblockTypedConfirmBodyLine1', {
                name: unblockTarget?.name || unblockTarget?.email || '',
                defaultValue: 'This will restore sign-in access for {{name}}.',
              })}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t(
                'workspace.users.actions.unblockTypedConfirmBodyLine2',
                'Type the user\'s display name exactly to confirm.'
              )}
            </Text>
          </>
        }
        confirmationKeyword={unblockConfirmKeyword}
        confirmInputLabel={t('workspace.users.actions.typeNameToConfirm', {
          keyword: unblockConfirmKeyword,
        })}
        primaryButtonText={t('workspace.users.actions.unblockConfirmAction', 'Unblock')}
        cancelLabel={t('workspace.users.actions.cancelButton')}
        isLoading={isUnblocking}
        confirmLoadingLabel={t('workspace.users.actions.unblocking', 'Unblocking…')}
        onConfirm={() => void handleConfirmUnblock()}
      />
    </Flex>
  );
}

export default function UsersPage() {
  return (
    <Suspense>
      <UsersPageContent />
    </Suspense>
  );
}
