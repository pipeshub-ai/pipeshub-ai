'use client';

import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Box, Button, Flex, IconButton, Text, TextField } from '@radix-ui/themes';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { PaginationFooter } from '@/app/components/data-display/pagination-footer';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { EmptyState, NoResults } from '@/app/components/ui/empty-state';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { AGENTS_CHANGED_EVENT } from '@/app/(main)/chat/constants';
import { useUserStore } from '@/lib/store/user-store';
import { useAuthStore, useUserPermission } from '@/config';
import { AgentsApi } from '../../api';
import type { AgentListRecord } from '../../types';
import { AgentIndexRow, agentIndexGrid, agentKeyOf } from './agent-index-row';

const DEFAULT_PAGE_SIZE = 50;
/** The most the list API returns in one page. */
const MAX_PAGE_SIZE = 200;
/** A creator id the users lookup accepts (a Mongo id); anything else, such as "system", stays unnamed. */
const USER_ID_PATTERN = /^[a-f0-9]{24}$/i;

/** One page of the list as the server sees it; also what the URL keeps, as on All records. */
interface ListQuery {
  search: string;
  page: number;
  limit: number;
}

interface ListResult extends ListQuery {
  agents: AgentListRecord[];
  totalItems: number;
}

function parseListQuery(params: URLSearchParams): ListQuery {
  const page = Number.parseInt(params.get('page') ?? '', 10);
  const limit = Number.parseInt(params.get('limit') ?? '', 10);
  return {
    search: params.get('search')?.trim() ?? '',
    page: page > 0 ? page : 1,
    limit: limit > 0 ? Math.min(limit, MAX_PAGE_SIZE) : DEFAULT_PAGE_SIZE,
  };
}

function serializeListQuery({ search, page, limit }: ListQuery): string {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (page > 1) params.set('page', String(page));
  if (limit !== DEFAULT_PAGE_SIZE) params.set('limit', String(limit));
  return params.toString();
}

const currentUserId = () => useUserStore.getState().profile?.userId ?? useAuthStore.getState().user?.id ?? null;

const HEADER_CELL: CSSProperties = {
  height: 'calc(var(--space-6) + var(--space-1))',
  padding: 'var(--space-2)',
  boxSizing: 'border-box',
  display: 'flex',
  alignItems: 'center',
};

/** The quiet Find of the Collections, All records and All artifacts headers (28px), on tokens. */
const FIND_BUTTON: CSSProperties = {
  height: 'calc(var(--space-5) + var(--space-1))',
  padding: '0 var(--space-2)',
  gap: 'calc(var(--space-1) * 1.5)',
  margin: 0,
  fontWeight: 'var(--font-weight-regular)',
  color: 'var(--text-secondary)',
  borderRadius: 'var(--radius-2)',
};

