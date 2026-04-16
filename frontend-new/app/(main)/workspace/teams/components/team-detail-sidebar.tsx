'use client';

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { Box, Flex, Text, Badge, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  SearchableCheckboxDropdown,
  AvatarCell,
  SelectDropdown,
} from '../../components';
import type { CheckboxOption } from '../../components';
import { useTeamsStore } from '../store';
import { TeamsApi } from '../api';
import { UsersApi } from '../../users/api';
import { useUsersStore } from '../../users/store';
import type { TeamMember, TeamMemberRole } from '../types';
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

  // Shared users cache from the users store
  const allUsers = useUsersStore((s) => s.allUsers);
  const isLoadingAllUsers = useUsersStore((s) => s.isLoadingAllUsers);
  const setAllUsers = useUsersStore((s) => s.setAllUsers);
  const setIsLoadingAllUsers = useUsersStore((s) => s.setIsLoadingAllUsers);

  const [isDeleting, setIsDeleting] = useState(false);
  const [addMemberRole, setAddMemberRole] = useState<TeamMemberRole>('READER');
  // Team members fetched via API (with profile pictures)
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [isLoadingMembers, setIsLoadingMembers] = useState(false);

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

  // Fetch team members with profile pictures when panel opens
  const fetchTeamMembers = useCallback(async () => {
    if (!detailTeam) return;
    setIsLoadingMembers(true);
    try {
      const { members } = await TeamsApi.getTeamUsers(detailTeam.id, { limit: 100 });
      setTeamMembers(members);
    } catch {
      // handled by global interceptor
    } finally {
      setIsLoadingMembers(false);
    }
  }, [detailTeam]);

  useEffect(() => {
    if (!isDetailPanelOpen || !detailTeam) {
      setTeamMembers([]);
      return;
    }
    fetchTeamMembers();
  }, [isDetailPanelOpen, detailTeam?.id]);

  // Load all users for the add-users dropdown (edit mode only)
  useEffect(() => {
    if (!isDetailPanelOpen || !isEditMode) return;
    if (allUsers.length > 0) return;

    let cancelled = false;
    const load = async () => {
      setIsLoadingAllUsers(true);
      try {
        const { users: mergedUsers } = await UsersApi.fetchMergedUsers({
          limit: 100,
        });
        if (!cancelled) setAllUsers(mergedUsers);
      } catch {
        // handled by global interceptor
      } finally {
        if (!cancelled) setIsLoadingAllUsers(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [isDetailPanelOpen, isEditMode, allUsers.length, setAllUsers, setIsLoadingAllUsers]);

  // User options for add-users dropdown (exclude already-added members by UUID)
  const availableUserOptions: CheckboxOption[] = useMemo(() => {
    if (!detailTeam) return [];
    const memberUuids = new Set(teamMembers.map((m) => m.id));

    return allUsers
      .filter((u) => !memberUuids.has(u.id))
      .map((u) => ({
        id: u.id,
        label: u.name || u.email || 'Unknown User',
        subtitle: u.email,
      }));
  }, [allUsers, teamMembers, detailTeam]);

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
      // Build updateUserRoles: new users with selected role
      const updateUserRoles: { userId: string; role: TeamMemberRole }[] = [];

      // Add newly selected users with chosen role
      for (const userId of editAddUserIds) {
        updateUserRoles.push({ userId, role: addMemberRole });
      }

      await TeamsApi.updateTeam(detailTeam.id, {
        name: editTeamName.trim() || undefined,
        description: editTeamDescription.trim() || undefined,
        updateUserRoles: updateUserRoles.length > 0 ? updateUserRoles : undefined,
      });

      // Refresh the team data, members, and re-open detail panel in view mode
      const updatedTeam = await TeamsApi.getTeam(detailTeam.id);
      openDetailPanel(updatedTeam);
      await fetchTeamMembers();

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
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
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
              height: 32,
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
              padding: '8px',
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
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
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
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          <Flex align="center" justify="between">
            <Text
              size="2"
              weight="medium"
              style={{ color: 'var(--slate-12)' }}
            >
              {t('workspace.teams.detail.members', 'Members')}
            </Text>
            <Badge variant="soft" color="gray" size="1">
              {teamMembers.length}
            </Badge>
          </Flex>

          {teamMembers.length === 0 && !isLoadingMembers ? (
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.teams.detail.noMembers', 'No members in this team')}
            </Text>
          ) : (
            <Flex direction="column" gap="3">
              {teamMembers.map((member) => {
                const isPendingRemove = pendingRemoveUserIds.has(member.id);
                return (
                  <Flex
                    key={member.id}
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
              })}
            </Flex>
          )}
        </Box>

        {/* Role section (edit mode only) */}
        {isEditMode && (
          <Box
            style={{
              backgroundColor: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              padding: 16,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
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
              padding: 16,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
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
              emptyText={
                isLoadingAllUsers
                  ? t('workspace.common.loadingUsers', 'Loading users...')
                  : t('workspace.common.noUsersAvailable', 'No users available')
              }
              showAvatar
            />
          </Box>
        )}
      </Box>

      {/* Delete Team section (edit mode only) — separate box */}
      {isEditMode && detailTeam?.canDelete && (
        <Box
          style={{
            marginTop: 16,
            padding: 16,
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
            <Button
              variant="outline"
              color="red"
              size="1"
              onClick={handleDeleteTeam}
              disabled={isDeleting}
              style={{
                cursor: isDeleting ? 'not-allowed' : 'pointer',
                flexShrink: 0,
              }}
            >
              {isDeleting
                ? t('workspace.teams.edit.deleting', 'Deleting...')
                : t('workspace.teams.edit.deleteButton', 'Delete Team')}
            </Button>
          </Flex>
        </Box>
      )}
    </WorkspaceRightPanel>
  );
}
