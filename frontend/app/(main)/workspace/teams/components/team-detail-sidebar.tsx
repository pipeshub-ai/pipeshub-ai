'use client';

import React, { useMemo } from 'react';
import { Box, Flex, Text, Badge, Button } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import {
  WorkspaceRightPanel,
  FormField,
  SearchableCheckboxDropdown,
  AvatarCell,
  PaginatedMembersList,
  ConfirmationDialog,
} from '../../components';
import { useWorkspaceDrawerNestedModalHost } from '../../components/workspace-right-panel';
import type { TeamMember, TeamMemberRole } from '../types';
import { TEAM_ROLE_LABELS, normalizeTeamMemberRole } from '../constants';
import { useTeamDetail } from '../use-team-detail';
import { RoleDropdownMenu } from '@/app/components/share';

// ========================================
// Component
// ========================================

export function TeamDetailSidebar({
  onUpdateSuccess,
}: {
  onUpdateSuccess?: () => void;
}) {
  const { i18n } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const notices = useMemo(
    () => ({
      saved: (name: string) =>
        addToast({
          variant: 'success',
          title: i18n.t('workspace.teams.edit.saveSuccess', 'Team updated!'),
          description: i18n.t('workspace.teams.edit.saveSuccessDescription', {
            name,
            defaultValue: `Changes to '${name}' saved successfully`,
          }),
          duration: 3000,
        }),
      saveFailed: (error: unknown) =>
        addToast({
          variant: 'error',
          title: i18n.t('workspace.teams.edit.saveError', 'Failed to update team'),
          description: getUserFacingErrorMessage(
            error,
            i18n.t('workspace.teams.edit.saveErrorDescription', 'Could not update team. Please try again.')
          ),
          duration: 5000,
        }),
      deleted: (name: string) =>
        addToast({
          variant: 'success',
          title: i18n.t('workspace.teams.edit.deleteSuccess', 'Team deleted'),
          description: i18n.t('workspace.teams.edit.deleteSuccessDescription', {
            name,
            defaultValue: `"${name}" has been deleted`,
          }),
          duration: 3000,
        }),
      deleteFailed: () =>
        addToast({
          variant: 'error',
          title: i18n.t('workspace.teams.edit.deleteError', 'Failed to delete team'),
          duration: 5000,
        }),
    }),
    [addToast, i18n]
  );
  const {
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
    canEditTeam,
    handlePrimaryClick,
    handleSecondaryClick,
    panelTitle,
    creatorUser,
    isCreatorSelf,
  } = useTeamDetail({ onUpdateSuccess, notices });
  const nestedModalHost = useWorkspaceDrawerNestedModalHost(isDetailPanelOpen);

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
      hideFooter={!canEditTeam && !isEditMode}
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
          {(control) => (
            <input
              {...control}
              type="text"
              value={isEditMode ? editTeamName : detailTeam?.name ?? ''}
              onChange={(e) => {
                if (isEditMode) setEditTeamName(e.target.value);
              }}
              readOnly={!isEditMode}
              style={{
                width: '100%',
                height: 44, // the touch-target minimum; also ~12px around the 20px line
                padding: '0 12px',
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
                  e.currentTarget.style.padding = '0 11px';
                }
              }}
              onBlur={(e) => {
                e.currentTarget.style.border = '1px solid var(--slate-a5)';
                e.currentTarget.style.padding = '0 12px';
              }}
            />
          )}
        </FormField>

        {/* Team Description */}
        <FormField
          label={t(
            'workspace.teams.detail.descriptionLabel',
            'Team Description'
          )}
        >
          {(control) => (
            <textarea
              {...control}
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
                padding: '10px 12px',
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
                  e.currentTarget.style.padding = '9px 11px';
                }
              }}
              onBlur={(e) => {
                e.currentTarget.style.border = '1px solid var(--slate-a5)';
                e.currentTarget.style.padding = '10px 12px';
              }}
            />
          )}
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
            {t('workspace.teams.detail.createdBy')}
          </Text>
          {creatorUser ? (
            <AvatarCell
              name={creatorUser.name || creatorUser.email || 'Unknown User'}
              email={creatorUser.email}
              avatarSize={32}
              isSelf={isCreatorSelf}
              profilePicture={creatorUser.profilePicture ?? undefined}
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
            key={`${detailTeam?.id}-${isEditMode ? 'edit' : 'view'}`}
            ref={membersListRef}
            fetcher={fetchTeamMembersFn}
            keyExtractor={(m) => m.id}
            searchPlaceholder={t('workspace.teams.detail.searchMembers', 'Search members...')}
            emptyText={t('workspace.teams.detail.noMembers', 'No members in this team')}
            maxHeight={320}
            listClassName="team-member-scroll-area"
            onFetched={(items) => setTeamMembers(items)}
            renderItem={(member) => {
              const isPendingRemove = pendingRemoveUserIds.has(member.userId);
              const effectiveRole = normalizeTeamMemberRole(
                pendingUpdateRoles.get(member.userId) ?? member.role,
                member.isOwner
              );
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
                      name={
                        member.userName?.trim() ||
                        member.userEmail?.trim() ||
                        member.userId ||
                        'Unknown'
                      }
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
                            member.userId,
                            r as TeamMemberRole,
                            normalizeTeamMemberRole(member.role, member.isOwner)
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
                        onClick={() => handleRemoveUser(member.userId)}
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
      {isEditMode && detailTeam?.canDelete && detailTeam.id !== `all_${detailTeam.orgId}` && (
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
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
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
              onClick={() => setIsDeleteConfirmOpen(true)}
              loading={isDeleting}
              loadingLabel={t('workspace.teams.edit.deleting', 'Deleting...')}
              style={{ flexShrink: 0 }}
            >
              {t('workspace.teams.edit.deleteButton', 'Delete Team')}
            </LoadingButton>
          </Flex>
        </Box>
      )}

      <ConfirmationDialog
        open={isDeleteConfirmOpen}
        onOpenChange={(open) => {
          if (!open && !isDeleting) setIsDeleteConfirmOpen(false);
        }}
        title={t('workspace.teams.actions.deleteConfirmTitle', 'Delete team?')}
        message={t('workspace.teams.actions.deleteConfirmMessage', {
          name: detailTeam?.name ?? '',
          defaultValue:
            '"{{name}}" will be deleted and its members will lose access shared through it. This can\'t be undone.',
        })}
        confirmLabel={t('workspace.teams.actions.deleteConfirmButton', 'Delete team')}
        cancelLabel={t('common.cancel', 'Cancel')}
        confirmVariant="danger"
        isLoading={isDeleting}
        confirmLoadingLabel={t('workspace.teams.actions.deleting', 'Deleting…')}
        onConfirm={() => void handleDeleteTeam()}
        container={nestedModalHost}
      />
    </WorkspaceRightPanel>
  );
}
