'use client';

import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react';
import { Box, Flex, Text, Badge, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import {
  FormField,
  SearchableCheckboxDropdown,
  AvatarCell,
} from '@/app/(main)/workspace/components';
import type { CheckboxOption } from '@/app/(main)/workspace/components';
import { usePaginatedUserOptions } from '@/app/(main)/workspace/hooks/use-paginated-user-options';
import { TeamsApi } from '@/app/(main)/workspace/teams/api';
import { TEAM_ROLE_LABELS } from '@/app/(main)/workspace/teams/constants';
import { RoleDropdownMenu } from '@/app/components/share';
import type { Team, TeamMemberRole } from '@/app/(main)/workspace/teams/types';

export interface CreateTeamFormHandle {
  /** Trigger submission. No-op if the form is invalid or already submitting. */
  submit: () => void;
}

export interface CreateTeamFormState {
  isValid: boolean;
  isSubmitting: boolean;
}

interface CreateTeamFormProps {
  /** Called after a team is successfully created. */
  onCreated: (team: Team) => void;
  /** When true, the internal paginated-user fetch is active. Default: true. */
  enabled?: boolean;
  /** Reactive state mirror for callers that render their own footer. */
  onStateChange?: (state: CreateTeamFormState) => void;
}

/**
 * Shared team-creation form body. Used by both the workspace Teams page
 * (inside a WorkspaceRightPanel whose own footer triggers submission) and
 * the share sidebar (inside its own view-stack with a custom footer).
 *
 * Owns all internal state — it resets automatically on unmount.
 * Callers invoke {@link CreateTeamFormHandle.submit} via ref and wire their
 * own footer using {@link CreateTeamFormState} from `onStateChange`.
 */
export const CreateTeamForm = forwardRef<CreateTeamFormHandle, CreateTeamFormProps>(
  function CreateTeamForm({ onCreated, enabled = true, onStateChange }, ref) {
    const { t } = useTranslation();
    const addToast = useToastStore((s) => s.addToast);

    const [teamName, setTeamName] = useState('');
    const [teamDescription, setTeamDescription] = useState('');
    const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
    const [memberRoles, setMemberRoles] = useState<Record<string, TeamMemberRole>>({});
    const [defaultRole, setDefaultRole] = useState<TeamMemberRole>('READER');
    const [selectedUserCache, setSelectedUserCache] = useState<Record<string, CheckboxOption>>({});
    const [isSubmitting, setIsSubmitting] = useState(false);

    // Prune role + cache entries when a selected user is removed
    useEffect(() => {
      const idSet = new Set(selectedUserIds);
      setMemberRoles((prev) => {
        let changed = false;
        const next: Record<string, TeamMemberRole> = {};
        for (const [id, role] of Object.entries(prev)) {
          if (idSet.has(id)) next[id] = role;
          else changed = true;
        }
        return changed ? next : prev;
      });
      setSelectedUserCache((prev) => {
        let changed = false;
        const next: Record<string, CheckboxOption> = {};
        for (const [id, opt] of Object.entries(prev)) {
          if (idSet.has(id)) next[id] = opt;
          else changed = true;
        }
        return changed ? next : prev;
      });
    }, [selectedUserIds]);

    // Paginated user options (server-search, UUID ids)
    const {
      options: userOptions,
      isLoading: userFilterLoading,
      hasMore: userFilterHasMore,
      onSearch: handleUserSearch,
      onLoadMore: handleUserLoadMore,
    } = usePaginatedUserOptions({
      enabled,
      idField: 'id',
      source: 'graph',
    });

    // Cache metadata for selected users while they're visible in options
    useEffect(() => {
      if (userOptions.length === 0 || selectedUserIds.length === 0) return;
      const idSet = new Set(selectedUserIds);
      setSelectedUserCache((prev) => {
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
    }, [userOptions, selectedUserIds]);

    // Selected users (with metadata) — in selection order, with cache fallback
    const selectedUsersOrdered = useMemo(() => {
      const currentById = new Map(userOptions.map((o) => [o.id, o]));
      return selectedUserIds
        .map((id) => currentById.get(id) ?? selectedUserCache[id])
        .filter((o): o is CheckboxOption => Boolean(o));
    }, [selectedUserIds, userOptions, selectedUserCache]);

    const isFormValid = teamName.trim().length > 0;

    // Mirror reactive state upward for external footer wiring
    useEffect(() => {
      onStateChange?.({ isValid: isFormValid, isSubmitting });
    }, [isFormValid, isSubmitting, onStateChange]);

    const setUserRole = useCallback((userId: string, role: TeamMemberRole) => {
      setMemberRoles((prev) => ({ ...prev, [userId]: role }));
    }, []);

    const removeUser = useCallback((userId: string) => {
      setSelectedUserIds((prev) => prev.filter((id) => id !== userId));
    }, []);

    // Lock a newly-selected user's role to the current defaultRole so later
    // default-role changes don't silently retarget them. Only "Apply to all"
    // re-applies the default to users already picked.
    const handleSelectionChange = useCallback(
      (ids: string[]) => {
        setSelectedUserIds((prev) => {
          const prevSet = new Set(prev);
          const newlyAdded = ids.filter((id) => !prevSet.has(id));
          if (newlyAdded.length > 0) {
            setMemberRoles((prevRoles) => {
              const next = { ...prevRoles };
              for (const id of newlyAdded) {
                if (!(id in next)) next[id] = defaultRole;
              }
              return next;
            });
          }
          return ids;
        });
      },
      [defaultRole]
    );

    const applyRoleToAll = useCallback(() => {
      setMemberRoles((prev) => {
        const next: Record<string, TeamMemberRole> = { ...prev };
        selectedUserIds.forEach((id) => {
          next[id] = defaultRole;
        });
        return next;
      });
    }, [selectedUserIds, defaultRole]);

    const handleSubmit = useCallback(async () => {
      if (!isFormValid || isSubmitting) return;
      setIsSubmitting(true);
      try {
        const newTeam = await TeamsApi.createTeam({
          name: teamName.trim(),
          description: teamDescription.trim() || undefined,
          userRoles: selectedUserIds.map((userId) => ({
            userId,
            role: memberRoles[userId] ?? defaultRole,
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

        onCreated(newTeam);
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.teams.create.errorGeneric', 'Failed to create team'),
          duration: 5000,
        });
      } finally {
        setIsSubmitting(false);
      }
    }, [
      isFormValid,
      isSubmitting,
      teamName,
      teamDescription,
      selectedUserIds,
      memberRoles,
      defaultRole,
      addToast,
      t,
      onCreated,
    ]);

    useImperativeHandle(ref, () => ({ submit: handleSubmit }), [handleSubmit]);

    return (
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
        <FormField label={t('workspace.teams.create.nameLabel', 'Team Name')}>
          <input
            type="text"
            value={teamName}
            onChange={(e) => setTeamName(e.target.value)}
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
          label={t('workspace.teams.create.descriptionLabel', 'Team Description')}
        >
          <textarea
            value={teamDescription}
            onChange={(e) => setTeamDescription(e.target.value)}
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
        <Flex direction="column" gap="2">
          <Flex align="center" justify="between">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.teams.create.addUsersLabel', 'Add Members')}
            </Text>
            <Badge variant="soft" color="gray" size="1">
              {t('workspace.common.selected', {
                count: selectedUserIds.length,
                defaultValue: '{{count}} Selected',
              })}
            </Badge>
          </Flex>

          {/* Compact inline: Default Role + Apply to all — sits above the
              search so the dropdown expansion doesn't push these controls
              out of view. */}
          <Flex align="center" justify="between" gap="2" wrap="wrap">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.teams.create.roleLabel', 'Default Role')}
            </Text>
            <Flex align="center" gap="2">
              <RoleDropdownMenu
                role={defaultRole}
                onRoleChange={(r) => setDefaultRole(r as TeamMemberRole)}
                labels={TEAM_ROLE_LABELS}
              />
              <Button
                variant="outline"
                color="gray"
                size="1"
                onClick={applyRoleToAll}
                disabled={selectedUserIds.length === 0}
              >
                {t('workspace.teams.create.applyToAll', 'Apply to all')}
              </Button>
            </Flex>
          </Flex>

          <SearchableCheckboxDropdown
            options={userOptions}
            selectedIds={selectedUserIds}
            onSelectionChange={handleSelectionChange}
            placeholder={t(
              'workspace.teams.create.addUsersPlaceholder',
              'Search or select user(s) to add to this team'
            )}
            emptyText={t('workspace.common.noUsersAvailable', 'No users available')}
            showAvatar
            onSearch={handleUserSearch}
            onLoadMore={handleUserLoadMore}
            isLoadingMore={userFilterLoading}
            hasMore={userFilterHasMore}
          />
        </Flex>

        {/* Selected members with per-row role (compact) */}
        {selectedUsersOrdered.length > 0 && (
          <Flex direction="column" gap="2">
            <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.teams.create.memberRoles', 'Member Roles')}
            </Text>
            <Flex
              direction="column"
              gap="1"
              className="upload-scroll-area"
              style={{ maxHeight: 220, overflowY: 'auto', paddingRight: 4 }}
            >
              {selectedUsersOrdered.map((user) => {
                const currentRole = memberRoles[user.id] ?? defaultRole;
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
                        onRoleChange={(r) =>
                          setUserRole(user.id, r as TeamMemberRole)
                        }
                        onRemove={() => removeUser(user.id)}
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
    );
  }
);
