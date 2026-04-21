'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Box, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useToastStore } from '@/lib/store/toast-store';
import {
  WorkspaceRightPanel,
  FormField,
  TagInput,
  SelectDropdown,
  SearchableCheckboxDropdown,
} from '../../components';
import type { SelectOption, CheckboxOption } from '../../components';
import { useUsersStore } from '../store';
import { UsersApi } from '../api';
import { GroupsApi } from '../../groups/api';
import { GROUP_TYPES, USER_ROLES, INVITE_ROLE_OPTIONS } from '../../constants';
import type { Group } from '../../groups/types';

// ========================================
// Constants
// ========================================

// Sourced from shared constants — cast to SelectOption[] for the dropdown.
const ROLE_OPTIONS: SelectOption[] = INVITE_ROLE_OPTIONS.map((r) => ({
  value: r.value,
  label: r.label,
  description: r.description,
}));

// ========================================
// Helpers
// ========================================

function validateEmail(value: string): string | null {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(value)) {
    return 'Invalid email address';
  }
  return null;
}

// ========================================
// Component
// ========================================

export function InviteUsersSidebar({
  onInviteSuccess,
}: {
  onInviteSuccess?: () => void;
}) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const {
    isInvitePanelOpen,
    inviteEmails,
    inviteRole,
    inviteGroupIds,
    isInviting,
    editingInviteUser,
    closeInvitePanel,
    setInviteEmails,
    setInviteRole,
    setInviteGroupIds,
    setIsInviting,
    resetInviteForm,
  } = useUsersStore();

  const isEditMode = editingInviteUser !== null;

  // Local groups data (transient — not persisted in store)
  const [groups, setGroups] = useState<Group[]>([]);
  const [isLoadingGroups, setIsLoadingGroups] = useState(false);

  // Reset form when panel closes
  useEffect(() => {
    if (!isInvitePanelOpen) {
      resetInviteForm();
      setGroups([]);
    }
  }, [isInvitePanelOpen, resetInviteForm]);

  // Fetch groups when panel opens
  useEffect(() => {
    if (!isInvitePanelOpen) return;

    let cancelled = false;
    const fetchGroups = async () => {
      setIsLoadingGroups(true);
      try {
        const { groups: data } = await GroupsApi.listGroups();
        if (!cancelled) {
          // Filter out system groups and deleted groups
          setGroups(
            data.filter((g) => g.type !== GROUP_TYPES.EVERYONE && !g.isDeleted)
          );
        }
      } catch {
        // Error handled by global interceptor
      } finally {
        if (!cancelled) setIsLoadingGroups(false);
      }
    };
    fetchGroups();
    return () => {
      cancelled = true;
    };
  }, [isInvitePanelOpen]);

  // In edit mode, pre-populate group selections by matching names from the fetched list.
  // user.userGroups only carries { name, type } — no _id — so we resolve IDs here.
  useEffect(() => {
    if (!isEditMode || !editingInviteUser || groups.length === 0) return;

    const userGroupNames = new Set(
      (editingInviteUser.userGroups ?? [])
        .filter((g) => g.type !== GROUP_TYPES.EVERYONE && g.type !== GROUP_TYPES.ADMIN)
        .map((g) => g.name)
    );

    const matchedIds = groups
      .filter((g) => userGroupNames.has(g.name))
      .map((g) => g._id);

    setInviteGroupIds(matchedIds);
  }, [groups, isEditMode]);
  // ^ Only re-run when groups load or edit mode changes. Do NOT add inviteGroupIds
  //   to deps or this will undo manual edits the user makes after opening.

  // Form validation (role hidden — not required for now)
  const hasValidEmails = inviteEmails.some((tag) => tag.isValid !== false);
  const isFormValid = hasValidEmails;

  // Group options for dropdown
  const groupOptions: CheckboxOption[] = groups.map((g) => ({
    id: g._id,
    label: g.name.charAt(0).toUpperCase() + g.name.slice(1),
  }));

  // Handle submit — create invite or update existing invite
  const handleSubmit = useCallback(async () => {
    if (!isFormValid) return;

    const validEmails = inviteEmails
      .filter((tag) => tag.isValid !== false)
      .map((tag) => tag.value);

    if (validEmails.length === 0) return;

    setIsInviting(true);
    try {
      if (isEditMode && editingInviteUser) {
        // ── Edit mode: update role + groups for the pending user ──
        const userId = editingInviteUser.userId;
        const currentRole = editingInviteUser.role || USER_ROLES.MEMBER;
        const newRole = inviteRole || USER_ROLES.MEMBER;

        // Find admin group from the fetched groups list
        const adminGroup = groups.find((g) => g.type === GROUP_TYPES.ADMIN);

        // Update role if changed
        if (adminGroup && newRole !== currentRole) {
          if (newRole === USER_ROLES.ADMIN) {
            await GroupsApi.addUsersToGroups([userId], [adminGroup._id]);
          } else {
            await GroupsApi.removeUsersFromGroups([userId], [adminGroup._id]);
          }
        }

        // Update group memberships
        // Determine current non-system group IDs
        const currentGroupIds = (editingInviteUser.userGroups ?? [])
          .filter((g) => g.type !== GROUP_TYPES.EVERYONE && g.type !== GROUP_TYPES.ADMIN)
          .map((g) => g._id ?? '')
          .filter(Boolean);

        const newGroupIds = new Set(inviteGroupIds);
        const currentIds = new Set(currentGroupIds);

        // Groups to add
        const toAdd = inviteGroupIds.filter((id) => !currentIds.has(id));
        // Groups to remove
        const toRemove = currentGroupIds.filter((id: string) => !newGroupIds.has(id));

        if (toAdd.length > 0) {
          await GroupsApi.addUsersToGroups([userId], toAdd);
        }
        if (toRemove.length > 0) {
          await GroupsApi.removeUsersFromGroups([userId], toRemove);
        }

        addToast({
          variant: 'success',
          title: t('workspace.users.invite.editSuccessTitle', 'Invite updated'),
          description: t(
            'workspace.users.invite.editSuccessDescription',
            {
              email: editingInviteUser.email,
              defaultValue: `Invite for ${editingInviteUser.email} has been updated`,
            }
          ),
          duration: 3000,
        });
      } else {
        // ── Create mode: send new invite ──
        await UsersApi.inviteUsers(validEmails, inviteGroupIds.length > 0 ? inviteGroupIds : undefined);

        const emailDisplay =
          validEmails.length === 1
            ? validEmails[0]
            : `${validEmails.length} users`;
        addToast({
          variant: 'success',
          title: t('workspace.users.invite.successTitle', 'Invite sent!'),
          description: t(
            'workspace.users.invite.successDescription',
            { email: emailDisplay, defaultValue: `${emailDisplay} has been invited` }
          ),
          duration: 3000,
        });
      }

      // Close panel and refresh parent list
      closeInvitePanel();
      onInviteSuccess?.();
    } catch {
      addToast({
        variant: 'error',
        title: isEditMode
          ? t('workspace.users.invite.editError', 'Failed to update invite')
          : t('workspace.users.invite.errorGeneric', 'Failed to send invite'),
        duration: 5000,
      });
    } finally {
      setIsInviting(false);
    }
  }, [
    isFormValid,
    inviteEmails,
    inviteRole,
    inviteGroupIds,
    isEditMode,
    editingInviteUser,
    groups,
    setIsInviting,
    closeInvitePanel,
    onInviteSuccess,
    addToast,
    t,
  ]);

  // Panel title & button labels change in edit mode
  const panelTitle = isEditMode
    ? t('workspace.users.invite.editTitle', 'Edit Invite')
    : t('workspace.users.invite.title', 'Invite User(s)');

  const primaryLabel = isEditMode
    ? t('workspace.users.invite.update', 'Update Invite')
    : t('workspace.users.invite.send', 'Send Invite');

  return (
    <WorkspaceRightPanel
      open={isInvitePanelOpen}
      onOpenChange={(open) => {
        if (!open) closeInvitePanel();
      }}
      title={panelTitle}
      icon={isEditMode ? 'edit' : 'person_add_alt'}
      headerActions={
        !isEditMode ? (
          <Button
            variant="outline"
            color="gray"
            size="2"
            disabled
            style={{ cursor: 'not-allowed', opacity: 0.5 }}
          >
            <MaterialIcon name="upload" size={16} />
            {t('workspace.users.invite.importCsv', 'Import CSV')}
          </Button>
        ) : undefined
      }
      primaryLabel={primaryLabel}
      secondaryLabel={t('workspace.users.invite.cancel', 'Cancel')}
      primaryDisabled={!isFormValid}
      primaryLoading={isInviting}
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
        {/* Email input */}
        <FormField label={t('workspace.users.invite.emailLabel', 'Invite to')}>
          <TagInput
            tags={inviteEmails}
            onTagsChange={isEditMode ? undefined : setInviteEmails}
            placeholder={
              isEditMode
                ? ''
                : t(
                    'workspace.users.invite.emailPlaceholder',
                    'Enter one or more email addresses'
                  )
            }
            validate={validateEmail}
            disabled={isEditMode}
          />
        </FormField>

        {/* Role dropdown — hidden for now */}
        {/* <FormField label={t('workspace.users.invite.roleLabel', 'Assign Role')}>
          <SelectDropdown
            value={inviteRole}
            onChange={setInviteRole}
            options={ROLE_OPTIONS}
            placeholder={t(
              'workspace.users.invite.rolePlaceholder',
              'Assign team member role'
            )}
          />
        </FormField> */}

        {/* Groups dropdown */}
        <FormField
          label={t(
            'workspace.users.invite.groupLabel',
            'Add to a User Group'
          )}
          optional
        >
          <SearchableCheckboxDropdown
            options={groupOptions}
            selectedIds={inviteGroupIds}
            onSelectionChange={setInviteGroupIds}
            placeholder={t(
              'workspace.users.invite.groupPlaceholder',
              'Search or select user group(s)'
            )}
            emptyText={
              isLoadingGroups
                ? 'Loading groups...'
                : 'No groups available'
            }
          />
        </FormField>
      </Box>
    </WorkspaceRightPanel>
  );
}
