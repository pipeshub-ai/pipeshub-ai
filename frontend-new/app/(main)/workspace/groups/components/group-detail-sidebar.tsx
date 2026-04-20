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
  PaginatedMembersList,
} from '../../components';
import type { CheckboxOption, PaginatedMembersListHandle } from '../../components';
import { useGroupsStore } from '../store';
import { GroupsApi } from '../api';
import type { GroupUser } from '../types';
import { usePaginatedUserOptions } from '../../hooks/use-paginated-user-options';

// ========================================
// Component
// ========================================

export function GroupDetailSidebar({
  onUpdateSuccess,
}: {
  onUpdateSuccess?: () => void;
}) {
  const { t } = useTranslation();
  const currentUser = useAuthStore((s) => s.user);
  const addToast = useToastStore((s) => s.addToast);

  const {
    isDetailPanelOpen,
    detailGroup,
    isEditMode,
    editGroupName,
    editGroupDescription,
    editAddUserIds,
    isSavingEdit,
    closeDetailPanel,
    enterEditMode,
    exitEditMode,
    setEditGroupName,
    setEditGroupDescription,
    setEditAddUserIds,
    setIsSavingEdit,
    setDetailGroup,
  } = useGroupsStore();

  const [isDeleting, setIsDeleting] = useState(false);
  // Group members list (paginated via PaginatedMembersList component)
  const membersListRef = useRef<PaginatedMembersListHandle>(null);
  const [groupMembers, setGroupMembers] = useState<GroupUser[]>([]);

  const fetchGroupMembersFn = useCallback(
    async (search: string | undefined, page: number, limit: number) => {
      if (!detailGroup) return { items: [] as GroupUser[], totalCount: 0 };
      const { users, totalCount } = await GroupsApi.getGroupUsers(detailGroup._id, { page, limit, search });
      return { items: users, totalCount };
    },
    [detailGroup]
  );

  // Track user IDs marked for removal (deferred until Save Edits)
  const [pendingRemoveUserIds, setPendingRemoveUserIds] = useState<Set<string>>(
    new Set()
  );

  // Reset pending removals when edit mode changes or panel closes
  useEffect(() => {
    if (!isEditMode || !isDetailPanelOpen) {
      setPendingRemoveUserIds(new Set());
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
    idField: 'userId',
  });

  // Exclude already-added members from the options
  const availableUserOptions: CheckboxOption[] = useMemo(() => {
    if (!detailGroup) return [];
    const memberIds = new Set(groupMembers.map((u) => u._id));
    return userOptions.filter((o) => !memberIds.has(o.id));
  }, [userOptions, groupMembers, detailGroup]);

  // Toggle a user for pending removal (deferred — applied on Save Edits)
  const handleRemoveUser = useCallback(
    (userId: string) => {
      setPendingRemoveUserIds((prev) => {
        const next = new Set(prev);
        if (next.has(userId)) {
          next.delete(userId); // Un-mark if clicked again
        } else {
          next.add(userId);
        }
        return next;
      });
    },
    []
  );

  // Handle deleting the group
  const handleDeleteGroup = useCallback(async () => {
    if (!detailGroup) return;

    setIsDeleting(true);
    try {
      await GroupsApi.deleteGroup(detailGroup._id);

      addToast({
        variant: 'success',
        title: t('workspace.groups.edit.deleteSuccess', 'Group deleted'),
        description: t(
          'workspace.groups.edit.deleteSuccessDescription',
          {
            name: detailGroup.name,
            defaultValue: `"${detailGroup.name}" has been deleted`,
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
          'workspace.groups.edit.deleteError',
          'Failed to delete group'
        ),
        duration: 5000,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [detailGroup, closeDetailPanel, onUpdateSuccess, addToast, t]);

  // Handle saving edits (add new users)
  const handleSaveEdits = useCallback(async () => {
    if (!detailGroup) return;

    setIsSavingEdit(true);
    try {
      // Remove pending users
      if (pendingRemoveUserIds.size > 0) {
        await GroupsApi.removeUsersFromGroups(
          Array.from(pendingRemoveUserIds),
          [detailGroup._id]
        );
      }

      // Add newly selected users
      if (editAddUserIds.length > 0) {
        await GroupsApi.addUsersToGroups(editAddUserIds, [detailGroup._id]);
      }

      // Refresh the group data and members
      const updatedGroup = await GroupsApi.getGroup(detailGroup._id);
      setDetailGroup(updatedGroup);
      membersListRef.current?.refresh();

      addToast({
        variant: 'success',
        title: t('workspace.groups.edit.saveSuccess', 'Group updated!'),
        description: t(
          'workspace.groups.edit.saveSuccessDescription',
          {
            name: detailGroup.name,
            defaultValue: `Changes to '${detailGroup.name}' saved successfully`,
          }
        ),
        duration: 3000,
      });

      exitEditMode();
      onUpdateSuccess?.();
    } catch {
      addToast({
        variant: 'error',
        title: t(
          'workspace.groups.edit.saveError',
          'Failed to update group'
        ),
        duration: 5000,
      });
    } finally {
      setIsSavingEdit(false);
    }
  }, [
    detailGroup,
    pendingRemoveUserIds,
    editAddUserIds,
    setIsSavingEdit,
    setDetailGroup,
    exitEditMode,
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

  const panelTitle = detailGroup?.name || 'Group';

  return (
    <WorkspaceRightPanel
      open={isDetailPanelOpen}
      onOpenChange={(open) => {
        if (!open) closeDetailPanel();
      }}
      title={panelTitle}
      icon="group"
      primaryLabel={
        isEditMode
          ? t('workspace.groups.edit.save', 'Save Edits')
          : t('workspace.groups.edit.edit', 'Edit Group')
      }
      secondaryLabel={t('workspace.groups.edit.cancel', 'Cancel')}
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
        {/* Group Name */}
        <FormField
          label={t('workspace.groups.detail.nameLabel', 'Group Name')}
        >
          <input
            type="text"
            value={isEditMode ? editGroupName : detailGroup?.name ?? ''}
            onChange={(e) => {
              if (isEditMode) setEditGroupName(e.target.value);
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

        {/* Group Description */}
        <FormField
          label={t(
            'workspace.groups.detail.descriptionLabel',
            'Group Description'
          )}
        >
          <textarea
            value={
              isEditMode ? editGroupDescription : ''
            }
            onChange={(e) => {
              if (isEditMode) setEditGroupDescription(e.target.value);
            }}
            readOnly={!isEditMode}
            placeholder={
              isEditMode
                ? t(
                    'workspace.groups.detail.descriptionPlaceholder',
                    'Describe the purpose of this group'
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
              e.currentTarget.style.padding = 'var(--space-2)';
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
            {t('workspace.groups.detail.createdBy', 'Created By')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            -
          </Text>
        </Box>

        {/* Users section box */}
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
            {t('workspace.groups.detail.users', 'Users')}
          </Text>

          <PaginatedMembersList<GroupUser>
            key={detailGroup?._id}
            ref={membersListRef}
            fetcher={fetchGroupMembersFn}
            keyExtractor={(u) => u._id}
            searchPlaceholder={t('workspace.groups.detail.searchUsers', 'Search users...')}
            emptyText={t('workspace.groups.detail.noUsers', 'No users in this group')}
            onFetched={(items) => setGroupMembers(items)}
            renderItem={(user) => {
              const isPendingRemove = pendingRemoveUserIds.has(user._id);
              return (
                <Flex
                  align="center"
                  justify="between"
                  style={{
                    opacity: isPendingRemove ? 0.5 : 1,
                    transition: 'opacity 0.15s ease',
                  }}
                >
                  <AvatarCell
                    name={user.fullName || user.email || 'Unknown'}
                    email={user.email ?? undefined}
                    avatarSize={32}
                    isSelf={user._id === currentUser?.id}
                    profilePicture={user.profilePicture ?? undefined}
                  />
                  {isEditMode && (
                    <Text
                      size="1"
                      onClick={() => handleRemoveUser(user._id)}
                      style={{
                        color: isPendingRemove
                          ? 'var(--accent-11)'
                          : 'var(--red-11)',
                        cursor: 'pointer',
                        flexShrink: 0,
                        fontWeight: 500,
                      }}
                    >
                      {isPendingRemove
                        ? t('workspace.groups.edit.undo', 'Undo')
                        : t('workspace.groups.edit.remove', 'Remove')}
                    </Text>
                  )}
                </Flex>
              );
            }}
          />
        </Box>

        {/* Access Permissions section box */}
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
            {t('workspace.groups.detail.accessPermissions', 'Access Permissions')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t(
              'workspace.groups.detail.accessComingSoon',
              'Access Permissions Coming Soon'
            )}
          </Text>
        </Box>

        {/* Add Users section box (edit mode only) */}
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
                {t('workspace.groups.edit.addUsersLabel', 'Add Users')}
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
                'workspace.groups.edit.addUsersPlaceholder',
                'Search or select user(s) to add to this group'
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

      {/* Delete Group section (edit mode only) — separate box */}
      {isEditMode && (
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
                {t('workspace.groups.edit.deleteTitle', {
                  name: detailGroup?.name,
                  defaultValue: `Delete '${detailGroup?.name}' Group`,
                })}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-10)' }}>
                {t(
                  'workspace.groups.edit.deleteDescription',
                  'Permanently remove this group from the workspace'
                )}
              </Text>
            </Flex>
            <LoadingButton
              variant="outline"
              color="red"
              size="1"
              onClick={handleDeleteGroup}
              loading={isDeleting}
              loadingLabel={t('workspace.groups.edit.deleting', 'Deleting...')}
              style={{ flexShrink: 0 }}
            >
              {t('workspace.groups.edit.deleteButton', 'Delete Group')}
            </LoadingButton>
          </Flex>
        </Box>
      )}
    </WorkspaceRightPanel>
  );
}
