'use client';

import React, { useEffect, useCallback, useMemo, useState } from 'react';
import { Box, Flex, Text, Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  SearchableCheckboxDropdown,
  SelectDropdown,
} from '../../components';
import type { CheckboxOption } from '../../components';
import { useTeamsStore } from '../store';
import { TeamsApi } from '../api';
import { UsersApi } from '../../users/api';
import { useUsersStore } from '../../users/store';
import { ROLE_OPTIONS } from '../constants';
import type { TeamMemberRole } from '../types';

// ========================================
// Component
// ========================================

export function CreateTeamSidebar({
  onCreateSuccess,
}: {
  onCreateSuccess?: () => void;
}) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const {
    isCreatePanelOpen,
    createTeamName,
    createTeamDescription,
    createTeamUserIds,
    isCreating,
    closeCreatePanel,
    setCreateTeamName,
    setCreateTeamDescription,
    setCreateTeamUserIds,
    setIsCreating,
    resetCreateForm,
  } = useTeamsStore();

  // Role for all added members
  const [memberRole, setMemberRole] = useState<TeamMemberRole>('READER');

  // Shared users cache from the users store
  const allUsers = useUsersStore((s) => s.allUsers);
  const isLoadingAllUsers = useUsersStore((s) => s.isLoadingAllUsers);
  const setAllUsers = useUsersStore((s) => s.setAllUsers);
  const setIsLoadingAllUsers = useUsersStore((s) => s.setIsLoadingAllUsers);

  // Reset form when panel closes
  useEffect(() => {
    if (!isCreatePanelOpen) {
      resetCreateForm();
    }
  }, [isCreatePanelOpen, resetCreateForm]);

  // Load all users into the shared store when panel opens (skip if already loaded)
  useEffect(() => {
    if (!isCreatePanelOpen) return;
    if (allUsers.length > 0) return; // already cached

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
    return () => {
      cancelled = true;
    };
  }, [isCreatePanelOpen, allUsers.length, setAllUsers, setIsLoadingAllUsers]);

  // User options for the dropdown — uses user UUID (id) since teams API expects UUIDs
  const userOptions: CheckboxOption[] = useMemo(
    () =>
      allUsers.map((u) => ({
        id: u.id,
        label: u.name || u.email || 'Unknown User',
        subtitle: u.email,
      })),
    [allUsers]
  );

  // Form validation
  const isFormValid = createTeamName.trim().length > 0;

  // Handle submit
  const handleSubmit = useCallback(async () => {
    if (!isFormValid) return;

    setIsCreating(true);
    try {
      const newTeam = await TeamsApi.createTeam({
        name: createTeamName.trim(),
        description: createTeamDescription.trim() || undefined,
        userRoles: createTeamUserIds.map((userId) => ({
          userId,
          role: memberRole,
        })),
      });

      addToast({
        variant: 'success',
        title: t('workspace.teams.create.successTitle', 'Team created!'),
        description: t(
          'workspace.teams.create.successDescription',
          {
            name: newTeam.name,
            defaultValue: `"${newTeam.name}" has been created successfully`,
          }
        ),
        duration: 3000,
      });

      closeCreatePanel();
      onCreateSuccess?.();
    } catch {
      addToast({
        variant: 'error',
        title: t(
          'workspace.teams.create.errorGeneric',
          'Failed to create team'
        ),
        duration: 5000,
      });
    } finally {
      setIsCreating(false);
    }
  }, [
    isFormValid,
    createTeamName,
    createTeamDescription,
    createTeamUserIds,
    memberRole,
    setIsCreating,
    closeCreatePanel,
    onCreateSuccess,
    addToast,
    t,
  ]);

  return (
    <WorkspaceRightPanel
      open={isCreatePanelOpen}
      onOpenChange={(open) => {
        if (!open) closeCreatePanel();
      }}
      title={t('workspace.teams.create.title', 'Create Team')}
      icon="groups"
      primaryLabel={t('workspace.teams.create.submit', 'Create Team')}
      secondaryLabel={t('workspace.teams.create.cancel', 'Cancel')}
      primaryDisabled={!isFormValid}
      primaryLoading={isCreating}
      onPrimaryClick={handleSubmit}
    >
      {/* Form card */}
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
          label={t('workspace.teams.create.nameLabel', 'Team Name')}
        >
          <input
            type="text"
            value={createTeamName}
            onChange={(e) => setCreateTeamName(e.target.value)}
            placeholder={t(
              'workspace.teams.create.namePlaceholder',
              'e.g. Product Engineering'
            )}
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
            }}
            onFocus={(e) => {
              e.currentTarget.style.border = '2px solid var(--accent-8)';
              e.currentTarget.style.padding = '5px 7px';
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
            'workspace.teams.create.descriptionLabel',
            'Team Description'
          )}
        >
          <textarea
            value={createTeamDescription}
            onChange={(e) => setCreateTeamDescription(e.target.value)}
            placeholder={t(
              'workspace.teams.create.descriptionPlaceholder',
              'Describe the purpose of this team'
            )}
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
            }}
            onFocus={(e) => {
              e.currentTarget.style.border = '2px solid var(--accent-8)';
              e.currentTarget.style.padding = '7px';
            }}
            onBlur={(e) => {
              e.currentTarget.style.border = '1px solid var(--slate-a5)';
              e.currentTarget.style.padding = '8px';
            }}
          />
        </FormField>

        {/* Add Members */}
        <Flex direction="column" gap="1">
          <Flex align="center" justify="between">
            <Text
              size="2"
              weight="medium"
              style={{ color: 'var(--slate-12)' }}
            >
              {t('workspace.teams.create.addUsersLabel', 'Add Members')}
            </Text>
            <Badge variant="soft" color="gray" size="1">
              {t('workspace.common.selected', { count: createTeamUserIds.length, defaultValue: '{{count}} Selected' })}
            </Badge>
          </Flex>
          <SearchableCheckboxDropdown
            options={userOptions}
            selectedIds={createTeamUserIds}
            onSelectionChange={setCreateTeamUserIds}
            placeholder={t(
              'workspace.teams.create.addUsersPlaceholder',
              'Search or select user(s) to add to this team'
            )}
            emptyText={
              isLoadingAllUsers
                ? t('workspace.common.loadingUsers', 'Loading users...')
                : t('workspace.common.noUsersAvailable', 'No users available')
            }
            showAvatar
          />
        </Flex>

        {/* Role */}
        <FormField label={t('workspace.teams.create.roleLabel', 'Role')}>
          <SelectDropdown
            value={memberRole}
            onChange={(val) => setMemberRole(val as TeamMemberRole)}
            options={ROLE_OPTIONS}
          />
        </FormField>
      </Box>
    </WorkspaceRightPanel>
  );
}
