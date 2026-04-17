'use client';

import React, { useEffect, useMemo, useCallback, useRef, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { formatDate } from '@/lib/utils/formatters';
import { DateRangePicker } from '@/app/components/ui';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  EntityPageHeader,
  EntityFilterBar,
  EntityDataTable,
  EntityPagination,
  EntityEmptyState,
  EntityRowActionMenu,
  AvatarCell,
} from '../components';
import type { ColumnConfig } from '../components';
import type { FilterChipConfig } from '../components/entity-filter-bar';
import type { RowAction } from '../components/entity-row-action-menu';
import { useGroupsStore } from './store';
import { GroupsApi } from './api';
import type { Group } from './types';
import { CreateGroupSidebar } from './components/create-group-sidebar';
import { GroupDetailSidebar } from './components/group-detail-sidebar';

// ========================================
// Constants
// ========================================

const GROUPS_FILTER_CHIPS: FilterChipConfig[] = [
  { key: 'createdOn', label: 'Created On', icon: 'calendar_today' },
];

// ========================================
// Page Component
// ========================================

function GroupsPageContent() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const {
    groups,
    selectedGroups,
    page,
    limit,
    totalCount,
    searchQuery,
    filters,
    isLoading,
    error: _error,
    setGroups,
    setSelectedGroups,
    setPage,
    setLimit,
    setSearchQuery,
    setFilters,
    setLoading,
    setError,
    openCreatePanel,
    openDetailPanel,
    closeCreatePanel,
    closeDetailPanel,
    enterEditMode,
    exitEditMode,
    isCreatePanelOpen,
    isDetailPanelOpen,
    isEditMode,
    detailGroup,
  } = useGroupsStore();

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── Fetch groups (server-paginated + server-filtered) ──────────────────
  const fetchGroups = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await GroupsApi.listGroups({
        page,
        limit,
        search: searchQuery || undefined,
        createdAfter: filters.createdAfter || undefined,
        createdBefore: filters.createdBefore || undefined,
      });
      setGroups(result.groups, result.totalCount);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load groups';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [page, limit, searchQuery, filters, setGroups, setLoading, setError]);

  useEffect(() => {
    if (isProfileInitialized && isAdmin) {
      fetchGroups();
    }
  }, [fetchGroups, isProfileInitialized, isAdmin]);

  // URL ↔ Store panel sync — see docs/url-driven-panel-state.md
  const pendingUrlRef = useRef<string | null>(null);
  const initialUrlProcessed = useRef(false);

  // Build a comparison key from panel params
  const buildUrlKey = useCallback(
    (panel: string | null, groupId: string | null, mode: string | null) =>
      `${panel ?? ''}|${groupId ?? ''}|${mode ?? ''}`,
    []
  );

  // URL → Store: read query params and open/close panels
  useEffect(() => {
    const panel = searchParams.get('panel');
    const groupId = searchParams.get('groupId');
    const mode = searchParams.get('mode');
    const urlKey = buildUrlKey(panel, groupId, mode);

    // Skip if this URL was set by our Store→URL sync
    if (pendingUrlRef.current === urlKey) {
      pendingUrlRef.current = null;
      return;
    }

    // Read store state directly (avoids adding store values as deps)
    const store = useGroupsStore.getState();

    if (panel === 'create') {
      if (!store.isCreatePanelOpen) openCreatePanel();
      initialUrlProcessed.current = true;
      return;
    }

    if (panel === 'detail' && groupId) {
      const alreadyShowing =
        store.isDetailPanelOpen && store.detailGroup?._id === groupId;

      if (alreadyShowing) {
        // Already showing this group — just sync edit mode
        if (mode === 'edit' && !store.isEditMode) enterEditMode();
        else if (mode !== 'edit' && store.isEditMode) exitEditMode();
        initialUrlProcessed.current = true;
      } else {
        // Need to open the panel for this group
        const existing = store.groups.find((g) => g._id === groupId);
        if (existing) {
          openDetailPanel(existing);
          if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
          initialUrlProcessed.current = true;
        } else if (!store.isLoading) {
          // Groups loaded but not in list — try API
          GroupsApi.getGroup(groupId)
            .then((group) => {
              openDetailPanel(group);
              if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
              initialUrlProcessed.current = true;
            })
            .catch(() => {
              initialUrlProcessed.current = true;
              pendingUrlRef.current = buildUrlKey(null, null, null);
              router.replace('/workspace/groups/');
            });
        }
        // If still loading, initialUrlProcessed stays false → Store→URL won't fire yet
      }
      return;
    }

    // No panel param — close any open panels (only after first load)
    if (initialUrlProcessed.current) {
      if (store.isCreatePanelOpen) closeCreatePanel();
      if (store.isDetailPanelOpen) closeDetailPanel();
    }
    initialUrlProcessed.current = true;
  }, [searchParams]);

  // Group resolver: retry opening the panel once groups finish loading
  useEffect(() => {
    if (isLoading || groups.length === 0) return;

    const panel = searchParams.get('panel');
    const groupId = searchParams.get('groupId');
    const mode = searchParams.get('mode');

    // Only act if URL wants a detail panel that isn't open yet
    if (panel !== 'detail' || !groupId) return;
    const store = useGroupsStore.getState();
    if (store.isDetailPanelOpen && store.detailGroup?._id === groupId) return;

    const existing = groups.find((g) => g._id === groupId);
    if (existing) {
      openDetailPanel(existing);
      if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
      initialUrlProcessed.current = true;
    } else {
      GroupsApi.getGroup(groupId)
        .then((group) => {
          openDetailPanel(group);
          if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
          initialUrlProcessed.current = true;
        })
        .catch(() => {
          initialUrlProcessed.current = true;
          pendingUrlRef.current = buildUrlKey(null, null, null);
          router.replace('/workspace/groups/');
        });
    }
  }, [groups, isLoading]);

  // Store → URL: when store panel state changes, update the URL
  useEffect(() => {
    if (!initialUrlProcessed.current) return;

    let targetPanel: string | null = null;
    let targetGroupId: string | null = null;
    let targetMode: string | null = null;

    if (isCreatePanelOpen) {
      targetPanel = 'create';
    } else if (isDetailPanelOpen && detailGroup) {
      targetPanel = 'detail';
      targetGroupId = detailGroup._id;
      if (isEditMode) targetMode = 'edit';
    }

    const currentPanel = searchParams.get('panel');
    const currentGroupId = searchParams.get('groupId');
    const currentMode = searchParams.get('mode');

    if (
      targetPanel !== currentPanel ||
      targetGroupId !== currentGroupId ||
      targetMode !== currentMode
    ) {
      pendingUrlRef.current = buildUrlKey(targetPanel, targetGroupId, targetMode);

      const params = new URLSearchParams();
      if (targetPanel) params.set('panel', targetPanel);
      if (targetGroupId) params.set('groupId', targetGroupId);
      if (targetMode) params.set('mode', targetMode);

      const query = params.toString();
      router.replace(query ? `/workspace/groups/?${query}` : '/workspace/groups/');
    }
  }, [isCreatePanelOpen, isDetailPanelOpen, isEditMode, detailGroup, searchParams, router, buildUrlKey]);

  // ── URL-based panel navigation helpers ──
  const navigateToCreatePanel = useCallback(() => {
    router.push('/workspace/groups/?panel=create');
  }, [router]);

  const navigateToDetailPanel = useCallback(
    (group: Group) => {
      router.push(`/workspace/groups/?panel=detail&groupId=${group._id}`);
    },
    [router]
  );

  // ── Filter chips with translated labels ──
  const filterChips = useMemo<FilterChipConfig[]>(
    () =>
      GROUPS_FILTER_CHIPS.map((chip) => ({
        ...chip,
        label: t(`workspace.filters.${chip.key}`) || chip.label,
      })),
    [t]
  );

  // ── Render individual filter components ──
  const renderFilter = useCallback(
    (filter: FilterChipConfig) => {
      switch (filter.key) {
        case 'createdOn':
          return (
            <DateRangePicker
              label={filter.label}
              icon={filter.icon}
              startDate={filters.createdAfter}
              endDate={filters.createdBefore}
              dateType={filters.createdDateType}
              onApply={(startDate, endDate, dateType) =>
                setFilters({
                  createdAfter: dateType === 'before' ? undefined : startDate,
                  createdBefore: dateType === 'after' ? undefined
                    : dateType === 'on' ? startDate
                    : endDate || startDate,
                  createdDateType: dateType,
                })
              }
              onClear={() =>
                setFilters({
                  createdAfter: undefined,
                  createdBefore: undefined,
                  createdDateType: undefined,
                })
              }
              defaultDateType="between"
            />
          );
        default:
          return null;
      }
    },
    [filters, setFilters]
  );

  // Date filter is now server-side; use groups directly
  const filteredGroups = groups;

  // ── Column definitions ──────────────────

  const columns = useMemo<ColumnConfig<Group>[]>(
    () => [
      {
        key: 'name',
        label: t('workspace.groups.columns.name'),
        width: '20%',
        minWidth: '160px',
        render: (group) => (
          <AvatarCell name={group.name} />
        ),
      },
      {
        key: 'description',
        label: t('workspace.groups.columns.description'),
        minWidth: '200px',
        render: () => (
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            -
          </Text>
        ),
      },
      {
        key: 'users',
        label: t('workspace.groups.columns.users'),
        width: '80px',
        render: (group) => (
          <Badge variant="soft" color="gray" size="1">
            {group.userCount ?? 0}
          </Badge>
        ),
      },
      {
        key: 'createdBy',
        label: t('workspace.groups.columns.createdBy'),
        width: '20%',
        minWidth: '140px',
        render: () => (
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            -
          </Text>
        ),
      },
      {
        key: 'createdOn',
        label: t('workspace.groups.columns.createdOn'),
        width: '140px',
        render: (group) => (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {group.createdAt ? formatDate(group.createdAt) : '-'}
          </Text>
        ),
      },
    ],
    [t]
  );

  // ── Row actions ────────────
  const renderRowActions = useCallback(
    (group: Group) => {
      const actions: RowAction[] = [
        {
          icon: 'group',
          label: t('workspace.groups.actions.viewGroup'),
          onClick: () => {
            navigateToDetailPanel(group);
          },
        },
        {
          icon: 'delete',
          label: t('workspace.groups.actions.delete'),
          variant: 'danger',
          separatorBefore: true,
          onClick: async () => {
            try {
              await GroupsApi.deleteGroup(group._id);
              addToast({
                variant: 'success',
                title: t('workspace.groups.actions.deleteSuccess', 'Group deleted'),
                duration: 3000,
              });
              fetchGroups();
            } catch {
              addToast({
                variant: 'error',
                title: t('workspace.groups.actions.deleteError', 'Failed to delete group'),
                duration: 5000,
              });
            }
          },
        },
      ];
      return <EntityRowActionMenu actions={actions} />;
    },
    [t, navigateToDetailPanel, fetchGroups, addToast]
  );

  // ── Empty state ──
  const hasActiveFilters = !!(
    searchQuery.trim() ||
    filters.createdAfter ||
    filters.createdBefore
  );
  const isEmpty = !isLoading && groups.length === 0 && !hasActiveFilters;
  const isEmptyFiltered = !isLoading && groups.length === 0 && hasActiveFilters;

  // Guard: don't render until profile is resolved / redirect non-admin users
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── Render ──────────────────────────────

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingLeft: '40px',
        paddingRight: '40px',
      }}
    >
      {/* Header */}
      <EntityPageHeader
        title={t('workspace.groups.title')}
        subtitle={t('workspace.groups.subtitle')}
        searchPlaceholder={t('workspace.groups.searchPlaceholder')}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        ctaLabel={t('workspace.groups.createGroup')}
        ctaIcon="group_add"
        onCtaClick={navigateToCreatePanel}
      />

      {/* Content */}
      <Flex
        direction="column"
        style={{
          flex: 1,
          overflow: 'hidden',
        }}
      >
        {isEmpty ? (
          <EntityEmptyState
            icon="group"
            title={t('workspace.groups.emptyTitle')}
            description={t('workspace.groups.emptyDescription')}
            ctaLabel={t('workspace.groups.createGroup')}
            ctaIcon="group_add"
            onCtaClick={navigateToCreatePanel}
          />
        ) : (
          <Flex
            direction="column"
            style={{
              flex: 1,
              overflow: 'hidden',
              border: '1px solid var(--slate-6)',
              borderRadius: 'var(--radius-3)',
            }}
          >
            {/* Filter bar */}
            <EntityFilterBar filters={filterChips} renderFilter={renderFilter} />

            {isEmptyFiltered ? (
              <Flex
                direction="column"
                align="center"
                justify="center"
                gap="2"
                style={{ flex: 1, padding: 'var(--space-6)' }}
              >
                <MaterialIcon name="filter_list_off" size={32} color="var(--slate-8)" />
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.groups.noFilterResults', 'No groups match the applied filters')}
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  {t('workspace.groups.noFilterResultsHint', 'Try adjusting or clearing the filters above')}
                </Text>
              </Flex>
            ) : (
              <>
                {/* Data table */}
                <EntityDataTable<Group>
                  columns={columns}
                  data={filteredGroups}
                  getItemId={(g) => g._id}
                  selectedIds={selectedGroups}
                  onSelectionChange={setSelectedGroups}
                  renderRowActions={renderRowActions}
                  isLoading={isLoading}
                  onRowClick={(group) => navigateToDetailPanel(group)}
                />

                {/* Pagination */}
                <EntityPagination
                  page={page}
                  limit={limit}
                  totalCount={totalCount}
                  onPageChange={setPage}
                  onLimitChange={setLimit}
                />
              </>
            )}
          </Flex>
        )}
      </Flex>

      {/* ── Panels ── */}
      <CreateGroupSidebar onCreateSuccess={fetchGroups} />
      <GroupDetailSidebar onUpdateSuccess={fetchGroups} />
    </Flex>
  );
}

export default function GroupsPage() {
  return (
    <Suspense>
      <GroupsPageContent />
    </Suspense>
  );
}
