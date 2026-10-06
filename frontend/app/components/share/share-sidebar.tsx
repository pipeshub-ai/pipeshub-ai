'use client';

import { DRAWER_TOAST_INSET_PX, useToastDrawerInset } from '@/lib/toast-safe-area';
import { useTranslation } from 'react-i18next';

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Dialog, Flex, Box, Text, Button, IconButton, VisuallyHidden } from '@radix-ui/themes';
import { ConfirmationDialog } from '@/workspace/components';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useAuthStore } from '@/config';
import { usePaginatedList } from '@/app/(main)/workspace/hooks/use-paginated-list';
import { ShareCommonApi } from './api';
import {
  CreateTeamForm,
  type CreateTeamFormHandle,
  type CreateTeamFormState,
} from '@/app/components/team';

const USERS_PAGE_LIMIT = 25;
const SCROLL_THRESHOLD_PX = 40;
import { ShareSearchInput } from './share-search-input';
import { ShareableRow } from './shareable-row';
import { ShareSettings } from './share-settings';
import {
  buildShareSubmission,
  DEFAULT_TEAM_SHARE_ROLE,
  getShareRoleLabels,
  toTeamShareRole,
  type ShareAdapter,
  type SharedMember,
  type ShareTeam,
  type ShareUser,
  type ShareSelection,
  type ShareRole,
  type ShareSubmission,
  type ShareConfirmSpec,
  type ShareMode,
  type ShareAccessSummary,
  type ShareSettingDescriptor,
} from './types';
import { LottieLoader } from '../ui/lottie-loader';
import { toast } from '@/lib/store/toast-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

interface ShareSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  adapter: ShareAdapter;
  /** Called after a successful share/unshare so the parent can re-fetch */
  onShareSuccess?: () => void;
  /** Optional content rendered above the members list (e.g. org-wide toggle). */
  headerContent?: React.ReactNode;
}

