'use client';

import React, { useEffect, useCallback } from 'react';
import { Box, Flex, Text, Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  LabeledTextField,
  SearchableCheckboxDropdown,
  useFormValidation,
} from '../../components';
import { workspaceNameProblem } from '../../name-rules';
import { fieldErrorsOf } from '@/lib/api/api-error';
import { useGroupsStore } from '../store';
import { GroupsApi } from '../api';
import { groupSaveRefusalMessage } from '../save-error';
import { usePaginatedUserOptions } from '../../hooks/use-paginated-user-options';

// ========================================
// Component
// ========================================

export function CreateGroupSidebar({
  onCreateSuccess,
}: {
  onCreateSuccess?: () => void;
}) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const {
    isCreatePanelOpen,
    createGroupName,
    createGroupUserIds,
    isCreating,
    closeCreatePanel,
    setCreateGroupName,
    setCreateGroupUserIds,
    setIsCreating,
    resetCreateForm,
  } = useGroupsStore();

  // Reset form when panel closes
  useEffect(() => {
    if (!isCreatePanelOpen) {
      resetCreateForm();
    }
  }, [isCreatePanelOpen, resetCreateForm]);

  // ── Paginated user options for add-users dropdown ──
  const {
    options: userOptions,
    isLoading: userFilterLoading,
    hasMore: userFilterHasMore,
    onSearch: handleUserSearch,
    onLoadMore: handleUserLoadMore,
  } = usePaginatedUserOptions({
    enabled: isCreatePanelOpen,
    idField: 'userId',
      // Service accounts are offered here, each marked, because membership is
    // how one is given anything to read — the create panel tells an
    // administrator to grant access through groups and teams.
    includeServiceAccounts: true,
    serviceAccountBadge: t('workspace.serviceAccounts.memberBadge'),
  });

  // Same rule as a team name: required, at most 100 characters.
  const validation = useFormValidation<'name'>({
    name: workspaceNameProblem(createGroupName, t),
  });
  const isFormValid = validation.isValid;
  const resetValidation = validation.reset;
  useEffect(() => {
    if (!isCreatePanelOpen) resetValidation();
  }, [isCreatePanelOpen, resetValidation]);

  // Handle submit
  const handleSubmit = useCallback(async () => {
    if (!isFormValid) return;

    setIsCreating(true);
    try {
      // Step 1: Create the group
      const newGroup = await GroupsApi.createGroup(createGroupName.trim());

      // Step 2: Add users if any were selected
      if (createGroupUserIds.length > 0) {
        await GroupsApi.addUsersToGroups(createGroupUserIds, [newGroup._id]);
      }

      // Show success toast
      addToast({
        variant: 'success',
        title: t('workspace.groups.create.successTitle', 'Group created!'),
        description: t(
          'workspace.groups.create.successDescription',
          {
            name: newGroup.name,
            defaultValue: `"${newGroup.name}" has been created successfully`,
          }
        ),
        duration: 3000,
      });

      // Close panel and refresh parent list
      closeCreatePanel();
      onCreateSuccess?.();
    } catch (error) {
      // A reason the server pinned to the name shows on the field, not as a toast.
      const onField = validation.applyServerErrors(fieldErrorsOf(error), ['name']);
      if (!onField) {
        addToast({
          variant: 'error',
          title: t(
            'workspace.groups.create.errorGeneric',
            'Failed to create group'
          ),
          description: groupSaveRefusalMessage(error),
          duration: 5000,
        });
      }
    } finally {
      setIsCreating(false);
    }
  }, [
    isFormValid,
    validation.applyServerErrors,
    createGroupName,
    createGroupUserIds,
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
      title={t('workspace.groups.create.title', 'Create group')}
      icon="group"
      primaryLabel={t('workspace.groups.create.submit', 'Create group')}
      secondaryLabel={t('workspace.groups.create.cancel', 'Cancel')}
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
          gap: 'var(--space-5)',
        }}
      >
        {/* Group Name */}
        <FormField
          label={t('workspace.groups.create.nameLabel', 'Group Name')}
          required
          error={validation.errorFor('name')}
        >
          <LabeledTextField
            value={createGroupName}
            onChange={(e) => {
              setCreateGroupName(e.target.value);
              validation.clearServerError('name');
            }}
            onBlur={() => validation.touch('name')}
            placeholder={t(
              'workspace.groups.create.namePlaceholder',
              'e.g. Data Engineering'
            )}
          />
        </FormField>

        {/* Add Users */}
        <Flex direction="column" gap="1">
          <Flex align="center" justify="between">
            <Text
              size="2"
              weight="medium"
              style={{ color: 'var(--slate-12)' }}
            >
              {t('workspace.groups.create.addUsersLabel', 'Add Users')}
            </Text>
            <Badge variant="soft" color="gray" size="1">
              {t('workspace.common.selected', { count: createGroupUserIds.length, defaultValue: '{{count}} Selected' })}
            </Badge>
          </Flex>
          <SearchableCheckboxDropdown
            options={userOptions}
            selectedIds={createGroupUserIds}
            onSelectionChange={setCreateGroupUserIds}
            placeholder={t(
              'workspace.groups.create.addUsersPlaceholder',
              'Search or select user(s) to add to this group'
            )}
            emptyText={t('workspace.common.noUsersAvailable', 'No users available')}
            showAvatar
            onSearch={handleUserSearch}
            onLoadMore={handleUserLoadMore}
            isLoadingMore={userFilterLoading}
            hasMore={userFilterHasMore}
          />
        </Flex>

        {/* Access Permissions (coming soon) */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.groups.create.accessLabel', 'Access permissions')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {t(
              'workspace.groups.create.accessComingSoon',
              'Access permissions coming soon'
            )}
          </Text>
        </Flex>
      </Box>
    </WorkspaceRightPanel>
  );
}