/** The Agents index: every agent you can use, a page at a time, with its owner and when it last changed. */
export function AgentsIndex() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useIsMobile();
  const canCreateAgent = useUserPermission('createAgent');
  const profileUserId = useUserStore((s) => s.profile?.userId);
  const authUserId = useAuthStore((s) => s.user?.id);
  const myId = profileUserId ?? authUserId ?? null;

  // Seeded from the URL once, so a reload or a shared link opens the same search and page.
  const [query, setQuery] = useState<ListQuery>(() => parseListQuery(searchParams));
  const [searchInput, setSearchInput] = useState(query.search);
  const [searchOpen, setSearchOpen] = useState(query.search !== '');
  const debouncedSearch = useDebouncedSearch(searchInput, 300).trim();

  const [result, setResult] = useState<ListResult | null>(null);
  const [ownerNames, setOwnerNames] = useState<Map<string, string>>(() => new Map());
  const [isLoading, setIsLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const requestId = useRef(0);
  const knownOwners = useRef(new Set<string>());

  const load = useCallback(async (q: ListQuery) => {
    const id = ++requestId.current;
    let movedPage = false;
    setIsLoading(true);
    try {
      const { agents, pagination } = await AgentsApi.getAgents({ page: q.page, limit: q.limit, search: q.search || undefined });
      if (id !== requestId.current) return;
      // Past the last page (an old link, or its last agent went): show the last page instead.
      if (agents.length === 0 && pagination.totalPages > 0 && q.page > pagination.totalPages) {
        movedPage = true;
        setQuery((prev) => ({ ...prev, page: pagination.totalPages }));
        return;
      }
      const rows = agents.filter((a) => Boolean(agentKeyOf(a)));
      const me = currentUserId();
      const unknown = [...new Set(rows.map((a) => a.createdBy))].filter(
        (owner) => owner && owner !== me && USER_ID_PATTERN.test(owner) && !knownOwners.current.has(owner),
      );
      if (unknown.length > 0) {
        try {
          const users = await UsersApi.getUsersByIds(unknown);
          unknown.forEach((owner) => knownOwners.current.add(owner));
          setOwnerNames((prev) => {
            const next = new Map(prev);
            for (const user of users) {
              const label = user.name?.trim() || user.email?.trim();
              if (user.id && label) next.set(user.id, label);
            }
            return next;
          });
        } catch {
          // Owners stay unnamed; the list itself still shows.
        }
      }
      if (id !== requestId.current) return;
      setResult({ ...q, agents: rows, totalItems: pagination.totalItems });
      setLoadFailed(false);
    } catch {
      if (id !== requestId.current) return;
      setLoadFailed(true);
    } finally {
      if (id === requestId.current && !movedPage) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(query);
    const reload = () => void load(query);
    window.addEventListener(AGENTS_CHANGED_EVENT, reload);
    return () => window.removeEventListener(AGENTS_CHANGED_EVENT, reload);
  }, [load, query]);

  // A new search starts from the first page, as on the other index pages.
  useEffect(() => {
    setQuery((prev) => (prev.search === debouncedSearch ? prev : { ...prev, search: debouncedSearch, page: 1 }));
  }, [debouncedSearch]);

  // Written back as All records writes its own: replaceState needs no route fetch, and Next keeps
  // useSearchParams in step with it.
  const queryString = serializeListQuery(query);
  useEffect(() => {
    if (queryString === searchParams.toString()) return;
    window.history.replaceState(null, '', queryString ? `/agents/?${queryString}` : '/agents/');
  }, [queryString, searchParams]);

  const closeSearch = () => {
    setSearchOpen(false);
    setSearchInput('');
  };

  const handleDeleted = useCallback((deletedId: string) => {
    setResult((prev) => {
      if (!prev) return prev;
      const agents = prev.agents.filter((a) => agentKeyOf(a) !== deletedId);
      // The last row of a later page stays until the page before it loads, so no empty state flashes.
      return agents.length === 0 && prev.page > 1 ? prev : { ...prev, agents, totalItems: Math.max(0, prev.totalItems - 1) };
    });
    // Also reloads this page from the server, which steps back a page when this one is now past the end.
    window.dispatchEvent(new Event(AGENTS_CHANGED_EVENT));
  }, []);

  // Only the first load shows a skeleton; a new page or search keeps the rows until it arrives.
  const firstLoad = isLoading && result === null;
  const skeleton = useDeferredLoading(firstLoad);
  const showSkeleton = firstLoad || skeleton.visible;
  const showError = loadFailed && !isLoading;
  const ready = result !== null && !showSkeleton && !showError;

  const ownerOf = (agent: AgentListRecord) =>
    myId != null && agent.createdBy === myId ? t('common.you') : (ownerNames.get(agent.createdBy) ?? '');
  const grid = agentIndexGrid(isMobile);

  let body: ReactNode = null;
  if (showSkeleton) {
    body = (
      <ListSkeleton
        variant="table-row"
        rowHeight={63}
        hasCheckbox={false}
        actionsWidth={40}
        columns={[{ minWidth: '160px', barWidth: 0.5, glyph: { size: 20 } }, { width: '110px' }, ...(isMobile ? [] : [{ width: '96px' }])]}
        statusLabel={t('agents.index.loading')}
        exiting={skeleton.exiting}
      />
    );
  } else if (ready && result.agents.length > 0) {
    body = result.agents.map((agent) => (
      <AgentIndexRow key={agentKeyOf(agent)} agent={agent} owner={ownerOf(agent)} onDeleted={handleDeleted} />
    ));
  }

  let emptyState: ReactNode = null;
  if (ready && result.agents.length === 0) {
    emptyState = result.search ? (
      <NoResults
        icon={null}
        title={t('agents.index.noSearchMatchesTitle')}
        description={t('agents.index.noSearchMatchesDescription')}
      />
    ) : (
      <EmptyState
        title={t('agents.index.emptyTitle')}
        description={canCreateAgent ? t('agents.index.emptyDescriptionCanCreate') : t('agents.index.emptyDescription')}
      />
    );
  }

  return (
    <Flex direction="column" style={{ height: '100%', minHeight: 0 }}>
      <Flex
        align="center"
        justify="between"
        gap="3"
        style={{
          height: 'var(--space-7)',
          paddingBlock: 'var(--space-1)',
          paddingInline: 'var(--space-3)',
          boxSizing: 'border-box',
          backdropFilter: 'blur(var(--space-2))',
          WebkitBackdropFilter: 'blur(var(--space-2))',
          backgroundColor: 'var(--effects-translucent)',
          borderBottom: '1px solid var(--border-subtle)',
          flexShrink: 0,
        }}
      >
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <SidebarExpandButton placement="inline" />
          <MaterialIcon name="smart_toy" size={16} color="var(--text-secondary)" />
          <Text size="2" weight="medium" style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
            {t('agents.pageTitle')}
          </Text>
        </Flex>
        {/* Find, then the page's primary furthest right: the Collections header's order. */}
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {searchOpen ? (
            <IconButton
              variant="ghost"
              size="2"
              color="gray"
              className="ghost-box touch-hit"
              onClick={closeSearch}
              aria-label={t('agents.index.closeSearch')}
            >
              <MaterialIcon name="close" size={18} color="var(--text-secondary)" />
            </IconButton>
          ) : isMobile ? (
            <IconButton
              variant="ghost"
              size="2"
              color="gray"
              className="ghost-box touch-hit"
              onClick={() => setSearchOpen(true)}
              aria-label={t('action.find')}
            >
              <MaterialIcon name="search" size={18} color="var(--text-secondary)" />
            </IconButton>
          ) : (
            <Button variant="ghost" size="1" color="gray" onClick={() => setSearchOpen(true)} style={FIND_BUTTON}>
              <MaterialIcon name="search" size={14} color="var(--text-secondary)" />
              {t('action.find')}
            </Button>
          )}
          {canCreateAgent && (
            <Button size="1" variant="solid" className="touch-hit" onClick={() => router.push('/agents/new')}>
              <MaterialIcon name="add" size={16} />
              {t('chat.newAgent')}
            </Button>
          )}
        </Flex>
      </Flex>

      {searchOpen && (
        <Flex
          style={{
            padding: 'var(--space-2) var(--space-4)',
            backgroundColor: 'var(--surface-subtle)',
            borderBottom: '1px solid var(--border-subtle)',
            flexShrink: 0,
          }}
        >
          <TextField.Root
            autoFocus
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') closeSearch();
            }}
            placeholder={t('agents.index.searchPlaceholder')}
            aria-label={t('agents.index.searchLabel')}
            style={{ width: '100%' }}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--text-secondary)" />
            </TextField.Slot>
          </TextField.Root>
        </Flex>
      )}

      {showError ? (
        <EmptyState
          title={t('agents.index.loadFailedTitle')}
          description={t('agents.index.loadFailedDescription')}
          action={
            <Button variant="soft" color="gray" onClick={() => void load(query)}>
              {t('action.tryAgain')}
            </Button>
          }
        />
      ) : (
        <Box
          className="no-scrollbar"
          aria-label={t('agents.pageTitle')}
          aria-busy={isLoading || undefined}
          style={{ flex: 1, minHeight: 0, overflow: 'auto' }}
        >
          {/* One block the header and every row stretch across, so on a narrow page the fills and
              hairlines scroll sideways with the cells instead of stopping short. */}
          <Box style={{ minWidth: grid.minWidth }}>
            <Box
              style={{
                display: 'grid',
                gridTemplateColumns: grid.columns,
                alignItems: 'center',
                paddingInline: 'var(--space-2)',
                position: 'sticky',
                top: 0,
                zIndex: 1,
                backgroundColor: 'var(--surface-subtle)',
                borderBottom: '1px solid var(--border-subtle)',
              }}
            >
              {[t('agents.index.columnAgent'), t('agents.index.columnOwner'), ...(isMobile ? [] : [t('table.updated')])].map((label) => (
                <Box key={label} style={HEADER_CELL}>
                  <Text size="1" style={{ color: 'var(--text-secondary)' }}>
                    {label}
                  </Text>
                </Box>
              ))}
              <Box style={HEADER_CELL} />
            </Box>
            {body}
          </Box>
          {emptyState}
        </Box>
      )}

      {ready && result.totalItems > 0 && (
        <PaginationFooter
          pagination={{
            page: result.page,
            limit: result.limit,
            totalCount: result.totalItems,
            totalPages: Math.ceil(result.totalItems / result.limit),
          }}
          onPageChange={(page) => setQuery((prev) => ({ ...prev, page }))}
          onLimitChange={(limit) => setQuery((prev) => ({ ...prev, limit, page: 1 }))}
        />
      )}
    </Flex>
  );
}