export function ShareSidebar({
  open,
  onOpenChange,
  adapter,
  onShareSuccess,
  headerContent,
}: ShareSidebarProps) {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  useToastDrawerInset(open, DRAWER_TOAST_INSET_PX);
  const roleLabels = getShareRoleLabels(t);
  const currentUser = useAuthStore((s) => s.user);

  // View toggle
  const [currentView, setCurrentView] = useState<'share' | 'create-team'>('share');

  // Share form state
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedItems, setSelectedItems] = useState<ShareSelection[]>([]);
  const [selectedRole, setSelectedRole] = useState<ShareRole>('READER');
  // Tracks whether any portaled role dropdown is open — used to suppress the
  // dialog's outside-click handler while the dropdown is active.
  const [isRoleDropdownOpen, setIsRoleDropdownOpen] = useState(false);

  // Collaboration extras (adapters without them keep the defaults)
  const [mode, setMode] = useState<ShareMode>('manage');
  const [summary, setSummary] = useState<ShareAccessSummary | null>(null);
  const [settingsList, setSettingsList] = useState<ShareSettingDescriptor[]>([]);
  const [pendingSettingId, setPendingSettingId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<{
    spec: ShareConfirmSpec;
    submission: ShareSubmission;
  } | null>(null);
  const [transferTarget, setTransferTarget] = useState<SharedMember | null>(null);

  // Fetched data
  const [suggestedTeams, setSuggestedTeams] = useState<ShareTeam[]>([]);
  const [nonPaginatedUsers, setNonPaginatedUsers] = useState<ShareUser[]>([]);
  const [existingMembers, setExistingMembers] = useState<SharedMember[]>([]);
  const membersScrollRef = useRef<HTMLDivElement>(null);

  // Paginated mode: hook owns search, page, items. Idle when the sidebar is
  // closed or the adapter doesn't support pagination.
  const isPaginatedMode = !!adapter.getSharingUsersPaginated;
  const paginatedUsersFetcher = useCallback(
    async (search: string | undefined, page: number, limit: number) => {
      if (!adapter.getSharingUsersPaginated) return { items: [] as ShareUser[], totalCount: 0 };
      const result = await adapter.getSharingUsersPaginated({ page, limit, search });
      return { items: result.users, totalCount: result.totalCount };
    },
    [adapter]
  );
  const paginated = usePaginatedList<ShareUser>({
    fetcher: paginatedUsersFetcher,
    limit: USERS_PAGE_LIMIT,
    enabled: open && isPaginatedMode,
  });

  // Unified view: in paginated mode the hook is the source of truth; otherwise
  // the state-loaded user list from the open-fetch effect below.
  const allUsers = isPaginatedMode ? paginated.items : nonPaginatedUsers;
  const searchQueryInput = isPaginatedMode ? paginated.search : searchQuery;
  const updateSearchQuery = useCallback(
    (value: string) => {
      if (isPaginatedMode) paginated.setSearch(value);
      else setSearchQuery(value);
    },
    [isPaginatedMode, paginated]
  );

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
      setNote('');
      setInlineError(null);
      setPendingConfirm(null);
      setTransferTarget(null);
      paginated.reset();
    }
    // paginated.reset is stable; omit from deps to avoid reset loops on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const applyAdapterExtras = useCallback(async () => {
    const nextMode = adapter.getMode?.() ?? 'manage';
    setMode(nextMode);
    setSummary(nextMode === 'summary' ? adapter.getAccessSummary?.() ?? null : null);
    if (adapter.settings) {
      setSettingsList(nextMode === 'manage' ? await adapter.settings() : []);
    }
  }, [adapter]);

  const refreshMembers = useCallback(async () => {
    setExistingMembers(await adapter.getSharedMembers());
    await applyAdapterExtras();
  }, [adapter, applyAdapterExtras]);

  // Coded failures (collaboration adapters) show inline; the rest keep the generic toast.
  const reportError = useCallback(
    (error: unknown, titleKey: string, fallbackKey: string) => {
      if (adapter.formatError) {
        setInlineError(adapter.formatError(error));
        return;
      }
      const message = (error as { message?: string })?.message ?? t(fallbackKey);
      toast.error(t(titleKey), { description: message });
    },
    [adapter, t]
  );

  // Fetch non-user data when sidebar opens (paginated users are handled by the hook)
  useEffect(() => {
    if (!open) return;

    const fetchData = async () => {
      setIsLoading(true);
      try {
        const promises: Promise<unknown>[] = [adapter.getSharedMembers()];
        if (!isPaginatedMode) {
          promises.push(adapter.getSharingUsers ? adapter.getSharingUsers() : ShareCommonApi.getAllUsers());
        }
        if (adapter.supportsTeams) {
          promises.push(ShareCommonApi.listUserTeams());
        }

        const results = await Promise.all(promises);
        setExistingMembers(results[0] as SharedMember[]);
        let idx = 1;
        if (!isPaginatedMode) {
          setNonPaginatedUsers(results[idx] as ShareUser[]);
          idx += 1;
        }
        if (adapter.supportsTeams && results[idx]) {
          setSuggestedTeams(results[idx] as ShareTeam[]);
        }
        await applyAdapterExtras();
      } catch {
        // Error handling via global interceptor
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [open, adapter, isPaginatedMode, applyAdapterExtras]);

  // Infinite scroll handler — hook's loadMore is a no-op when not paginated
  const handleUsersScroll = useCallback(() => {
    if (!isPaginatedMode) return;
    const el = membersScrollRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD_PX) {
      paginated.loadMore();
    }
  }, [isPaginatedMode, paginated]);

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

  // Filtered suggested teams — teams always come from a single, non-paginated
  // endpoint, so filter client-side regardless of the user-list mode.
  const filteredTeams = useMemo(() => {
    if (!adapter.supportsTeams) return [];
    return suggestedTeams.filter((team) => {
      if (existingMemberIds.has(team.id)) return false;
      if (selectedIds.has(team.id)) return false;
      if (!searchQueryInput) return true;
      const q = searchQueryInput.toLowerCase();
      return team.name.toLowerCase().includes(q);
    });
  }, [suggestedTeams, existingMemberIds, selectedIds, searchQueryInput, adapter.supportsTeams]);

  // Filtered suggested members — in paginated mode the server already filtered
  // by query, so only apply the exclusion rules (existing, selected, self).
  const filteredMembers = useMemo(() => {
    return allUsers.filter((user) => {
      if (existingMemberIds.has(user.id)) return false;
      if (selectedIds.has(user.id)) return false;
      if (currentUser && user.id === currentUser.id) return false;
      if (isPaginatedMode || !searchQueryInput) return true;
      const q = searchQueryInput.toLowerCase();
      return user.name.toLowerCase().includes(q) || (user.email ?? '').toLowerCase().includes(q);
    });
  }, [allUsers, existingMemberIds, selectedIds, currentUser, searchQueryInput, isPaginatedMode]);

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
      updateSearchQuery('');
    },
    [updateSearchQuery]
  );

  const handleTeamRoleChange = useCallback((id: string, role: ShareRole) => {
    setSelectedItems((prev) =>
      prev.map((s) => (s.id === id && s.type === 'team' ? { ...s, role: toTeamShareRole(role) } : s))
    );
  }, []);

  const handleRemoveSelection = useCallback((id: string) => {
    setSelectedItems((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const submitShare = useCallback(
    async (submission: ShareSubmission) => {
      setIsSubmitting(true);
      setInlineError(null);
      try {
        await adapter.share(submission);
        await refreshMembers();

        const names = selectedItems.map((s) => s.name).join(', ');
        toast.success(t('shareSidebar.accessShared'), { description: t('shareSidebar.sharedWith', { names }) });

        setSelectedItems([]);
        setNote('');
        updateSearchQuery('');

        onShareSuccess?.();
      } catch (error) {
        reportError(error, 'shareSidebar.shareFailed', 'shareSidebar.shareError');
      } finally {
        setIsSubmitting(false);
      }
    },
    [selectedItems, adapter, refreshMembers, onShareSuccess, updateSearchQuery, reportError, t]
  );

  const maxPerSubmit = adapter.maxPerSubmit;
  const overLimitBy = maxPerSubmit != null ? Math.max(selectedItems.length - maxPerSubmit, 0) : 0;

  const handleShare = useCallback(async () => {
    if (selectedItems.length === 0 || overLimitBy > 0) return;

    const submission: ShareSubmission = buildShareSubmission(
      adapter.supportsRoles
        ? selectedItems
        : selectedItems.map((s) => ({ ...s, role: 'READER' as const })),
      adapter.supportsRoles ? selectedRole : 'READER',
    );
    const trimmedNote = note.trim();
    if (adapter.noteField && trimmedNote) {
      submission.note = trimmedNote.slice(0, adapter.noteField.maxLength);
    }
    const spec = adapter.requiresConfirm?.(submission, selectedItems) ?? null;
    if (spec) {
      setPendingConfirm({ spec, submission });
      return;
    }
    await submitShare(submission);
  }, [selectedItems, selectedRole, note, adapter, submitShare, overLimitBy]);

  const handleConfirmShare = useCallback(async () => {
    if (!pendingConfirm) return;
    const { spec, submission } = pendingConfirm;
    setPendingConfirm(null);
    await submitShare(spec.orgWide ? { ...submission, confirmOrgWide: true } : submission);
  }, [pendingConfirm, submitShare]);

  const handleConfirmTransfer = useCallback(async () => {
    const target = transferTarget;
    if (!target || !adapter.transferOwnership) return;
    setTransferTarget(null);
    setInlineError(null);
    try {
      await adapter.transferOwnership(target.id);
      await refreshMembers();
      toast.success(t('chat.collab.share.transferred'), {
        description: t('chat.collab.share.transferredDescription', { name: target.name }),
      });
      onShareSuccess?.();
    } catch (error) {
      reportError(error, 'shareSidebar.roleFailed', 'shareSidebar.roleError');
    }
  }, [transferTarget, adapter, refreshMembers, onShareSuccess, reportError, t]);

  const handleToggleSetting = useCallback(
    async (id: string, value: boolean) => {
      if (!adapter.updateSetting) return;
      setInlineError(null);
      setPendingSettingId(id);
      setSettingsList((prev) => prev.map((d) => (d.id === id ? { ...d, value } : d)));
      try {
        await adapter.updateSetting(id, value);
      } catch (error) {
        setSettingsList((prev) => prev.map((d) => (d.id === id ? { ...d, value: !value } : d)));
        reportError(error, 'shareSidebar.roleFailed', 'shareSidebar.roleError');
      } finally {
        setPendingSettingId(null);
      }
    },
    [adapter, reportError]
  );

  // Update role for existing member.
  // Note: the "at least one owner must remain" invariant is enforced server-side;
  // a failed update surfaces through the catch block's toast.
  const handleRoleChange = useCallback(
    async (memberId: string, memberType: 'user' | 'team', newRole: ShareRole) => {
      if (!adapter.updateRole) return;
      const member = existingMembers.find((m) => m.id === memberId && m.type === memberType);
      // Users cannot change their own permission
      if (member?.isCurrentUser) return;
      setInlineError(null);
      try {
        await adapter.updateRole(memberId, memberType, newRole);
        setExistingMembers((prev) =>
          prev.map((m) =>
            m.id === memberId && m.type === memberType ? { ...m, role: newRole, isOwner: newRole === 'OWNER' } : m
          )
        );
        toast.success(t('shareSidebar.roleUpdated'), {
          description: t('shareSidebar.roleUpdatedDescription', {
            name: member?.name ?? t('shareSidebar.member'),
            role: t(`recordView.permission${newRole.charAt(0)}${newRole.slice(1).toLowerCase()}`),
          }),
        });
      } catch (error) {
        reportError(error, 'shareSidebar.roleFailed', 'shareSidebar.roleError');
      }
    },
    [adapter, existingMembers, reportError, t]
  );

  // Remove member.
  // Note: the "at least one owner must remain" invariant is enforced server-side.
  const handleRemoveMember = useCallback(
    async (memberId: string, memberType: 'user' | 'team') => {
      const member = existingMembers.find((m) => m.id === memberId);
      // Users cannot remove themselves (use the leave/revoke own access flow instead)
      if (member?.isCurrentUser) return;
      setInlineError(null);
      try {
        await adapter.removeMember(memberId, memberType);
        setExistingMembers((prev) => prev.filter((m) => m.id !== memberId));
        toast.success(t('shareSidebar.accessRevoked'), {
          description: t('shareSidebar.accessRevokedDescription', { name: member?.name ?? t('shareSidebar.member') }),
        });
        onShareSuccess?.();
      } catch (error) {
        reportError(error, 'shareSidebar.revokeFailed', 'shareSidebar.revokeError');
      }
    },
    [adapter, existingMembers, onShareSuccess, reportError, t]
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

  // Only a manager changes rows; the owner row is never editable once the adapter lists levels.
  const canEditRow = (member: SharedMember) =>
    mode === 'manage' && !member.isCurrentUser && !(adapter.roleOptions && member.isOwner);

  // Create-team form state (when currentView === 'create-team')
  const createFormRef = useRef<CreateTeamFormHandle>(null);
  const [createFormState, setCreateFormState] = useState<CreateTeamFormState>({
    isValid: false,
    isSubmitting: false,
  });

  return (
    <>
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        onPointerDownOutside={(e) => {
          // Prevent dialog from closing while a portaled role dropdown is open.
          if (isRoleDropdownOpen) {
            e.preventDefault();
          }
        }}
        onEscapeKeyDown={(e) => {
          // Escape closes the open role menu only.
          if (isRoleDropdownOpen) e.preventDefault();
        }}
        style={{
          position: 'fixed',
          ...(isMobile
            ? { top: 0, right: 0, bottom: 0, left: 0, width: '100%', maxWidth: '100%', maxHeight: '100dvh', borderRadius: 0 }
            : {
                top: 10,
                right: 10,
                bottom: 10,
                width: '37.5rem',
                maxWidth: '100vw',
                maxHeight: 'calc(100vh - 20px)',
                borderRadius: 'var(--radius-2)',
              }),
          padding: 0,
          margin: 0,
          background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
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
          <Flex direction="column" style={{ height: '100%' }}>
            {/* Header */}
            <Flex
              align="center"
              justify="between"
              style={{
                padding: '12px 16px',
                borderBottom: '1px solid var(--olive-3)',
                background: 'var(--effects-translucent)',
                backdropFilter: 'blur(8px)',
                flexShrink: 0,
              }}
            >
              <Flex align="center" gap="2">
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="2"
                  onClick={() => setCurrentView('share')}
                  aria-label={t('common.back')}
                >
                  <MaterialIcon name="arrow_back" size={18} color="var(--slate-11)" />
                </IconButton>
                <Flex
                  align="center"
                  justify="center"
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: '50%',
                    backgroundColor: 'var(--slate-3)',
                  }}
                >
                  <MaterialIcon name="group" size={16} color="var(--slate-11)" />
                </Flex>
                <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {t('workspace.teams.createTeam')}
                </Text>
              </Flex>
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                onClick={() => onOpenChange(false)}
                aria-label={t('common.close')}
              >
                <MaterialIcon name="close" size={18} color="var(--slate-11)" />
              </IconButton>
            </Flex>

            {/* Scrollable body */}
            <Box style={{ flex: 1, overflow: 'auto', padding: '16px', minHeight: 0 }}>
              <CreateTeamForm
                ref={createFormRef}
                enabled
                onCreated={handleTeamCreated}
                onStateChange={setCreateFormState}
              />
            </Box>

            {/* Footer */}
            <Flex
              align="center"
              justify="end"
              gap="2"
              style={{
                padding: '12px 16px',
                borderTop: '1px solid var(--olive-3)',
                background: 'var(--effects-translucent)',
                backdropFilter: 'blur(8px)',
                flexShrink: 0,
              }}
            >
              <Button
                variant="outline"
                color="gray"
                size="2"
                onClick={() => setCurrentView('share')}
                disabled={createFormState.isSubmitting}
              >
                {t('action.cancel')}
              </Button>
              <LoadingButton
                variant="solid"
                size="2"
                onClick={() => createFormRef.current?.submit()}
                disabled={!createFormState.isValid}
                loading={createFormState.isSubmitting}
                loadingLabel={t('action.creating')}
              >
                {t('workspace.teams.createTeam')}
              </LoadingButton>
            </Flex>
          </Flex>
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
                aria-label={t('common.close')}
              >
                <MaterialIcon name="close" size={16} color="var(--slate-11)" />
              </IconButton>
            </Flex>

            {/* Search input */}
            {mode !== 'summary' && (
            <Box style={{ padding: '16px 16px 8px', background: 'var(--effects-translucent)', backdropFilter: 'blur(8px)' }}>
              {adapter.notice && (
                <Text
                  as="p"
                  size="1"
                  role="note"
                  style={{ color: 'var(--slate-11)', marginBottom: 8 }}
                >
                  {adapter.notice}
                </Text>
              )}
              <ShareSearchInput
                selections={selectedItems}
                searchQuery={searchQueryInput}
                selectedRole={selectedRole}
                supportsRoles={adapter.supportsRoles}
                onSearchChange={updateSearchQuery}
                onRemoveSelection={handleRemoveSelection}
                removeSelectionLabel={(name) => t('chat.collab.access.removeSelection', { name })}
                onRoleChange={setSelectedRole}
                onTeamRoleChange={handleTeamRoleChange}
                onRoleDropdownOpenChange={setIsRoleDropdownOpen}
                onRemoveLastSelection={() =>
                  setSelectedItems((prev) => prev.slice(0, -1))
                }
                onEmailSubmit={handleEmailSubmit}
                roleOptions={adapter.roleOptions}
                inputLabel={adapter.roleOptions ? t('chat.collab.share.searchLabel') : undefined}
              />
              {overLimitBy > 0 && maxPerSubmit != null && (
                <Text
                  as="p"
                  size="1"
                  role="alert"
                  data-testid="share-limit-message"
                  style={{ color: 'var(--red-11)', marginTop: 8 }}
                >
                  {t('chat.collab.access.shareLimit', { max: maxPerSubmit, over: overLimitBy })}
                </Text>
              )}
              {adapter.noteField && selectedItems.length > 0 && (
                <Flex direction="column" gap="1" style={{ marginTop: 8 }}>
                  <Text as="label" size="1" htmlFor="share-note" style={{ color: 'var(--slate-11)' }}>
                    {t('chat.collab.share.noteLabel')}
                  </Text>
                  <textarea
                    id="share-note"
                    value={note}
                    maxLength={adapter.noteField.maxLength}
                    onChange={(e) => setNote(e.target.value)}
                    rows={3}
                    style={{
                      border: '1px solid var(--slate-6)',
                      borderRadius: 'var(--radius-2)',
                      padding: 8,
                      fontSize: 14,
                      fontFamily: 'var(--default-font-family)',
                      background: 'var(--tokens-colors-surface)',
                      color: 'var(--slate-12)',
                      resize: 'vertical',
                    }}
                  />
                  <Text size="1" style={{ color: 'var(--slate-9)', alignSelf: 'flex-end' }}>
                    {t('chat.collab.share.noteCount', { used: note.length, max: adapter.noteField.maxLength })}
                  </Text>
                </Flex>
              )}
            </Box>
            )}

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
              {headerContent}

              {mode === 'summary' && summary && (
                <Flex direction="column" gap="1" style={{ padding: '16px 0 8px' }}>
                  <Text size="2" style={{ color: 'var(--slate-12)' }}>
                    {t('chat.collab.share.summaryOwner', { name: summary.ownerName })}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)' }}>
                    {t('chat.collab.share.summaryCount', { total: summary.count })}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)' }}>
                    {t('chat.collab.share.summaryAccess', { access: summary.myAccessLabel })}
                  </Text>
                </Flex>
              )}

              {inlineError && (
                <Text
                  as="p"
                  size="2"
                  role="alert"
                  style={{ color: 'var(--red-11)', padding: '8px 0' }}
                >
                  {inlineError}
                </Text>
              )}

              {isLoading ? (
                <Flex align="center" justify="center" style={{ padding: '40px 0' }}>
                  <LottieLoader variant="loader" size={32} showLabel />
                </Flex>
              ) : (
                <>
                  {/* Suggested teams */}
                  {mode !== 'summary' && adapter.supportsTeams && filteredTeams.length > 0 && (
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
                          fontStyle: 'normal',
                        }}
                      >
                        {t('shareSidebar.suggestedTeams')}
                      </Text>
                      {filteredTeams.map((team) => (
                        <ShareableRow
                          key={team.id}
                          type="team"
                          name={team.name}
                          subtitle={t('shareSidebar.memberCount', { count: team.memberCount })}
                          isSelected={selectedIds.has(team.id)}
                          showRadio
                          onToggle={() =>
                            handleToggleSelection({
                              type: 'team',
                              id: team.id,
                              name: team.name,
                              memberCount: team.memberCount,
                              role: DEFAULT_TEAM_SHARE_ROLE,
                            })
                          }
                        />
                      ))}
                    </>
                  )}

                  {/* Suggested members */}
                  {mode !== 'summary' && filteredMembers.length > 0 && (
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
                          fontStyle: 'normal',
                        }}
                      >
                        {t('shareSidebar.suggestedMembers')}
                      </Text>
                      {/* Cap suggestions at 5; search narrows further */}
                      {filteredMembers.slice(0, 5).map((user) => (
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
                        {t('workspace.teams.detail.members')}
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
                          name={member.name}
                          subtitle={member.email}
                          avatarUrl={member.avatarUrl}
                          isCurrentUser={member.isCurrentUser}
                          isOwner={member.isOwner}
                          role={member.role}
                          showRoleDropdown={canEditRow(member)}
                          staticRoleLabel={
                            adapter.roleOptions && !canEditRow(member) && !member.isOwner
                              ? adapter.roleOptions.find((o) => o.role === member.role)?.label
                              : undefined
                          }
                          roleOptions={adapter.roleOptions}
                          onMakeOwner={
                            adapter.transferOwnership &&
                            mode === 'manage' &&
                            member.type === 'user' &&
                            member.role === 'WRITER' &&
                            !member.state &&
                            !member.isCurrentUser
                              ? () => setTransferTarget(member)
                              : undefined
                          }
                          teamRoleEditable={adapter.supportsRoles && !!adapter.teamRolesEditable}
                          noRolesInfo={
                            member.state
                              ? {
                                  title: t(member.state === 'former_member' ? 'chat.collab.share.formerMember' : 'chat.collab.share.deletedTeam'),
                                  description: t('chat.collab.share.removeOnly'),
                                }
                              : !adapter.supportsRoles && member.type === 'user'
                              ? { title: roleLabels[member.role]?.label ?? roleLabels.READER.label, description: roleLabels[member.role]?.description ?? '' }
                              : undefined
                          }
                          onRoleChange={
                            adapter.supportsRoles
                              ? (newRole) => handleRoleChange(member.id, member.type, newRole)
                              : undefined
                          }
                          onRemove={() => handleRemoveMember(member.id, member.type)}
                          onRoleDropdownOpenChange={setIsRoleDropdownOpen}
                        />
                      ))}
                    </>
                  )}

                  {mode === 'manage' && settingsList.length > 0 && (
                    <ShareSettings
                      descriptors={settingsList}
                      onToggle={handleToggleSetting}
                      pendingId={pendingSettingId}
                    />
                  )}

                  {/* Loading more indicator */}
                  {paginated.isLoadingMore && (
                    <Text size="1" style={{ color: 'var(--slate-9)', textAlign: 'center', padding: 8, display: 'block' }}>
                      {t('shareSidebar.loadingMoreUsers')}
                    </Text>
                  )}

                  {/* Empty state */}
                  {mode !== 'summary' &&
                    filteredTeams.length === 0 &&
                    filteredMembers.length === 0 &&
                    existingMembers.length === 0 && (
                      <Flex
                        align="center"
                        justify="center"
                        style={{ padding: '40px 0' }}
                      >
                        <Text size="2" style={{ color: 'var(--slate-9)' }}>
                          {t('shareSidebar.noResults')}
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
                {mode === 'summary' ? t('common.close') : t('action.cancel')}
              </Button>

              {mode !== 'summary' && adapter.supportsTeams && (
                <Button
                  variant="outline"
                  size="2"
                  onClick={() => setCurrentView('create-team')}
                >
                  {t('shareSidebar.createNewTeam')}
                </Button>
              )}

              {mode !== 'summary' && (
              <LoadingButton
                variant="solid"
                size="2"
                onClick={handleShare}
                disabled={selectedItems.length === 0 || overLimitBy > 0 || selectedItems.some((s) => s.isInvalid)}
                loading={isSubmitting}
                loadingLabel={t('shareSidebar.sharing')}
                style={selectedItems.length > 0 && overLimitBy === 0 && !isSubmitting && !selectedItems.some((s) => s.isInvalid) ? { backgroundColor: 'var(--emerald-10)' } : undefined}
              >
                {t('action.share')}
              </LoadingButton>
              )}
            </Flex>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>

      <ConfirmationDialog
        open={pendingConfirm !== null}
        onOpenChange={(next) => {
          if (!next) setPendingConfirm(null);
        }}
        title={pendingConfirm?.spec.title ?? ''}
        message={pendingConfirm?.spec.message ?? ''}
        confirmLabel={pendingConfirm?.spec.confirmLabel ?? ''}
        confirmVariant="primary"
        onConfirm={handleConfirmShare}
      />
      <ConfirmationDialog
        open={transferTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTransferTarget(null);
        }}
        title={t('chat.collab.share.transferTitle', { name: transferTarget?.name ?? '' })}
        message={t('chat.collab.share.transferMessage', { name: transferTarget?.name ?? '' })}
        confirmLabel={t('chat.collab.share.makeOwner')}
        confirmVariant="primary"
        onConfirm={handleConfirmTransfer}
      />
    </>
  );
}
