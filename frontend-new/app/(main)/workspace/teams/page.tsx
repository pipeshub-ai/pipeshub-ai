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
import type { DateFilterType } from '@/app/components/ui/date-range-picker';
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
import type { Team } from './types';
import { CreateTeamSidebar, TeamDetailSidebar } from './components';

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

/** Check if a timestamp (ms) falls within a date range */
function isInDateRange(
  timestampMs: number | undefined,
  afterDate?: string,
  beforeDate?: string,
  dateType?: DateFilterType
): boolean {
  if (!timestampMs) return false;
  if (!afterDate && !beforeDate) return true;

  const itemDate = new Date(timestampMs);
  itemDate.setHours(0, 0, 0, 0);

  if (dateType === 'on' && afterDate) {
    const target = new Date(afterDate);
    target.setHours(0, 0, 0, 0);
    return itemDate.getTime() === target.getTime();
  }
  if (dateType === 'before' && beforeDate) {
    const before = new Date(beforeDate);
    before.setHours(0, 0, 0, 0);
    return itemDate < before;
  }
  if (dateType === 'after' && afterDate) {
    const after = new Date(afterDate);
    after.setHours(0, 0, 0, 0);
    return itemDate > after;
  }
  // between
  if (afterDate && beforeDate) {
    const after = new Date(afterDate);
    after.setHours(0, 0, 0, 0);
    const before = new Date(beforeDate);
    before.setHours(23, 59, 59, 999);
    return itemDate >= after && itemDate <= before;
  }
  return true;
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

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // Prevent rendering (and running data-fetching effects) while profile is
  // unresolved or before the redirect fires for confirmed non-admin users.
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

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

  // ── Fetch teams on mount and on page/search change ──
  const fetchTeams = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await TeamsApi.listTeams({
        page,
        limit,
        search: searchQuery || undefined,
      });
      setTeams(result.teams, result.totalCount);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load teams';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [page, limit, searchQuery, setTeams, setLoading, setError]);

  useEffect(() => {
    fetchTeams();
  }, [fetchTeams]);

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

  // ── Dynamic options for Created By filter ──
  const creatorOptions = useMemo(() => {
    const creatorsMap = new Map<string, { name: string; email: string }>();
    for (const team of teams) {
      const creator = findCreator(team);
      if (creator && !creatorsMap.has(creator.id)) {
        creatorsMap.set(creator.id, {
          name: creator.userName,
          email: creator.userEmail,
        });
      }
    }
    return Array.from(creatorsMap.entries()).map(([id, c]) => ({
      value: id,
      label: c.name || c.email,
      icon: 'person',
    }));
  }, [teams]);

  // ── Render individual filter components ──
  const renderFilter = useCallback(
    (filter: FilterChipConfig) => {
      switch (filter.key) {
        case 'createdBy':
          return (
            <FilterDropdown
              label={filter.label}
              icon={filter.icon}
              options={creatorOptions}
              selectedValues={filters.createdBy || []}
              onSelectionChange={(values) => setFilters({ createdBy: values })}
              searchable
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
    [filters, setFilters, creatorOptions]
  );

  // ── Client-side search + filter ──
  const filteredTeams = useMemo(() => {
    let result = teams;

    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (team) =>
          team.name?.toLowerCase().includes(q) ||
          team.description?.toLowerCase().includes(q)
      );
    }

    // Created By filter
    if (filters.createdBy?.length) {
      const selectedCreators = new Set(filters.createdBy);
      result = result.filter((team) => team.createdBy && selectedCreators.has(team.createdBy));
    }

    // Created On date filter
    if (filters.createdAfter || filters.createdBefore) {
      result = result.filter((team) =>
        isInDateRange(
          team.createdAtTimestamp,
          filters.createdAfter,
          filters.createdBefore,
          filters.createdDateType
        )
      );
    }

    return result;
  }, [teams, searchQuery, filters]);

  // ── Paginated slice ──
  const paginatedTeams = useMemo(() => {
    // If server already paginates, totalCount > 0 and teams.length <= limit
    if (totalCount > 0 && teams.length <= limit) return filteredTeams;
    // Client-side pagination fallback
    const start = (page - 1) * limit;
    return filteredTeams.slice(start, start + limit);
  }, [filteredTeams, page, limit, totalCount, teams.length]);

  const effectiveTotalCount = totalCount > 0 ? totalCount : filteredTeams.length;

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
  const isEmpty = !isLoading && teams.length === 0;

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
        {isEmpty ? (
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
