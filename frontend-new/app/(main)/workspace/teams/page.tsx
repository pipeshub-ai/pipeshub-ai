'use client';

import React, { useEffect, useMemo, useCallback, useRef, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { formatDate } from '@/lib/utils/formatters';
import { FilterDropdown, DateRangePicker } from '@/app/components/ui';
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
import { useTeamsStore } from './store';
import { TeamsApi } from './api';
import { UsersApi } from '../users/api';
import type { Team } from './types';
import { CreateTeamSidebar, TeamDetailSidebar } from './components';
import { usePaginatedFilterOptions } from '../hooks/use-paginated-filter-options';

// ========================================
// Constants
// ========================================

const TEAMS_FILTER_CHIPS: FilterChipConfig[] = [
  { key: 'createdBy', label: 'Created By', icon: 'person' },
  { key: 'createdOn', label: 'Created On', icon: 'calendar_today' },
];

// ========================================
// Helpers
// ========================================

/** Find the creator's member entry in the team members list */
function findCreator(team: Team) {
  if (!team.createdBy || !team.members?.length) return null;
  return team.members.find((m) => m.id === team.createdBy) ?? null;
}


// ========================================
// Page Component
// ========================================

function TeamsPageContent() {
  const { t } = useTranslation();
  const currentUser = useAuthStore((s) => s.user);
  const addToast = useToastStore((s) => s.addToast);
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const {
    teams,
    selectedTeams,
    page,
    limit,
    totalCount,
    searchQuery,
    filters,
    isLoading,
    setTeams,
    setSelectedTeams,
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
    detailTeam,
  } = useTeamsStore();

  // ── Paginated user filter (for "Created By" filter) ──
  const userFilter = usePaginatedFilterOptions({
    fetcher: async (search, page, limit) => {
      const { users, totalCount } = await UsersApi.listGraphUsers({ page, limit, search });
      return { items: users, totalCount };
    },
    mapOption: (u) => ({
      value: u.id,
      label: u.name || u.email || 'Unknown User',
      icon: 'person',
    }),
  });

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── Fetch teams (server-paginated + server-filtered) ──
  const fetchTeams = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Parameters<typeof TeamsApi.listTeams>[0] = {
        page,
        limit,
        search: searchQuery || undefined,
      };
      // Created By filter
      if (filters.createdBy?.length === 1) {
        params.created_by = filters.createdBy[0];
      }
      // Created On date filter
      if (filters.createdAfter) {
        params.created_after = new Date(filters.createdAfter).getTime();
      }
      if (filters.createdBefore) {
        const d = new Date(filters.createdBefore);
        d.setHours(23, 59, 59, 999);
        params.created_before = d.getTime();
      }

      const result = await TeamsApi.listTeams(params);
      setTeams(result.teams, result.totalCount);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load teams';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [page, limit, searchQuery, filters, setTeams, setLoading, setError]);

  useEffect(() => {
    if (isProfileInitialized && isAdmin) {
      fetchTeams();
    }
  }, [fetchTeams, isProfileInitialized, isAdmin]);

  // ── URL ↔ Store panel sync (see docs/url-driven-panel-state.md) ──
  const pendingUrlRef = useRef<string | null>(null);
  const initialUrlProcessed = useRef(false);

  const buildUrlKey = useCallback(
    (panel: string | null, teamId: string | null, mode: string | null) =>
      `${panel ?? ''}|${teamId ?? ''}|${mode ?? ''}`,
    []
  );

  // URL → Store: read query params and open/close panels
  useEffect(() => {
    const panel = searchParams.get('panel');
    const teamId = searchParams.get('teamId');
    const mode = searchParams.get('mode');
    const urlKey = buildUrlKey(panel, teamId, mode);

    // Skip if this URL was set by our Store→URL sync
    if (pendingUrlRef.current === urlKey) {
      pendingUrlRef.current = null;
      return;
    }

    const store = useTeamsStore.getState();

    if (panel === 'create') {
      if (!store.isCreatePanelOpen) openCreatePanel();
      initialUrlProcessed.current = true;
      return;
    }

    if (panel === 'detail' && teamId) {
      const alreadyShowing =
        store.isDetailPanelOpen && store.detailTeam?.id === teamId;

      if (alreadyShowing) {
        if (mode === 'edit' && !store.isEditMode) enterEditMode();
        else if (mode !== 'edit' && store.isEditMode) exitEditMode();
        initialUrlProcessed.current = true;
      } else {
        const existing = store.teams.find((t) => t.id === teamId);
        if (existing) {
          openDetailPanel(existing);
          if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
          initialUrlProcessed.current = true;
        } else if (!store.isLoading) {
          TeamsApi.getTeam(teamId)
            .then((team) => {
              openDetailPanel(team);
              if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
              initialUrlProcessed.current = true;
            })
            .catch(() => {
              initialUrlProcessed.current = true;
              pendingUrlRef.current = buildUrlKey(null, null, null);
              router.replace('/workspace/teams/');
            });
        }
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

  // Team resolver: retry opening the panel once teams finish loading
  useEffect(() => {
    if (isLoading || teams.length === 0) return;

    const panel = searchParams.get('panel');
    const teamId = searchParams.get('teamId');
    const mode = searchParams.get('mode');

    if (panel !== 'detail' || !teamId) return;
    const store = useTeamsStore.getState();
    if (store.isDetailPanelOpen && store.detailTeam?.id === teamId) return;

    const existing = teams.find((t) => t.id === teamId);
    if (existing) {
      openDetailPanel(existing);
      if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
      initialUrlProcessed.current = true;
    } else {
      TeamsApi.getTeam(teamId)
        .then((team) => {
          openDetailPanel(team);
          if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
          initialUrlProcessed.current = true;
        })
        .catch(() => {
          initialUrlProcessed.current = true;
          pendingUrlRef.current = buildUrlKey(null, null, null);
          router.replace('/workspace/teams/');
        });
    }
  }, [teams, isLoading]);

  // Store → URL: when store panel state changes, update the URL
  useEffect(() => {
    if (!initialUrlProcessed.current) return;

    let targetPanel: string | null = null;
    let targetTeamId: string | null = null;
    let targetMode: string | null = null;

    if (isCreatePanelOpen) {
      targetPanel = 'create';
    } else if (isDetailPanelOpen && detailTeam) {
      targetPanel = 'detail';
      targetTeamId = detailTeam.id;
      if (isEditMode) targetMode = 'edit';
    }

    const currentPanel = searchParams.get('panel');
    const currentTeamId = searchParams.get('teamId');
    const currentMode = searchParams.get('mode');

    if (
      targetPanel !== currentPanel ||
      targetTeamId !== currentTeamId ||
      targetMode !== currentMode
    ) {
      pendingUrlRef.current = buildUrlKey(targetPanel, targetTeamId, targetMode);

      const params = new URLSearchParams();
      if (targetPanel) params.set('panel', targetPanel);
      if (targetTeamId) params.set('teamId', targetTeamId);
      if (targetMode) params.set('mode', targetMode);

      const query = params.toString();
      router.replace(query ? `/workspace/teams/?${query}` : '/workspace/teams/');
    }
  }, [isCreatePanelOpen, isDetailPanelOpen, isEditMode, detailTeam, searchParams, router, buildUrlKey]);

  // ── URL-based panel navigation helpers ──
  const navigateToCreatePanel = useCallback(() => {
    router.push('/workspace/teams/?panel=create');
  }, [router]);

  const navigateToDetailPanel = useCallback(
    (team: Team) => {
      router.push(`/workspace/teams/?panel=detail&teamId=${team.id}`);
    },
    [router]
  );

  // ── Filter chips with translated labels ──
  const filterChips = useMemo<FilterChipConfig[]>(
    () =>
      TEAMS_FILTER_CHIPS.map((chip) => ({
        ...chip,
        label: t(`workspace.filters.${chip.key}`) || chip.label,
      })),
    [t]
  );

  // ── Render individual filter components ──
  const renderFilter = useCallback(
    (filter: FilterChipConfig) => {
      switch (filter.key) {
        case 'createdBy':
          return (
            <FilterDropdown
              label={filter.label}
              icon={filter.icon}
              options={userFilter.options}
              selectedValues={filters.createdBy || []}
              onSelectionChange={(values) => setFilters({ createdBy: values })}
              searchable
              onSearch={userFilter.onSearch}
              onLoadMore={userFilter.onLoadMore}
              isLoadingMore={userFilter.isLoading}
              hasMore={userFilter.hasMore}
            />
          );
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
    [filters, setFilters, userFilter]
  );

  // Filtering and pagination are server-side; use teams directly
  const paginatedTeams = teams;
  const effectiveTotalCount = totalCount;

  // ── Column definitions ──────────────────

  const columns = useMemo<ColumnConfig<Team>[]>(
    () => [
      {
        key: 'name',
        label: t('workspace.teams.columns.name'),
        width: '20%',
        minWidth: '160px',
        render: (team) => (
          <AvatarCell name={team.name} />
        ),
      },
      {
        key: 'description',
        label: t('workspace.teams.columns.description'),
        minWidth: '180px',
        render: (team) => (
          <Text
            size="2"
            style={{
              color: 'var(--slate-11)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {team.description || '-'}
          </Text>
        ),
      },
      {
        key: 'members',
        label: t('workspace.teams.columns.users'),
        width: '80px',
        render: (team) => (
          <Badge variant="soft" color="gray" size="1">
            {team.memberCount ?? team.members?.length ?? 0}
          </Badge>
        ),
      },
      {
        key: 'createdBy',
        label: t('workspace.teams.columns.createdBy'),
        width: '20%',
        minWidth: '180px',
        render: (team) => {
          const creator = findCreator(team);
          if (!creator) {
            return <Text size="2" style={{ color: 'var(--slate-9)' }}>-</Text>;
          }
          return (
            <AvatarCell
              name={creator.userName}
              email={creator.userEmail}
              isSelf={currentUser?.id === creator.id || currentUser?.email === creator.userEmail}
              profilePicture={creator.profilePicture}
            />
          );
        },
      },
      {
        key: 'createdOn',
        label: t('workspace.teams.columns.createdOn'),
        width: '140px',
        render: (team) => (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {team.createdAtTimestamp ? formatDate(team.createdAtTimestamp) : '-'}
          </Text>
        ),
      },
    ],
    [t, currentUser]
  );

  // ── Row actions ────────────
  const renderRowActions = useCallback(
    (team: Team) => {
      const actions: (RowAction | false)[] = [
        {
          icon: 'groups',
          label: t('workspace.teams.actions.viewTeam'),
          onClick: () => {
            navigateToDetailPanel(team);
          },
        },
        team.canDelete && {
          icon: 'delete',
          label: t('workspace.teams.actions.delete'),
          variant: 'danger' as const,
          separatorBefore: true,
          onClick: async () => {
            try {
              await TeamsApi.deleteTeam(team.id);
              addToast({
                variant: 'success',
                title: t('workspace.teams.actions.deleteSuccess', 'Team deleted'),
                duration: 3000,
              });
              fetchTeams();
            } catch {
              addToast({
                variant: 'error',
                title: t('workspace.teams.actions.deleteError', 'Failed to delete team'),
                duration: 5000,
              });
            }
          },
        },
      ];
      return <EntityRowActionMenu actions={actions} />;
    },
    [t, navigateToDetailPanel, fetchTeams, addToast]
  );

  // ── Empty state ──
  const hasActiveFilters = !!(
    searchQuery.trim() ||
    filters.createdBy?.length ||
    filters.createdAfter ||
    filters.createdBefore
  );
  const isEmpty = !isLoading && teams.length === 0 && !hasActiveFilters;
  const isEmptyFiltered = !isLoading && teams.length === 0 && hasActiveFilters;

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
        title={t('workspace.teams.title')}
        subtitle={t('workspace.teams.subtitle')}
        searchPlaceholder={t('workspace.teams.searchPlaceholder')}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        ctaLabel={t('workspace.teams.createTeam')}
        ctaIcon="groups"
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
        {isEmpty && !isEmptyFiltered ? (
          <EntityEmptyState
            icon="groups"
            title={t('workspace.teams.emptyTitle')}
            description={t('workspace.teams.emptyDescription')}
            ctaLabel={t('workspace.teams.createTeam')}
            ctaIcon="groups"
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
                  {t('workspace.teams.noFilterResults', 'No teams match the applied filters')}
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  {t('workspace.teams.noFilterResultsHint', 'Try adjusting or clearing the filters above')}
                </Text>
              </Flex>
            ) : (
              <>
                {/* Data table */}
                <EntityDataTable<Team>
                  columns={columns}
                  data={paginatedTeams}
                  getItemId={(team) => team.id}
                  selectedIds={selectedTeams}
                  onSelectionChange={setSelectedTeams}
                  renderRowActions={renderRowActions}
                  isLoading={isLoading}
                  onRowClick={(team) => navigateToDetailPanel(team)}
                />

                {/* Pagination */}
                <EntityPagination
                  page={page}
                  limit={limit}
                  totalCount={effectiveTotalCount}
                  onPageChange={setPage}
                  onLimitChange={setLimit}
                />
              </>
            )}
          </Flex>
        )}
      </Flex>

      {/* ── Panels ── */}
      <CreateTeamSidebar onCreateSuccess={fetchTeams} />
      <TeamDetailSidebar onUpdateSuccess={fetchTeams} />
    </Flex>
  );
}

export default function TeamsPage() {
  return (
    <Suspense>
      <TeamsPageContent />
    </Suspense>
  );
}
