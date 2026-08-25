'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Box, Button, Callout, Flex, HoverCard, Text } from '@radix-ui/themes';
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
import type { SelectOption, CheckboxOption, TagItem } from '../../components';
import { useUsersStore } from '../store';
import { UsersApi } from '../api';
import { GroupsApi } from '../../groups/api';
import { USER_ROLES, INVITE_ROLE_OPTIONS } from '../../constants';
import { GroupType, type Group } from '../../groups/types';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

// ========================================
// Constants
// ========================================

// Sourced from shared constants — cast to SelectOption[] for the dropdown.
const ROLE_OPTIONS: SelectOption[] = INVITE_ROLE_OPTIONS.map((r) => ({
  value: r.value,
  label: r.label,
  description: r.description,
}));

// Matches MAX_BULK_INVITE on the server.
const MAX_IMPORT_EMAILS = 1000;

const SAMPLE_CELL_STYLE: React.CSSProperties = {
  padding: '4px var(--space-2)',
  textAlign: 'left',
  borderBottom: '1px solid var(--olive-4)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

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
  const isAdmin = useUserStore(selectIsAdmin);

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
  const [isImporting, setIsImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Reset form when panel closes
  useEffect(() => {
    if (!isInvitePanelOpen) {
      resetInviteForm();
      setGroups([]);
    }
  }, [isInvitePanelOpen, resetInviteForm]);

  // Members can only invite as Member — lock the role whenever the panel opens.
  useEffect(() => {
    if (isInvitePanelOpen && !isAdmin) {
      setInviteRole(USER_ROLES.MEMBER);
    }
  }, [isInvitePanelOpen, isAdmin, setInviteRole]);

  // Fetch groups when panel opens (admin-only; groups APIs stay admin-gated)
  useEffect(() => {
    if (!isInvitePanelOpen || !isAdmin) return;

    let cancelled = false;
    const fetchGroups = async () => {
      setIsLoadingGroups(true);
      try {
        const { groups: data } = await GroupsApi.listGroups();
        if (!cancelled) {
          // Filter out system groups and deleted groups
          setGroups(
            data.filter((g) => g.type !== GroupType.EVERYONE && !g.isDeleted)
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
  }, [isInvitePanelOpen, isAdmin]);

  // In edit mode, pre-populate group selections by matching names from the fetched list.
  // user.userGroups only carries { name, type } — no _id — so we resolve IDs here.
  useEffect(() => {
    if (!isEditMode || !editingInviteUser || groups.length === 0) return;

    const userGroupNames = new Set(
      (editingInviteUser.userGroups ?? [])
        .filter((g) => g.type !== GroupType.EVERYONE && g.type !== GroupType.ADMIN)
        .map((g) => g.name)
    );

    const matchedIds = groups
      .filter((g) => userGroupNames.has(g.name))
      .map((g) => g._id);

    setInviteGroupIds(matchedIds);
  }, [groups, isEditMode]);
  // ^ Only re-run when groups load or edit mode changes. Do NOT add inviteGroupIds
  //   to deps or this will undo manual edits the user makes after opening.

  // Form validation
  const hasValidEmails = inviteEmails.some((tag) => tag.isValid !== false);
  const isFormValid = hasValidEmails && Boolean(inviteRole);

  const adminGroupId = groups.find((g) => g.type === GroupType.ADMIN)?._id;
  const isGrantingAdmin =
    inviteRole === USER_ROLES.ADMIN ||
    Boolean(adminGroupId && inviteGroupIds.includes(adminGroupId));

  // Group options for dropdown (everyone already excluded when groups are fetched)
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

        // Update role if changed (stored on User.role, not admin group)
        if (newRole !== currentRole) {
          await UsersApi.updateUser(userId, {
            role: newRole === USER_ROLES.ADMIN ? 'admin' : 'member',
          });
        }

        // Update group memberships
        // Determine current non-system group IDs
        const currentGroupIds = (editingInviteUser.userGroups ?? [])
          .filter((g) => g.type !== GroupType.EVERYONE && g.type !== GroupType.ADMIN)
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
        await UsersApi.inviteUsers(
          validEmails,
          inviteGroupIds.length > 0 ? inviteGroupIds : undefined,
          isAdmin ? inviteRole || USER_ROLES.MEMBER : USER_ROLES.MEMBER,
        );

        const emailDisplay =
          validEmails.length === 1
            ? validEmails[0]
            : `${validEmails.length} users`;
        addToast({
          variant: 'success',
          title: t('workspace.users.invite.successTitle', 'Invite sent!'),
          description: t('workspace.users.invite.successDescription', {
            email: emailDisplay,
            defaultValue: `${emailDisplay} has been invited`,
          }),
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
    isAdmin,
  ]);

  // Fills the email box for review rather than inviting straight away — nothing
  // is sent until the user presses Send Invite.
  const handleImportFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = '';
      if (!file) return;

      setIsImporting(true);
      try {
        // Loaded on demand so the parser stays out of the page bundle.
        const XLSX = await import('xlsx');
        const workbook = XLSX.read(await file.arrayBuffer(), {
          type: 'array',
          codepage: 65001,
          sheetRows: MAX_IMPORT_EMAILS + 5,
        });

        const found: string[] = [];
        workbook.SheetNames.forEach((sheetName) => {
          const sheet = workbook.Sheets[sheetName];
          if (!sheet) return;
          const rows = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
            header: 1,
            blankrows: false,
          });
          rows.forEach((row) => {
            row.forEach((cell) => {
              if (cell == null) return;
              const value = String(cell).trim();
              // 254 = max email length per RFC 5321.
              if (
                found.length <= MAX_IMPORT_EMAILS &&
                value.length <= 254 &&
                value.includes('@')
              ) {
                found.push(value);
              }
            });
          });
        });

        if (found.length === 0) {
          throw new Error(
            t(
              'workspace.users.invite.importNoEmails',
              'No email addresses found in the file'
            )
          );
        }
        if (found.length > MAX_IMPORT_EMAILS) {
          throw new Error(
            t('workspace.users.invite.importTooMany', {
              defaultValue: 'File exceeds the {{max}}-user limit',
              max: MAX_IMPORT_EMAILS,
            })
          );
        }

        const { isInvitePanelOpen: stillOpen, inviteEmails: current } =
          useUsersStore.getState();
        if (!stillOpen) return;

        const seen = new Set(
          current.map((tag) => tag.value.trim().toLowerCase())
        );
        const added: TagItem[] = [];
        found.forEach((raw) => {
          const value = raw.toLowerCase();
          if (seen.has(value)) return;
          seen.add(value);
          added.push({
            id: `tag-import-${added.length}-${Math.random().toString(36).slice(2, 7)}`,
            value,
            isValid: validateEmail(value) === null,
          });
        });

        if (added.length === 0) {
          throw new Error(
            t(
              'workspace.users.invite.importAllDuplicates',
              'Every address in the file is already listed'
            )
          );
        }

        if (current.length + added.length > MAX_IMPORT_EMAILS) {
          throw new Error(
            t('workspace.users.invite.importTooMany', {
              defaultValue: 'File exceeds the {{max}}-user limit',
              max: MAX_IMPORT_EMAILS,
            })
          );
        }

        setInviteEmails([...current, ...added]);

        const invalidCount = added.filter((tag) => !tag.isValid).length;
        const duplicateCount = found.length - added.length;
        addToast({
          variant: 'success',
          title: t('workspace.users.invite.importLoaded', {
            defaultValue: '{{count}} address(es) added',
            count: added.length,
          }),
          description: t(
            'workspace.users.invite.importLoadedDescription',
            'Review the list, then press Send Invite.'
          ),
          duration: 4000,
        });
        if (invalidCount > 0 || duplicateCount > 0) {
          addToast({
            variant: 'warning',
            title: t('workspace.users.invite.importSkipped', {
              defaultValue:
                '{{invalid}} invalid, {{duplicate}} duplicate address(es)',
              invalid: invalidCount,
              duplicate: duplicateCount,
            }),
            description: t(
              'workspace.users.invite.importSkippedDescription',
              'Invalid entries are marked in the list — remove them before sending.'
            ),
            duration: 6000,
          });
        }
      } catch (err) {
        addToast({
          variant: 'error',
          title: t('workspace.users.invite.importFailed', 'Import failed'),
          description:
            (err as { message?: string })?.message ||
            t(
              'workspace.users.invite.importFailedDescription',
              'Could not read the file. Upload a valid CSV or Excel file.'
            ),
          duration: 5000,
        });
      } finally {
        setIsImporting(false);
      }
    },
    [inviteEmails, setInviteEmails, addToast, t]
  );

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
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              style={{ display: 'none' }}
              onChange={handleImportFile}
            />
            <HoverCard.Root>
              <HoverCard.Trigger>
                <Button
                  variant="outline"
                  color="gray"
                  size="2"
                  disabled={isImporting}
                  onClick={() => fileInputRef.current?.click()}
                  style={{ cursor: isImporting ? 'default' : 'pointer' }}
                >
                  <MaterialIcon name="upload" size={16} />
                  {t('workspace.users.invite.import', 'Import')}
                </Button>
              </HoverCard.Trigger>
              <HoverCard.Content size="2" maxWidth="330px">
                <Flex direction="column" gap="2">
                  <Text size="2" weight="medium">
                    {t(
                      'workspace.users.invite.importFormatTitle',
                      'CSV or Excel — any layout works'
                    )}
                  </Text>
                  <Box
                    style={{
                      border: '1px solid var(--olive-5)',
                      borderRadius: 'var(--radius-2)',
                      overflow: 'hidden',
                    }}
                  >
                    <table
                      style={{
                        width: '100%',
                        borderCollapse: 'collapse',
                        tableLayout: 'fixed',
                      }}
                    >
                      <thead>
                        <tr style={{ backgroundColor: 'var(--olive-3)' }}>
                          <th style={SAMPLE_CELL_STYLE}>
                            <Text size="1" weight="medium">
                              email
                            </Text>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {['alice@acme.com', 'bob@acme.com'].map((email) => (
                          <tr key={email}>
                            <td style={SAMPLE_CELL_STYLE}>
                              <Text size="1">{email}</Text>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Box>
                  <Flex direction="column" gap="1">
                    {[
                      t(
                        'workspace.users.invite.importHintHeader',
                        'Header row optional — extra columns ignored'
                      ),
                      t(
                        'workspace.users.invite.importHintLimit',
                        'Up to 1000 users per file (.csv, .xlsx, .xls)'
                      ),
                    ].map((hint) => (
                      <Text key={hint} size="1" color="gray">
                        • {hint}
                      </Text>
                    ))}
                  </Flex>
                </Flex>
              </HoverCard.Content>
            </HoverCard.Root>
          </>
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
          padding: 'var(--space-4)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-5)',
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

        {/* Role dropdown */}
        <FormField label={t('workspace.users.invite.roleLabel', 'Assign Role')}>
          <SelectDropdown
            value={isAdmin ? inviteRole : USER_ROLES.MEMBER}
            onChange={setInviteRole}
            options={
              isAdmin
                ? ROLE_OPTIONS
                : ROLE_OPTIONS.filter((r) => r.value === USER_ROLES.MEMBER)
            }
            disabled={!isAdmin}
            placeholder={t(
              'workspace.users.invite.rolePlaceholder',
              'Assign team member role'
            )}
          />
        </FormField>

        {/* Groups dropdown — Enterprise Edition only */}
        {/* <FormField
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
          {isGrantingAdmin ? (
            <Box mt="2">
              <Callout.Root color="amber" variant="soft" size="1">
                <Callout.Icon>
                  <MaterialIcon name="warning" size={16} />
                </Callout.Icon>
                <Callout.Text size="1" style={{ lineHeight: 1.45 }}>
                  {t('workspace.users.invite.adminGroupWarning', {
                    defaultValue:
                      'Everyone invited here becomes a full org admin — including every address imported from a file. Admins can manage users, groups and org settings.',
                  })}
                </Callout.Text>
              </Callout.Root>
            </Box>
          ) : null}
        </FormField> */}
      </Box>
    </WorkspaceRightPanel>
  );
}
