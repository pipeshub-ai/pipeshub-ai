'use client';

import React, { useEffect, useCallback } from 'react';
import { Box, Flex, Text, Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  SearchableCheckboxDropdown,
} from '../../components';
import { useGroupsStore } from '../store';
import { GroupsApi } from '../api';
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
    createGroupDescription,
    createGroupUserIds,
    isCreating,
    closeCreatePanel,
    setCreateGroupName,
    setCreateGroupDescription,
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
  });

  // Form validation
  const isFormValid = createGroupName.trim().length > 0;

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
    } catch {
      addToast({
        variant: 'error',
        title: t(
          'workspace.groups.create.errorGeneric',
          'Failed to create group'
        ),
        duration: 5000,
      });
    } finally {
      setIsCreating(false);
    }
  }, [
    isFormValid,
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
      title={t('workspace.groups.create.title', 'Create Group')}
      icon="group"
      primaryLabel={t('workspace.groups.create.submit', 'Create Group')}
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
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        {/* Group Name */}
        <FormField
          label={t('workspace.groups.create.nameLabel', 'Group Name')}
        >
          <input
            type="text"
            value={createGroupName}
            onChange={(e) => setCreateGroupName(e.target.value)}
            placeholder={t(
              'workspace.groups.create.namePlaceholder',
              'e.g. Data Engineering'
            )}
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

        {/* Group Description */}
        <FormField
          label={t(
            'workspace.groups.create.descriptionLabel',
            'Group Description'
          )}
        >
          <textarea
            value={createGroupDescription}
            onChange={(e) => setCreateGroupDescription(e.target.value)}
            placeholder={t(
              'workspace.groups.create.descriptionPlaceholder',
              'Describe the purpose of this group'
            )}
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
            {t('workspace.groups.create.accessLabel', 'Access Permissions')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {t(
              'workspace.groups.create.accessComingSoon',
              'Access Permissions Coming Soon'
            )}
          </Text>
        </Flex>
      </Box>
    </WorkspaceRightPanel>
  );
}
