'use client';

import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Box, Flex, Text, Badge, Button } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { useUserStore } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  SearchableCheckboxDropdown,
  AvatarCell,
  PaginatedMembersList,
} from '../../components';
import type { CheckboxOption, PaginatedMembersListHandle } from '../../components';
import { useTeamsStore } from '../store';
import { TeamsApi } from '../api';
import type { TeamMember, TeamMemberRole } from '../types';
import { usePaginatedUserOptions } from '../../hooks/use-paginated-user-options';
import { TEAM_ROLE_LABELS } from '../constants';
import { UsersApi } from '../../users/api';
import { RoleDropdownMenu } from '@/app/components/share';

// ========================================
// Component
// ========================================

export function TeamDetailSidebar({
  onUpdateSuccess,
}: {
  onUpdateSuccess?: () => void;
}) {
  const { t } = useTranslation();
  const currentUser = useAuthStore((s) => s.user);
  const profile = useUserStore((s) => s.profile);
  const addToast = useToastStore((s) => s.addToast);

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
  const [addMemberRole, setAddMemberRole] = useState<TeamMemberRole>('READER');
  // Pending per-member role updates (applied on Save Edits as updateUserRoles)
  const [pendingUpdateRoles, setPendingUpdateRoles] = useState<Map<string, TeamMemberRole>>(
    new Map()
  );
  // Per-user role overrides for pending Add Members — fall back to addMemberRole
  const [addMemberRoles, setAddMemberRoles] = useState<Record<string, TeamMemberRole>>({});
  // Cache user metadata for pending adds (survives search/pagination churn)
  const [addUserCache, setAddUserCache] = useState<Record<string, CheckboxOption>>({});
  // Team members list (paginated via PaginatedMembersList component)
  const membersListRef = useRef<PaginatedMembersListHandle>(null);
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [existingMemberIds, setExistingMemberIds] = useState<Set<string>>(new Set());
  const [creatorUser, setCreatorUser] = useState<{ id: string; name?: string; email?: string } | null>(null);
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
    const seed = new Set((detailTeam.members ?? []).map((m) => m.id));

    const loadAllMemberIds = async () => {
      try {
        const pageSize = 100;
        const first = await TeamsApi.getTeamUsers(detailTeam.id, { page: 1, limit: pageSize });
        first.members.forEach((m) => seed.add(m.id));

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
            res.members.forEach((m) => seed.add(m.id));
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

  // Resolve "Created By" by graph UUID from users API.
  useEffect(() => {
    if (!isDetailPanelOpen || !detailTeam?.createdBy) {
      setCreatorUser(null);
      return;
    }

    let cancelled = false;
    UsersApi.getGraphUsersByIds([detailTeam.createdBy]).then((resolved) => {
      if (cancelled) return;
      const user = resolved[detailTeam.createdBy];
      if (!user) {
        setCreatorUser(null);
        return;
      }
      setCreatorUser({
        id: user.id,
        name: user.name,
        email: user.email,
      });
    });

    return () => {
      cancelled = true;
    };
  }, [isDetailPanelOpen, detailTeam?.createdBy]);

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
    onSearch: handleUserSearch,
    onLoadMore: handleUserLoadMore,
  } = usePaginatedUserOptions({
    enabled: isDetailPanelOpen && isEditMode,
    idField: 'id',
    source: 'graph',
  });

  // Exclude already-added members from the options
  const availableUserOptions: CheckboxOption[] = useMemo(() => {
    if (!detailTeam) return [];
    const memberUuids = new Set(existingMemberIds);
    // Keep local page data merged in too (helps before full fetch finishes).
    teamMembers.forEach((m) => memberUuids.add(m.id));
    // Do not show users already selected in "Add Members".
    const pendingAddIds = new Set(editAddUserIds);

    return userOptions.filter(
      (o) => !memberUuids.has(o.id) && !pendingAddIds.has(o.id)
    );
  }, [userOptions, teamMembers, existingMemberIds, editAddUserIds, detailTeam]);

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

  // Stage a per-member role change (applied on Save Edits as updateUserRoles)
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

      addToast({
        variant: 'success',
        title: t('workspace.teams.edit.deleteSuccess', 'Team deleted'),
        description: t(
          'workspace.teams.edit.deleteSuccessDescription',
          {
            name: detailTeam.name,
            defaultValue: `"${detailTeam.name}" has been deleted`,
          }
        ),
        duration: 3000,
      });

      closeDetailPanel();
      onUpdateSuccess?.();
    } catch {
      addToast({
        variant: 'error',
        title: t(
          'workspace.teams.edit.deleteError',
          'Failed to delete team'
        ),
        duration: 5000,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [detailTeam, closeDetailPanel, onUpdateSuccess, addToast, t]);

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

      addToast({
        variant: 'success',
        title: t('workspace.teams.edit.saveSuccess', 'Team updated!'),
        description: t(
          'workspace.teams.edit.saveSuccessDescription',
          {
            name: detailTeam.name,
            defaultValue: `Changes to '${detailTeam.name}' saved successfully`,
          }
        ),
        duration: 3000,
      });
      onUpdateSuccess?.();
    } catch (error) {
      const message =
        (error as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message
        ?? t('workspace.teams.edit.saveErrorDescription', 'Could not update team. Please try again.');
      addToast({
        variant: 'error',
        title: t('workspace.teams.edit.saveError', 'Failed to update team'),
        description: message,
        duration: 5000,
      });
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
    addToast,
    t,
  ]);

  // Handle footer action
  const handlePrimaryClick = useCallback(() => {
    if (isEditMode) {
      handleSaveEdits();
    } else {
      enterEditMode();
    }
  }, [isEditMode, handleSaveEdits, enterEditMode]);

  const handleSecondaryClick = useCallback(() => {
    if (isEditMode) {
      exitEditMode();
    } else {
      closeDetailPanel();
    }
  }, [isEditMode, exitEditMode, closeDetailPanel]);

  const panelTitle = detailTeam?.name || 'Team';

  const creatorEmail = (creatorUser?.email ?? '').trim().toLowerCase();
  const isCreatorSelf = Boolean(
    (normalizedCurrentUserId && creatorUser?.id === normalizedCurrentUserId) ||
    (normalizedCurrentUserEmail && creatorEmail === normalizedCurrentUserEmail)
  );

  return (
    <WorkspaceRightPanel
      open={isDetailPanelOpen}
      onOpenChange={(open) => {
        if (!open) closeDetailPanel();
      }}
      title={panelTitle}
      icon="groups"
      primaryLabel={
        isEditMode
          ? t('workspace.teams.edit.save', 'Save Edits')
          : t('workspace.teams.edit.edit', 'Edit Team')
      }
      secondaryLabel={t('workspace.teams.edit.cancel', 'Cancel')}
      primaryDisabled={isEditMode && isSavingEdit}
      primaryLoading={isSavingEdit}
      onPrimaryClick={handlePrimaryClick}
      onSecondaryClick={handleSecondaryClick}
    >
      {/* Main card containing form + sections */}
      <Box
        style={{
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-4)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-4)',
        }}
      >
        {/* Team Name */}
        <FormField
          label={t('workspace.teams.detail.nameLabel', 'Team Name')}
        >
          <input
            type="text"
            value={isEditMode ? editTeamName : detailTeam?.name ?? ''}
            onChange={(e) => {
              if (isEditMode) setEditTeamName(e.target.value);
            }}
            readOnly={!isEditMode}
            style={{
              width: '100%',
              height: 'var(--space-8)',
              padding: '6px 8px',
              backgroundColor: 'var(--color-surface)',
              border: '1px solid var(--slate-a5)',
              borderRadius: 'var(--radius-2)',
              fontSize: 14,
              lineHeight: '20px',
              fontFamily: 'var(--default-font-family)',
              color: 'var(--slate-12)',
              outline: 'none',
              boxSizing: 'border-box',
              cursor: isEditMode ? 'text' : 'default',
            }}
            onFocus={(e) => {
              if (isEditMode) {
                e.currentTarget.style.border = '2px solid var(--accent-8)';
                e.currentTarget.style.padding = '5px 7px';
              }
            }}
            onBlur={(e) => {
              e.currentTarget.style.border = '1px solid var(--slate-a5)';
              e.currentTarget.style.padding = '6px 8px';
            }}
          />
        </FormField>

        {/* Team Description */}
        <FormField
          label={t(
            'workspace.teams.detail.descriptionLabel',
            'Team Description'
          )}
        >
          <textarea
            value={
              isEditMode
                ? editTeamDescription
                : detailTeam?.description ?? ''
            }
            onChange={(e) => {
              if (isEditMode) setEditTeamDescription(e.target.value);
            }}
            readOnly={!isEditMode}
            placeholder={
              isEditMode
                ? t(
                    'workspace.teams.detail.descriptionPlaceholder',
                    'Describe the purpose of this team'
                  )
                : ''
            }
            rows={4}
            style={{
              width: '100%',
              minHeight: 88,
              padding: 'var(--space-2)',
              backgroundColor: 'var(--color-surface)',
              border: '1px solid var(--slate-a5)',
              borderRadius: 'var(--radius-2)',
              fontSize: 14,
              lineHeight: '20px',
              fontFamily: 'var(--default-font-family)',
              color: 'var(--slate-12)',
              outline: 'none',
              boxSizing: 'border-box',
              resize: 'vertical',
              cursor: isEditMode ? 'text' : 'default',
            }}
            onFocus={(e) => {
              if (isEditMode) {
                e.currentTarget.style.border = '2px solid var(--accent-8)';
                e.currentTarget.style.padding = '7px';
              }
            }}
            onBlur={(e) => {
              e.currentTarget.style.border = '1px solid var(--slate-a5)';
              e.currentTarget.style.padding = '8px';
            }}
          />
        </FormField>

        {/* Created By section box */}
        <Box
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-4)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-4)',
          }}
        >
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--slate-12)' }}
          >
            {t('workspace.teams.detail.createdBy', 'Created By')}
          </Text>
          {creatorUser ? (
            <AvatarCell
              name={creatorUser.name || creatorUser.email || 'Unknown User'}
              email={creatorUser.email}
              avatarSize={32}
              isSelf={isCreatorSelf}
            />
          ) : (
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              -
            </Text>
          )}
        </Box>

        {/* Members section box */}
        <Box
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-4)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-4)',
          }}
        >
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--slate-12)' }}
          >
            {t('workspace.teams.detail.members', 'Members')}
          </Text>

          <PaginatedMembersList<TeamMember>
            key={detailTeam?.id}
            ref={membersListRef}
            fetcher={fetchTeamMembersFn}
            keyExtractor={(m) => m.id}
            searchPlaceholder={t('workspace.teams.detail.searchMembers', 'Search members...')}
            emptyText={t('workspace.teams.detail.noMembers', 'No members in this team')}
            maxHeight={320}
            listClassName="team-member-scroll-area"
            onFetched={(items) => setTeamMembers(items)}
            renderItem={(member) => {
              const isPendingRemove = pendingRemoveUserIds.has(member.id);
              const effectiveRole = (pendingUpdateRoles.get(member.id) ?? member.role) as TeamMemberRole | string;
              const isCurrentMemberUser = isSameAsCurrentUser(member);
              const canEditRole = isEditMode && !isCurrentMemberUser && !isPendingRemove;
              return (
                <Flex
                  align="center"
                  justify="between"
                  gap="2"
                  style={{
                    opacity: isPendingRemove ? 0.5 : 1,
                    transition: 'opacity 0.15s ease',
                  }}
                >
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    <AvatarCell
                      name={member.userName || member.userEmail || 'Unknown'}
                      email={member.userEmail}
                      avatarSize={28}
                      isSelf={isCurrentMemberUser}
                      profilePicture={member.profilePicture}
                    />
                  </Box>
                  <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
                    {canEditRole ? (
                      <RoleDropdownMenu
                        role={effectiveRole as TeamMemberRole}
                        onRoleChange={(r) =>
                          handleMemberRoleChange(
                            member.id,
                            r as TeamMemberRole,
                            member.role as TeamMemberRole
                          )
                        }
                        labels={TEAM_ROLE_LABELS}
                      />
                    ) : (
                      <Badge variant="soft" color="gray" size="1">
                        {effectiveRole}
                      </Badge>
                    )}
                    {isEditMode && !isCurrentMemberUser && (
                      <Text
                        size="1"
                        onClick={() => handleRemoveUser(member.id)}
                        style={{
                          color: isPendingRemove
                            ? 'var(--accent-11)'
                            : 'var(--red-11)',
                          cursor: 'pointer',
                          fontWeight: 500,
                          paddingLeft: 4,
                        }}
                      >
                        {isPendingRemove
                          ? t('workspace.teams.edit.undo', 'Undo')
                          : t('workspace.teams.edit.remove', 'Remove')}
                      </Text>
                    )}
                  </Flex>
                </Flex>
              );
            }}
          />
        </Box>

        {/* Add Members section box (edit mode only) */}
        {isEditMode && (
          <Box
            style={{
              backgroundColor: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-4)',
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-2)',
            }}
          >
            <Flex align="center" justify="between">
              <Text
                size="2"
                weight="medium"
                style={{ color: 'var(--slate-12)' }}
              >
                {t('workspace.teams.edit.addUsersLabel', 'Add Members')}
              </Text>
              <Badge variant="soft" color="gray" size="1">
                {t('workspace.common.selected', { count: editAddUserIds.length, defaultValue: '{{count}} Selected' })}
              </Badge>
            </Flex>

            {/* Compact inline: Default Role + Apply to all — sits above the
                search so the dropdown expansion doesn't push these controls
                out of view. */}
            <Flex align="center" justify="between" gap="2" wrap="wrap">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.teams.detail.roleLabel', 'Default Role')}
              </Text>
              <Flex align="center" gap="2">
                <RoleDropdownMenu
                  role={addMemberRole}
                  onRoleChange={(r) => setAddMemberRole(r as TeamMemberRole)}
                  labels={TEAM_ROLE_LABELS}
                />
                <Button
                  variant="outline"
                  color="gray"
                  size="1"
                  onClick={handleApplyRoleToAll}
                  disabled={editAddUserIds.length === 0}
                >
                  {t('workspace.teams.edit.applyToAll', 'Apply to all')}
                </Button>
              </Flex>
            </Flex>

            <SearchableCheckboxDropdown
              options={availableUserOptions}
              selectedIds={editAddUserIds}
              onSelectionChange={handleAddSelectionChange}
              placeholder={t(
                'workspace.teams.edit.addUsersPlaceholder',
                'Search or select user(s) to add to this team'
              )}
              emptyText={t('workspace.common.noUsersAvailable', 'No users available')}
              showAvatar
              onSearch={handleUserSearch}
              onLoadMore={handleUserLoadMore}
              isLoadingMore={userFilterLoading}
              hasMore={userFilterHasMore}
            />

            {/* Per-row role for pending-add users */}
            {selectedAddsOrdered.length > 0 && (
              <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-2)' }}>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.teams.edit.newMemberRoles', 'New Member Roles')}
                </Text>
                <Flex
                  direction="column"
                  gap="1"
                  className="team-member-scroll-area"
                  style={{ maxHeight: 220, overflowY: 'auto', paddingRight: 4 }}
                >
                  {selectedAddsOrdered.map((user) => {
                    const currentRole = addMemberRoles[user.id] ?? addMemberRole;
                    return (
                      <Flex
                        key={user.id}
                        align="center"
                        justify="between"
                        gap="2"
                        style={{ padding: '2px 0' }}
                      >
                        <Box style={{ flex: 1, minWidth: 0 }}>
                          <AvatarCell
                            name={user.label}
                            email={user.subtitle}
                            avatarSize={28}
                            profilePicture={user.profilePicture}
                          />
                        </Box>
                        <Box style={{ flexShrink: 0 }}>
                          <RoleDropdownMenu
                            role={currentRole}
                            onRoleChange={(r) => setAddUserRole(user.id, r as TeamMemberRole)}
                            onRemove={() => removeAddUser(user.id)}
                            labels={TEAM_ROLE_LABELS}
                          />
                        </Box>
                      </Flex>
                    );
                  })}
                </Flex>
              </Flex>
            )}
          </Box>
        )}
      </Box>

      {/* Delete Team section (edit mode only) — separate box */}
      {isEditMode && detailTeam?.canDelete && (
        <Box
          style={{
            marginTop: 'var(--space-4)',
            padding: 'var(--space-4)',
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
          }}
        >
          <Flex align="center" justify="between">
            <Flex direction="column" gap="1">
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.teams.edit.deleteTitle', {
                  name: detailTeam?.name,
                  defaultValue: `Delete '${detailTeam?.name}' Team`,
                })}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-10)' }}>
                {t(
                  'workspace.teams.edit.deleteDescription',
                  'Permanently remove this team from the workspace'
                )}
              </Text>
            </Flex>
            <LoadingButton
              variant="outline"
              color="red"
              size="1"
              onClick={handleDeleteTeam}
              loading={isDeleting}
              loadingLabel={t('workspace.teams.edit.deleting', 'Deleting...')}
              style={{ flexShrink: 0 }}
            >
              {t('workspace.teams.edit.deleteButton', 'Delete Team')}
            </LoadingButton>
          </Flex>
        </Box>
      )}
    </WorkspaceRightPanel>
  );
}
