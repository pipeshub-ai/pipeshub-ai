'use client';

import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Box, Flex, Text, Badge, Button } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  SearchableCheckboxDropdown,
  AvatarCell,
  SelectDropdown,
  PaginatedMembersList,
} from '../../components';
import type { CheckboxOption, PaginatedMembersListHandle } from '../../components';
import { useTeamsStore } from '../store';
import { TeamsApi } from '../api';
import type { TeamMember, TeamMemberRole } from '../types';
import { usePaginatedUserOptions } from '../../hooks/use-paginated-user-options';
import { ROLE_OPTIONS } from '../constants';

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
  // Team members list (paginated via PaginatedMembersList component)
  const membersListRef = useRef<PaginatedMembersListHandle>(null);
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);

  const fetchTeamMembersFn = useCallback(
    async (search: string | undefined, page: number, limit: number) => {
      if (!detailTeam) return { items: [] as TeamMember[], totalCount: 0 };
      const { members, totalCount } = await TeamsApi.getTeamUsers(detailTeam.id, { page, limit, search });
      return { items: members, totalCount };
    },
    [detailTeam]
  );

  // Track member UUIDs marked for removal (deferred until Save Edits)
  const [pendingRemoveUserIds, setPendingRemoveUserIds] = useState<Set<string>>(
    new Set()
  );

  // Reset pending removals and role when edit mode changes or panel closes
  useEffect(() => {
    if (!isEditMode || !isDetailPanelOpen) {
      setPendingRemoveUserIds(new Set());
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
    const memberUuids = new Set(teamMembers.map((m) => m.id));
    return userOptions.filter((o) => !memberUuids.has(o.id));
  }, [userOptions, teamMembers, detailTeam]);

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
      // Build addUserRoles: new users with selected role
      const addUserRoles: { userId: string; role: TeamMemberRole }[] = [];
      for (const userId of editAddUserIds) {
        addUserRoles.push({ userId, role: addMemberRole });
      }

      // Build removeUserIds from pending removals
      const removeUserIds = Array.from(pendingRemoveUserIds);

      await TeamsApi.updateTeam(detailTeam.id, {
        name: editTeamName.trim() || undefined,
        description: editTeamDescription.trim() || undefined,
        addUserRoles: addUserRoles.length > 0 ? addUserRoles : undefined,
        removeUserIds: removeUserIds.length > 0 ? removeUserIds : undefined,
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
    } catch {
      addToast({
        variant: 'error',
        title: t(
          'workspace.teams.edit.saveError',
          'Failed to update team'
        ),
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
    pendingRemoveUserIds,
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

  // Find the creator member from fetched team members
  const creatorMember = useMemo(() => {
    if (!detailTeam?.createdBy || !teamMembers.length) return null;
    return teamMembers.find((m) => m.id === detailTeam.createdBy) ?? null;
  }, [detailTeam, teamMembers]);

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
          {creatorMember ? (
            <AvatarCell
              name={creatorMember.userName}
              email={creatorMember.userEmail}
              avatarSize={32}
              isSelf={currentUser?.id === creatorMember.id}
              profilePicture={creatorMember.profilePicture}
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
            onFetched={(items) => setTeamMembers(items)}
            renderItem={(member) => {
              const isPendingRemove = pendingRemoveUserIds.has(member.id);
              return (
                <Flex
                  align="center"
                  justify="between"
                  style={{
                    opacity: isPendingRemove ? 0.5 : 1,
                    transition: 'opacity 0.15s ease',
                  }}
                >
                  <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
                    <AvatarCell
                      name={member.userName || member.userEmail || 'Unknown'}
                      email={member.userEmail}
                      avatarSize={32}
                      isSelf={currentUser?.id === member.id}
                      profilePicture={member.profilePicture}
                    />
                    <Badge variant="soft" color="gray" size="1" style={{ flexShrink: 0 }}>
                      {member.role}
                    </Badge>
                  </Flex>
                  {isEditMode && !member.isOwner && (
                    <Text
                      size="1"
                      onClick={() => handleRemoveUser(member.id)}
                      style={{
                        color: isPendingRemove
                          ? 'var(--accent-11)'
                          : 'var(--red-11)',
                        cursor: 'pointer',
                        flexShrink: 0,
                        fontWeight: 500,
                        marginLeft: 8,
                      }}
                    >
                      {isPendingRemove
                        ? t('workspace.teams.edit.undo', 'Undo')
                        : t('workspace.teams.edit.remove', 'Remove')}
                    </Text>
                  )}
                </Flex>
              );
            }}
          />
        </Box>

        {/* Role section (edit mode only) */}
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
            <FormField label={t('workspace.teams.detail.roleLabel', 'Role')}>
              <SelectDropdown
                value={addMemberRole}
                onChange={(val) => setAddMemberRole(val as TeamMemberRole)}
                options={ROLE_OPTIONS}
              />
            </FormField>
          </Box>
        )}

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

            <SearchableCheckboxDropdown
              options={availableUserOptions}
              selectedIds={editAddUserIds}
              onSelectionChange={setEditAddUserIds}
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
