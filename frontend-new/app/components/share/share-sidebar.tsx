'use client';

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Dialog, Flex, Box, Text, Button, IconButton, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useAuthStore } from '@/lib/store/auth-store';
import { ShareCommonApi } from './api';
import { ShareSearchInput } from './share-search-input';
import { ShareableRow } from './shareable-row';
import { CreateTeamView } from './create-team-view';
import type {
  ShareAdapter,
  SharedMember,
  ShareTeam,
  ShareUser,
  ShareSelection,
  ShareRole,
  ShareSubmission,
} from './types';
import { LottieLoader } from '../ui/lottie-loader';
import { toast } from '@/lib/store/toast-store';

interface ShareSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  adapter: ShareAdapter;
  /** Called after a successful share/unshare so the parent can re-fetch */
  onShareSuccess?: () => void;
}

export function ShareSidebar({
  open,
  onOpenChange,
  adapter,
  onShareSuccess,
}: ShareSidebarProps) {
  const currentUser = useAuthStore((s) => s.user);

  // View toggle
  const [currentView, setCurrentView] = useState<'share' | 'create-team'>('share');

  // Share form state
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedItems, setSelectedItems] = useState<ShareSelection[]>([]);
  const [selectedRole, setSelectedRole] = useState<ShareRole>('READER');

  // Fetched data
  const [suggestedTeams, setSuggestedTeams] = useState<ShareTeam[]>([]);
  const [allUsers, setAllUsers] = useState<ShareUser[]>([]);
  const [existingMembers, setExistingMembers] = useState<SharedMember[]>([]);

  // Pagination state for users (when adapter supports it)
  const USERS_PAGE_LIMIT = 25;
  const [usersPage, setUsersPage] = useState(1);
  const [usersTotalCount, setUsersTotalCount] = useState(0);
  const [isLoadingMoreUsers, setIsLoadingMoreUsers] = useState(false);
  const hasMoreUsers = adapter.getSharingUsersPaginated ? usersPage * USERS_PAGE_LIMIT < usersTotalCount : false;
  const usersSearchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const membersScrollRef = useRef<HTMLDivElement>(null);

  // Loading
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Reset state when sidebar closes
  useEffect(() => {
    if (!open) {
      setCurrentView('share');
      setSearchQuery('');
      setSelectedItems([]);
      setSelectedRole('READER');
      setUsersPage(1);
      setUsersTotalCount(0);
    }
  }, [open]);

  // Fetch data when sidebar opens
  useEffect(() => {
    if (!open) return;

    const fetchData = async () => {
      setIsLoading(true);
      try {
        const promises: Promise<unknown>[] = [
          adapter.getSharedMembers(),
        ];

        // Paginated or full user fetch
        if (adapter.getSharingUsersPaginated) {
          promises.push(
            adapter.getSharingUsersPaginated({ page: 1, limit: USERS_PAGE_LIMIT }).then((result) => {
              setUsersTotalCount(result.totalCount);
              return result.users;
            })
          );
        } else {
          promises.push(
            adapter.getSharingUsers ? adapter.getSharingUsers() : ShareCommonApi.getAllUsers()
          );
        }

        if (adapter.supportsTeams) {
          promises.push(ShareCommonApi.listUserTeams());
        }

        const results = await Promise.all(promises);
        setExistingMembers(results[0] as SharedMember[]);
        setAllUsers(results[1] as ShareUser[]);
        if (adapter.supportsTeams && results[2]) {
          setSuggestedTeams(results[2] as ShareTeam[]);
        }
      } catch {
        // Error handling via global interceptor
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [open, adapter]);

  // Server-side search for paginated mode
  useEffect(() => {
    if (!adapter.getSharingUsersPaginated || !open) return;
    if (usersSearchTimerRef.current) clearTimeout(usersSearchTimerRef.current);
    usersSearchTimerRef.current = setTimeout(async () => {
      setUsersPage(1);
      try {
        const result = await adapter.getSharingUsersPaginated!({
          page: 1,
          limit: USERS_PAGE_LIMIT,
          search: searchQuery || undefined,
        });
        setAllUsers(result.users);
        setUsersTotalCount(result.totalCount);
      } catch {
        // handled by global interceptor
      }
    }, 300);
    return () => {
      if (usersSearchTimerRef.current) clearTimeout(usersSearchTimerRef.current);
    };
  }, [searchQuery, adapter, open]);

  // Infinite scroll handler for paginated users
  const handleUsersScroll = useCallback(() => {
    if (!hasMoreUsers || isLoadingMoreUsers || !adapter.getSharingUsersPaginated) return;
    const el = membersScrollRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
      const nextPage = usersPage + 1;
      setUsersPage(nextPage);
      setIsLoadingMoreUsers(true);
      adapter.getSharingUsersPaginated({ page: nextPage, limit: USERS_PAGE_LIMIT, search: searchQuery || undefined })
        .then((result) => {
          setAllUsers((prev) => [...prev, ...result.users]);
        })
        .catch(() => { /* handled by global interceptor */ })
        .finally(() => setIsLoadingMoreUsers(false));
    }
  }, [hasMoreUsers, isLoadingMoreUsers, usersPage, searchQuery, adapter]);

  // Existing member IDs (for filtering suggestions)
  const existingMemberIds = useMemo(() => {
    const ids = new Set<string>();
    existingMembers.forEach((m) => ids.add(m.id));
    return ids;
  }, [existingMembers]);

  // Selected item IDs
  const selectedIds = useMemo(
    () => new Set(selectedItems.map((s) => s.id)),
    [selectedItems]
  );

  // Filtered suggested teams (exclude already-shared, filter by query)
  const filteredTeams = useMemo(() => {
    if (!adapter.supportsTeams) return [];
    return suggestedTeams.filter((team) => {
      if (existingMemberIds.has(team.id)) return false;
      if (selectedIds.has(team.id)) return false;
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return team.name.toLowerCase().includes(q);
    });
  }, [suggestedTeams, existingMemberIds, selectedIds, searchQuery, adapter.supportsTeams]);

  // Filtered suggested members (exclude already-shared, current user, filter by query)
  const filteredMembers = useMemo(() => {
    return allUsers.filter((user) => {
      if (existingMemberIds.has(user.id)) return false;
      if (selectedIds.has(user.id)) return false;
      if (currentUser && user.id === currentUser.id) return false;
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return user.name.toLowerCase().includes(q) || (user.email ?? '').toLowerCase().includes(q);
    });
  }, [allUsers, existingMemberIds, selectedIds, currentUser, searchQuery]);

  // Handle raw email typed by user
  const handleEmailSubmit = useCallback(
    (email: string) => {
      const q = email.toLowerCase();
      const match = allUsers.find((u) => (u.email ?? '').toLowerCase() === q);
      if (match) {
        // Valid org user — add as normal selection if not already present
        if (!selectedIds.has(match.id) && !existingMemberIds.has(match.id)) {
          setSelectedItems((prev) => [...prev, { type: 'user', id: match.id, name: match.name, email: match.email }]);
        }
      } else {
        // Unknown email — add as invalid chip if not already added
        if (!selectedIds.has(q)) {
          setSelectedItems((prev) => [...prev, { type: 'user', id: q, name: email, email, isInvalid: true }]);
        }
      }
    },
    [allUsers, selectedIds, existingMemberIds]
  );

  // Toggle selection of a team or member
  const handleToggleSelection = useCallback(
    (item: ShareSelection) => {
      setSelectedItems((prev) => {
        const exists = prev.find((s) => s.id === item.id);
        if (exists) return prev.filter((s) => s.id !== item.id);
        return [...prev, item];
      });
      setSearchQuery('');
    },
    []
  );

  const handleRemoveSelection = useCallback((id: string) => {
    setSelectedItems((prev) => prev.filter((s) => s.id !== id));
  }, []);

  // Submit share
  const handleShare = useCallback(async () => {
    if (selectedItems.length === 0) return;

    setIsSubmitting(true);
    try {
      const submission: ShareSubmission = {
        userIds: selectedItems.filter((s) => s.type === 'user').map((s) => s.id),
        teamIds: selectedItems.filter((s) => s.type === 'team').map((s) => s.id),
        role: adapter.supportsRoles ? selectedRole : 'READER',
      };
      await adapter.share(submission);

      // Refresh members
      const updatedMembers = await adapter.getSharedMembers();
      setExistingMembers(updatedMembers);

      const names = selectedItems.map((s) => s.name).join(', ');
      toast.success('Access shared', { description: `Shared with ${names}` });

      setSelectedItems([]);
      setSearchQuery('');

      onShareSuccess?.();
    } catch {
      toast.error('Failed to share', { description: 'Could not share access. Please try again.' });
    } finally {
      setIsSubmitting(false);
    }
  }, [selectedItems, selectedRole, adapter, onShareSuccess]);

  // Update role for existing member
  const handleRoleChange = useCallback(
    async (memberId: string, memberType: 'user' | 'team', newRole: ShareRole) => {
      if (!adapter.updateRole) return;
      const member = existingMembers.find((m) => m.id === memberId);
      try {
        await adapter.updateRole(memberId, memberType, newRole);
        setExistingMembers((prev) =>
          prev.map((m) => (m.id === memberId ? { ...m, role: newRole } : m))
        );
        toast.success('Role updated', {
          description: `${member?.name ?? 'Member'} is now a ${newRole.toLowerCase()}`,
        });
      } catch {
        toast.error('Failed to update role', { description: 'Could not update role. Please try again.' });
      }
    },
    [adapter, existingMembers]
  );

  // Remove member
  const handleRemoveMember = useCallback(
    async (memberId: string, memberType: 'user' | 'team') => {
      const member = existingMembers.find((m) => m.id === memberId);
      try {
        await adapter.removeMember(memberId, memberType);
        setExistingMembers((prev) => prev.filter((m) => m.id !== memberId));
        toast.success('Access revoked', {
          description: `${member?.name ?? 'Member'} no longer has access`,
        });
        onShareSuccess?.();
      } catch {
        toast.error('Failed to revoke access', { description: 'Could not remove access. Please try again.' });
      }
    },
    [adapter, existingMembers, onShareSuccess]
  );

  // Team created callback
  const handleTeamCreated = useCallback(async () => {
    setCurrentView('share');
    // Refresh teams
    if (adapter.supportsTeams) {
      try {
        const teams = await ShareCommonApi.listUserTeams();
        setSuggestedTeams(teams);
      } catch {
        // ignore
      }
    }
  }, [adapter.supportsTeams]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: '37.5rem',
          maxWidth: '100vw',
          maxHeight: 'calc(100vh - 20px)',
          padding: 0,
          margin: 0,
          background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          backdropFilter: 'blur(25px)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{adapter.sidebarTitle}</Dialog.Title>
        </VisuallyHidden>

        {currentView === 'create-team' && adapter.supportsTeams ? (
          <CreateTeamView
            allUsers={allUsers}
            onTeamCreated={handleTeamCreated}
            onBack={() => setCurrentView('share')}
            onClose={() => onOpenChange(false)}
          />
        ) : (
          <Flex direction="column" style={{ height: '100%' }}>
            {/* Header */}
            <Flex
              align="center"
              justify="between"
              style={{
                padding: '8px 16px',
                borderBottom: '1px solid var(--olive-3)',
                background: 'var(--effects-translucent)',
                backdropFilter: 'blur(8px)',
              }}
            >
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {adapter.sidebarTitle}
              </Text>
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                onClick={() => onOpenChange(false)}
              >
                <MaterialIcon name="close" size={16} color="var(--slate-11)" />
              </IconButton>
            </Flex>

            {/* Search input */}
            <Box style={{ padding: '16px 16px 8px', background: 'var(--effects-translucent)', backdropFilter: 'blur(8px)' }}>
              <ShareSearchInput
                selections={selectedItems}
                searchQuery={searchQuery}
                selectedRole={selectedRole}
                supportsRoles={adapter.supportsRoles}
                onSearchChange={setSearchQuery}
                onRemoveSelection={handleRemoveSelection}
                onRoleChange={setSelectedRole}
                onRemoveLastSelection={() =>
                  setSelectedItems((prev) => prev.slice(0, -1))
                }
                onEmailSubmit={handleEmailSubmit}
              />
            </Box>

            {/* Scrollable body */}
            <Box
              ref={membersScrollRef}
              onScroll={handleUsersScroll}
              style={{
                flex: 1,
                overflow: 'auto',
                padding: '0 16px 16px',
                background: 'var(--effects-translucent)',
                backdropFilter: 'blur(8px)',
              }}
            >
              {isLoading ? (
                <Flex align="center" justify="center" style={{ padding: '40px 0' }}>
                  <LottieLoader variant="loader" size={32} showLabel />
                </Flex>
              ) : (
                <>
                  {/* Suggested teams */}
                  {adapter.supportsTeams && filteredTeams.length > 0 && (
                    <>
                      <Text
                        size="1"
                        weight="medium"
                        style={{
                          color: 'var(--slate-11)',
                          marginTop: 8,
                          marginBottom: 8,
                          display: 'block',
                          // textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                        }}
                      >
                        Suggested teams
                      </Text>
                      {filteredTeams.map((team) => (
                        <ShareableRow
                          key={team.id}
                          type="team"
                          name={team.name}
                          subtitle={`${team.memberCount} member${team.memberCount !== 1 ? 's' : ''}`}
                          isSelected={selectedIds.has(team.id)}
                          showRadio
                          onToggle={() =>
                            handleToggleSelection({
                              type: 'team',
                              id: team.id,
                              name: team.name,
                              memberCount: team.memberCount,
                            })
                          }
                        />
                      ))}
                    </>
                  )}

                  {/* Suggested members */}
                  {filteredMembers.length > 0 && (
                    <>
                      <Text
                        size="1"
                        weight="medium"
                        style={{
                          color: 'var(--slate-11)',
                          marginTop: 16,
                          marginBottom: 8,
                          display: 'block',
                          // textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                        }}
                      >
                        Suggested members
                      </Text>
                      {filteredMembers.map((user) => (
                        <ShareableRow
                          key={user.id}
                          type="member"
                          name={user.name}
                          subtitle={user.email}
                          avatarUrl={user.avatarUrl}
                          isSelected={selectedIds.has(user.id)}
                          showRadio={user.isInOrg}
                          showInvite={!user.isInOrg}
                          onToggle={() =>
                            handleToggleSelection({
                              type: 'user',
                              id: user.id,
                              name: user.name,
                              email: user.email,
                            })
                          }
                        />
                      ))}
                    </>
                  )}

                  {/* Existing members */}
                  {existingMembers.length > 0 && (
                    <>
                      <Text
                        size="1"
                        weight="medium"
                        style={{
                          color: 'var(--slate-11)',
                          marginTop: 16,
                          marginBottom: 8,
                          display: 'block',
                          // textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                        }}
                      >
                        Members
                      </Text>
                      {/* Teams first, then users */}
                      {[...existingMembers]
                        .sort((a, b) => {
                          if (a.type === b.type) return 0;
                          return a.type === 'team' ? -1 : 1;
                        })
                        .map((member) => (
                        <ShareableRow
                          key={member.id}
                          type={member.type === 'user' ? 'member' : 'team'}
                          name={
                            member.isCurrentUser
                              ? `${member.name} (you)`
                              : member.name
                          }
                          subtitle={member.email}
                          avatarUrl={member.avatarUrl}
                          isOwner={member.isOwner}
                          role={member.role}
                          showRoleDropdown={!member.isOwner && !member.isCurrentUser}
                          noRolesInfo={
                            !adapter.supportsRoles && member.type === 'user'
                              ? { title: 'Full Access', description: 'Chats do not have roles' }
                              : undefined
                          }
                          onRoleChange={
                            adapter.supportsRoles
                              ? (newRole) => handleRoleChange(member.id, member.type, newRole)
                              : undefined
                          }
                          onRemove={
                            !member.isOwner && !member.isCurrentUser
                              ? () => handleRemoveMember(member.id, member.type)
                              : undefined
                          }
                        />
                      ))}
                    </>
                  )}

                  {/* Loading more indicator */}
                  {isLoadingMoreUsers && (
                    <Text size="1" style={{ color: 'var(--slate-9)', textAlign: 'center', padding: 8, display: 'block' }}>
                      Loading more users...
                    </Text>
                  )}

                  {/* Empty state */}
                  {filteredTeams.length === 0 &&
                    filteredMembers.length === 0 &&
                    existingMembers.length === 0 && (
                      <Flex
                        align="center"
                        justify="center"
                        style={{ padding: '40px 0' }}
                      >
                        <Text size="2" style={{ color: 'var(--slate-9)' }}>
                          No users or teams found
                        </Text>
                      </Flex>
                    )}
                </>
              )}
            </Box>

            {/* Footer */}
            <Flex
              align="center"
              justify="end"
              gap="2"
              style={{
                padding: '12px 16px',
                borderTop: '1px solid var(--olive-3)',
                flexShrink: 0,
                background: 'var(--effects-translucent)',
                backdropFilter: 'blur(8px)',
              }}
            >
              <Button
                variant="outline"
                color="gray"
                size="2"
                onClick={() => onOpenChange(false)}
                // style={{borderRadius: 'var(--radius-2)', border: '1px solid var(--slate-a8)'}}
              >
                Cancel
              </Button>

              {adapter.supportsTeams && (
                <Button
                  variant="outline"
                  size="2"
                  onClick={() => setCurrentView('create-team')}
                >
                  Create a New Team
                </Button>
              )}

              <Button
                variant="solid"
                size="2"
                onClick={handleShare}
                disabled={selectedItems.length === 0 || isSubmitting || selectedItems.some((s) => s.isInvalid)}
                style={selectedItems.length > 0 && !isSubmitting && !selectedItems.some((s) => s.isInvalid) ? { backgroundColor: 'var(--emerald-10)' } : undefined}
              >
                {isSubmitting ? 'Sharing...' : 'Share'}
              </Button>
            </Flex>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
