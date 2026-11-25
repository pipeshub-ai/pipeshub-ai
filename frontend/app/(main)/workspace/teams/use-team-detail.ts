'use client';

/**
 * A team's panel, its state and its saves (`TeamDetailSidebar`): which members are added, removed or given another
 * role. Toasts are the caller's (`notices`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/config';
import { useUserStore } from '@/lib/store/user-store';
import type { CheckboxOption, PaginatedMembersListHandle } from '../components';
import { usePaginatedUserOptions } from '../hooks/use-paginated-user-options';
import { TeamsApi } from './api';
import { useTeamsStore } from './store';
import type { TeamMember, TeamMemberRole } from './types';

/** Min addable users to prefetch before stopping auto-pagination. */
const MIN_ADDABLE_USER_BUFFER = 25;
/** Cap auto-prefetch pages when client-side member filtering leaves sparse results. */
const MAX_ADD_USER_PREFETCH_PAGES = 10;

export interface TeamDetailNotices {
  saved: (name: string) => void;
  saveFailed: (error: unknown) => void;
  deleted: (name: string) => void;
  deleteFailed: () => void;
}

export function useTeamDetail({ onUpdateSuccess, notices }: { onUpdateSuccess?: () => void; notices: TeamDetailNotices }) {
  const { t } = useTranslation();
  const currentUser = useAuthStore((s) => s.user);
  const profile = useUserStore((s) => s.profile);

  const {
    isDetailPanelOpen,
    detailTeam,
    isEditMode,
    editTeamName,
    editTeamDescription,
    editAddUserIds,
    isSavingEdit,
    closeDetailPanel,
    enterEditMode,
    exitEditMode,
    setEditTeamName,
    setEditTeamDescription,
    setEditAddUserIds,
    setIsSavingEdit,
    openDetailPanel,
  } = useTeamsStore();

  const [isDeleting, setIsDeleting] = useState(false);
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false);

  // The store closes the panel (browser Back, another team) without touching
  // this local flag, which would otherwise reopen the dialog on the next open.
  useEffect(() => {
    if (!isDetailPanelOpen) setIsDeleteConfirmOpen(false);
  }, [isDetailPanelOpen]);
  const [addMemberRole, setAddMemberRole] = useState<TeamMemberRole>('READER');
  // Pending per-member role updates (Mongo userId; applied on Save as updateUserRoles)
  const [pendingUpdateRoles, setPendingUpdateRoles] = useState<Map<string, TeamMemberRole>>(
    new Map()
  );
  // Per-user role overrides for pending Add Members — fall back to addMemberRole
  const [addMemberRoles, setAddMemberRoles] = useState<Record<string, TeamMemberRole>>({});
  // Cache user metadata for pending adds (survives search/pagination churn)
  const [addUserCache, setAddUserCache] = useState<Record<string, CheckboxOption>>({});
  // Team members list (paginated via PaginatedMembersList component)
  const membersListRef = useRef<PaginatedMembersListHandle | null>(null);
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [existingMemberIds, setExistingMemberIds] = useState<Set<string>>(new Set());
  // Prefer the profile store for identity; auth store user is not always
  // populated after refresh in some routes.
  const normalizedCurrentUserId = (profile?.userId ?? currentUser?.id ?? '').trim();
  const normalizedCurrentUserEmail = (profile?.email ?? currentUser?.email ?? '')
    .trim()
    .toLowerCase();

  const isSameAsCurrentUser = useCallback(
    (member: Pick<TeamMember, 'id' | 'userId' | 'userEmail'>) => {
      const memberEmail = (member.userEmail ?? '').trim().toLowerCase();
      if (normalizedCurrentUserId) {
        if (member.id === normalizedCurrentUserId) return true;
        if (member.userId === normalizedCurrentUserId) return true;
      }
      return Boolean(normalizedCurrentUserEmail && memberEmail === normalizedCurrentUserEmail);
    },
    [normalizedCurrentUserId, normalizedCurrentUserEmail]
  );

  const fetchTeamMembersFn = useCallback(
    async (search: string | undefined, page: number, limit: number) => {
      if (!detailTeam) return { items: [] as TeamMember[], totalCount: 0 };
      const { members, totalCount } = await TeamsApi.getTeamUsers(detailTeam.id, { page, limit, search });
      return { items: members, totalCount };
    },
    [detailTeam]
  );

  // Resolve full member ID set only when Add Members is relevant (edit mode),
  // so we avoid extra fetches while just viewing the panel.
  useEffect(() => {
    if (!isDetailPanelOpen || !detailTeam || !isEditMode) {
      setExistingMemberIds(new Set());
      return;
    }

    let cancelled = false;
    const seed = new Set((detailTeam.members ?? []).map((m) => m.userId));

    const loadAllMemberIds = async () => {
      try {
        const pageSize = 100;
        const first = await TeamsApi.getTeamUsers(detailTeam.id, { page: 1, limit: pageSize });
        first.members.forEach((m) => seed.add(m.userId));

        const totalPages = Math.max(
          1,
          Math.ceil((first.totalCount ?? first.members.length ?? 0) / pageSize)
        );

        if (totalPages > 1) {
          const pages = Array.from({ length: totalPages - 1 }, (_, i) => i + 2);
          const rest = await Promise.all(
            pages.map((page) =>
              TeamsApi.getTeamUsers(detailTeam.id, { page, limit: pageSize })
            )
          );
          for (const res of rest) {
            res.members.forEach((m) => seed.add(m.userId));
          }
        }
      } catch {
        // Keep seeded/current IDs if full fetch fails.
      } finally {
        if (!cancelled) setExistingMemberIds(seed);
      }
    };

    loadAllMemberIds();
    return () => {
      cancelled = true;
    };
  }, [isDetailPanelOpen, detailTeam, isEditMode]);

  // Track member UUIDs marked for removal (deferred until Save Edits)
  const [pendingRemoveUserIds, setPendingRemoveUserIds] = useState<Set<string>>(
    new Set()
  );

  // Reset pending changes when edit mode exits or panel closes
  useEffect(() => {
    if (!isEditMode || !isDetailPanelOpen) {
      setPendingRemoveUserIds(new Set());
      setPendingUpdateRoles(new Map());
      setAddMemberRoles({});
      setAddUserCache({});
      setAddMemberRole('READER');
    }
  }, [isEditMode, isDetailPanelOpen]);

  // ── Paginated user options for add-users dropdown ──
  const {
    options: userOptions,
    isLoading: userFilterLoading,
    hasMore: userFilterHasMore,
    loadedPage: userOptionsLoadedPage,
    onSearch: handleUserSearch,
    onLoadMore: handleUserLoadMore,
  } = usePaginatedUserOptions({
    enabled: isDetailPanelOpen && isEditMode,
    idField: 'userId',
    limit: 50,
      // Service accounts are offered here, each marked, because membership is
    // how one is given anything to read — the create panel tells an
    // administrator to grant access through groups and teams.
    includeServiceAccounts: true,
    serviceAccountBadge: t('workspace.serviceAccounts.memberBadge'),
  });

  // Exclude users who are already on the team (keep pending-add selections in the
  // dropdown so row checkboxes can tick/untick like create-team-form).
  const availableUserOptions: CheckboxOption[] = useMemo(() => {
    if (!detailTeam) return [];
    const memberMongoIds = new Set(existingMemberIds);
    // Keep local page data merged in too (helps before full fetch finishes).
    teamMembers.forEach((m) => memberMongoIds.add(m.userId));

    return userOptions.filter((o) => !memberMongoIds.has(o.id));
  }, [userOptions, teamMembers, existingMemberIds, detailTeam]);

  // Client-side member filtering can leave sparse pages (e.g. "All" team). Keep
  // prefetching org user pages until the addable buffer is full or pages run out.
  useEffect(() => {
    if (
      !isEditMode ||
      !isDetailPanelOpen ||
      userFilterLoading ||
      !userFilterHasMore ||
      availableUserOptions.length >= MIN_ADDABLE_USER_BUFFER ||
      userOptionsLoadedPage >= MAX_ADD_USER_PREFETCH_PAGES
    ) {
      return;
    }
    handleUserLoadMore();
  }, [
    isEditMode,
    isDetailPanelOpen,
    userFilterLoading,
    userFilterHasMore,
    availableUserOptions.length,
    userOptionsLoadedPage,
    handleUserLoadMore,
  ]);

  // Prune pending-add role + cache entries when a user is deselected
  useEffect(() => {
    const idSet = new Set(editAddUserIds);
    setAddMemberRoles((prev) => {
      let changed = false;
      const next: Record<string, TeamMemberRole> = {};
      for (const [id, role] of Object.entries(prev)) {
        if (idSet.has(id)) next[id] = role;
        else changed = true;
      }
      return changed ? next : prev;
    });
    setAddUserCache((prev) => {
      let changed = false;
      const next: Record<string, CheckboxOption> = {};
      for (const [id, opt] of Object.entries(prev)) {
        if (idSet.has(id)) next[id] = opt;
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [editAddUserIds]);

  // Cache metadata for selected-to-add users while visible in userOptions
  useEffect(() => {
    if (userOptions.length === 0 || editAddUserIds.length === 0) return;
    const idSet = new Set(editAddUserIds);
    setAddUserCache((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const opt of userOptions) {
        if (idSet.has(opt.id) && next[opt.id] !== opt) {
          next[opt.id] = opt;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [userOptions, editAddUserIds]);

  // Selected-to-add users, in selection order, with cache fallback
  const selectedAddsOrdered = useMemo(() => {
    const currentById = new Map(userOptions.map((o) => [o.id, o]));
    return editAddUserIds
      .map((id) => currentById.get(id) ?? addUserCache[id])
      .filter((o): o is CheckboxOption => Boolean(o));
  }, [editAddUserIds, userOptions, addUserCache]);

  // Set the role for a single pending-add user
  const setAddUserRole = useCallback(
    (userId: string, role: TeamMemberRole) => {
      setAddMemberRoles((prev) => ({ ...prev, [userId]: role }));
    },
    []
  );

  // Remove a user from the pending-add list (also clears role + cache via prune effect)
  const removeAddUser = useCallback(
    (userId: string) => {
      setEditAddUserIds(editAddUserIds.filter((id) => id !== userId));
    },
    [editAddUserIds, setEditAddUserIds]
  );

  // Lock a newly-selected pending-add user's role to the current addMemberRole
  // (default) at selection time. Later default-role changes don't silently
  // retarget them — only "Apply to all" re-applies the default to users
  // already picked.
  const handleAddSelectionChange = useCallback(
    (ids: string[]) => {
      const prevSet = new Set(editAddUserIds);
      const newlyAdded = ids.filter((id) => !prevSet.has(id));
      if (newlyAdded.length > 0) {
        setAddMemberRoles((prev) => {
          const next = { ...prev };
          for (const id of newlyAdded) {
            if (!(id in next)) next[id] = addMemberRole;
          }
          return next;
        });
      }
      setEditAddUserIds(ids);
    },
    [editAddUserIds, addMemberRole, setEditAddUserIds]
  );

  // Toggle a user for pending removal (deferred — applied on Save Edits)
  const handleRemoveUser = useCallback(
    (memberId: string) => {
      setPendingRemoveUserIds((prev) => {
        const next = new Set(prev);
        if (next.has(memberId)) {
          next.delete(memberId); // Un-mark if clicked again
        } else {
          next.add(memberId);
        }
        return next;
      });
    },
    []
  );

  // Stage a per-member role change (Mongo userId; applied on Save as updateUserRoles)
  const handleMemberRoleChange = useCallback(
    (memberId: string, role: TeamMemberRole, originalRole: TeamMemberRole | string) => {
      setPendingUpdateRoles((prev) => {
        const next = new Map(prev);
        if (role === originalRole) {
          next.delete(memberId);
        } else {
          next.set(memberId, role);
        }
        return next;
      });
    },
    []
  );

  // Apply the "Default role" to every pending-add user only.
  const handleApplyRoleToAll = useCallback(() => {
    setAddMemberRoles((prev) => {
      const next: Record<string, TeamMemberRole> = { ...prev };
      editAddUserIds.forEach((id) => {
        next[id] = addMemberRole;
      });
      return next;
    });
  }, [teamMembers, addMemberRole, editAddUserIds]);

  // Handle deleting the team
  const handleDeleteTeam = useCallback(async () => {
    if (!detailTeam) return;

    setIsDeleting(true);
    try {
      await TeamsApi.deleteTeam(detailTeam.id);

      notices.deleted(detailTeam.name);

      setIsDeleteConfirmOpen(false);
      closeDetailPanel();
      onUpdateSuccess?.();
    } catch {
      notices.deleteFailed();
    } finally {
      setIsDeleting(false);
    }
  }, [detailTeam, closeDetailPanel, onUpdateSuccess, notices]);

  // Handle saving edits
  const handleSaveEdits = useCallback(async () => {
    if (!detailTeam) return;

    setIsSavingEdit(true);
    try {
      // Build addUserRoles: new users with per-user role (falling back to default)
      const addUserRoles: { userId: string; role: TeamMemberRole }[] = [];
      for (const userId of editAddUserIds) {
        addUserRoles.push({ userId, role: addMemberRoles[userId] ?? addMemberRole });
      }

      // Build removeUserIds from pending removals
      const removeUserIds = Array.from(pendingRemoveUserIds);

      // Build updateUserRoles from staged per-member role changes, skipping
      // members that are also being removed in this save.
      const updateUserRoles: { userId: string; role: TeamMemberRole }[] = [];
      pendingUpdateRoles.forEach((role, userId) => {
        if (pendingRemoveUserIds.has(userId)) return;
        updateUserRoles.push({ userId, role });
      });

      await TeamsApi.updateTeam(detailTeam.id, {
        name: editTeamName.trim() || undefined,
        description: editTeamDescription.trim() || undefined,
        addUserRoles: addUserRoles.length > 0 ? addUserRoles : undefined,
        removeUserIds: removeUserIds.length > 0 ? removeUserIds : undefined,
        updateUserRoles: updateUserRoles.length > 0 ? updateUserRoles : undefined,
      });

      // Refresh the team data, members, and re-open detail panel in view mode
      const updatedTeam = await TeamsApi.getTeam(detailTeam.id);
      openDetailPanel(updatedTeam);
      membersListRef.current?.refresh();

      notices.saved(detailTeam.name);
      onUpdateSuccess?.();
    } catch (error) {
      notices.saveFailed(error);
    } finally {
      setIsSavingEdit(false);
    }
  }, [
    detailTeam,
    editTeamName,
    editTeamDescription,
    editAddUserIds,
    addMemberRole,
    addMemberRoles,
    pendingRemoveUserIds,
    pendingUpdateRoles,
    setIsSavingEdit,
    openDetailPanel,
    onUpdateSuccess,
    notices,
  ]);

  const canEditTeam = Boolean(detailTeam?.canEdit);

  // Handle footer action
  const handlePrimaryClick = useCallback(() => {
    if (isEditMode) {
      handleSaveEdits();
    } else if (canEditTeam) {
      enterEditMode();
    }
  }, [isEditMode, canEditTeam, handleSaveEdits, enterEditMode]);

  const handleSecondaryClick = useCallback(() => {
    if (isEditMode) {
      exitEditMode();
    } else {
      closeDetailPanel();
    }
  }, [isEditMode, exitEditMode, closeDetailPanel]);

  const panelTitle = detailTeam?.name || 'Team';

  const creatorUser = detailTeam?.createdByUser ?? null;
  const creatorEmail = (creatorUser?.email ?? '').trim().toLowerCase();
  const isCreatorSelf = Boolean(
    (normalizedCurrentUserId && creatorUser?.userId === normalizedCurrentUserId) ||
    (normalizedCurrentUserEmail && creatorEmail === normalizedCurrentUserEmail)
  );


  return {
    t,
    isDetailPanelOpen,
    detailTeam,
    isEditMode,
    editTeamName,
    editTeamDescription,
    editAddUserIds,
    isSavingEdit,
    closeDetailPanel,
    setEditTeamName,
    setEditTeamDescription,
    isDeleting,
    isDeleteConfirmOpen,
    setIsDeleteConfirmOpen,
    addMemberRole,
    setAddMemberRole,
    pendingUpdateRoles,
    addMemberRoles,
    membersListRef,
    setTeamMembers,
    isSameAsCurrentUser,
    fetchTeamMembersFn,
    pendingRemoveUserIds,
    availableUserOptions,
    userFilterLoading,
    userFilterHasMore,
    handleUserSearch,
    handleUserLoadMore,
    selectedAddsOrdered,
    setAddUserRole,
    removeAddUser,
    handleAddSelectionChange,
    handleRemoveUser,
    handleMemberRoleChange,
    handleApplyRoleToAll,
    handleDeleteTeam,
    handleSaveEdits,
    canEditTeam,
    handlePrimaryClick,
    handleSecondaryClick,
    panelTitle,
    creatorUser,
    isCreatorSelf,
  };
}
