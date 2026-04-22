'use client';

import { useCallback, useEffect, useRef, useState, Suspense } from 'react';
import { useRouter } from 'next/navigation';
import {
  Flex,
  Grid,
  Heading,
  Text,
  SegmentedControl,
  TextField,
  Button,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useToastStore } from '@/lib/store/toast-store';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import { YourMcpCard } from './components/your-mcp-card';
import type { MCPServerInstance } from '@/app/(main)/workspace/mcp-servers/types';

// ============================================================================
// Types
// ============================================================================

type AuthFilter = 'all' | 'authenticated' | 'not-authenticated';

const AUTH_FILTER_TABS = [
  { value: 'all' as AuthFilter, label: 'All' },
  { value: 'authenticated' as AuthFilter, label: 'Authenticated' },
  { value: 'not-authenticated' as AuthFilter, label: 'Not Authenticated' },
] as const;

const ITEMS_PER_PAGE = 20;

// ============================================================================
// Page content
// ============================================================================

function YourMcpsPageContent() {
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);

  const [servers, setServers] = useState<MCPServerInstance[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [activeFilter, setActiveFilter] = useState<AuthFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const pageRef = useRef(1);
  const searchRef = useRef('');
  const filterRef = useRef<AuthFilter>('all');
  const isInitialMount = useRef(true);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Data fetching ─────────────────────────────────────────────────────────

  const fetchServers = useCallback(
    async (page = 1, search = '', filter: AuthFilter = 'all', silent = false) => {
      if (page === 1 && !silent) setIsLoading(true);
      if (page > 1) setIsLoadingMore(true);

      try {
        const authStatus =
          filter === 'authenticated'
            ? 'authenticated'
            : filter === 'not-authenticated'
              ? 'unauthenticated'
              : undefined;

        const result = await McpServersApi.getMyMcpServers({
          page,
          limit: ITEMS_PER_PAGE,
          search: search || undefined,
          authStatus,
        });

        if (page === 1) {
          setServers(result.mcpServers);
        } else {
          setServers((prev) => [...prev, ...result.mcpServers]);
        }
        setHasMore(result.pagination.hasNext);
        pageRef.current = page;
      } catch {
        addToast({ variant: 'error', title: 'Failed to load MCP servers' });
      } finally {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    },
    [addToast]
  );

  // Initial load
  useEffect(() => {
    void fetchServers(1, '', 'all');
    return () => {
      isInitialMount.current = true;
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced search
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      searchRef.current = searchQuery;
      void fetchServers(1, searchQuery, filterRef.current);
    }, 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleFilterChange = useCallback(
    (val: string) => {
      const filter = val as AuthFilter;
      setActiveFilter(filter);
      filterRef.current = filter;
      void fetchServers(1, searchRef.current, filter);
    },
    [fetchServers]
  );

  const handleLoadMore = useCallback(() => {
    if (!hasMore || isLoadingMore) return;
    void fetchServers(pageRef.current + 1, searchRef.current, filterRef.current);
  }, [hasMore, isLoadingMore, fetchServers]);

  const handleRefresh = useCallback(() => {
    void fetchServers(1, searchRef.current, filterRef.current, true);
  }, [fetchServers]);

  const handleNotify = useCallback(
    (message: string, variant: 'success' | 'error' = 'success') => {
      addToast({ variant, title: message });
    },
    [addToast]
  );

  const handleOAuthSignIn = useCallback(
    async (server: MCPServerInstance) => {
      try {
        const result = await McpServersApi.oauthAuthorize(server.instanceId);
        // Store return URL so the OAuth callback can redirect back here
        sessionStorage.setItem('mcp_oauth_return_to', '/workspace/your-mcps');
        router.push(result.authorizationUrl);
      } catch {
        addToast({ variant: 'error', title: `Failed to start OAuth sign-in for ${server.displayName || server.instanceName}` });
      }
    },
    [addToast, router]
  );

  // ── Derived counts for tab labels ─────────────────────────────────────────

  const allCount = servers.length;
  const authCount = servers.filter((s) => s.isAuthenticated).length;
  const notAuthCount = servers.filter((s) => !s.isAuthenticated).length;

  const filteredServers =
    activeFilter === 'authenticated'
      ? servers.filter((s) => s.isAuthenticated)
      : activeFilter === 'not-authenticated'
        ? servers.filter((s) => !s.isAuthenticated)
        : servers;

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: 64,
        paddingBottom: 64,
        paddingLeft: 100,
        paddingRight: 100,
        overflowY: 'auto',
      }}
    >
      {/* ── Header ── */}
      <Flex justify="between" align="start" gap="2" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: 1 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Your MCPs
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            MCP servers configured by your administrator. Authenticate to use them in agents.
          </Text>
        </Flex>

        <TextField.Root
          size="2"
          placeholder="Search..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          autoComplete="off"
          style={{ width: 224, flexShrink: 0 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Flex>

      {/* ── Filter tabs ── */}
      <Flex align="center" gap="3">
        <SegmentedControl.Root
          value={activeFilter}
          onValueChange={handleFilterChange}
          size="2"
        >
          {AUTH_FILTER_TABS.map((tab) => (
            <SegmentedControl.Item key={tab.value} value={tab.value}>
              {tab.label}
              {!isLoading && activeFilter === 'all' && tab.value === 'all' && allCount > 0 && (
                <span style={{ marginLeft: 6, color: 'var(--gray-10)', fontSize: 12 }}>
                  {allCount}
                </span>
              )}
              {!isLoading && activeFilter === 'all' && tab.value === 'authenticated' && authCount > 0 && (
                <span style={{ marginLeft: 6, color: 'var(--gray-10)', fontSize: 12 }}>
                  {authCount}
                </span>
              )}
              {!isLoading && activeFilter === 'all' && tab.value === 'not-authenticated' && notAuthCount > 0 && (
                <span style={{ marginLeft: 6, color: 'var(--gray-10)', fontSize: 12 }}>
                  {notAuthCount}
                </span>
              )}
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
      </Flex>

      {/* ── Content ── */}
      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1, paddingTop: 80 }}>
          <LottieLoader variant="loader" size={48} showLabel label="Loading your MCPs..." />
        </Flex>
      ) : filteredServers.length === 0 ? (
        <EmptyState filter={activeFilter} searchQuery={searchQuery} />
      ) : (
        <Flex direction="column" gap="4">
          <Grid
            columns={{ initial: '2', md: '3', lg: '3' }}
            gap="4"
            style={{ width: '100%' }}
          >
            {filteredServers.map((server) => (
              <YourMcpCard
                key={server.instanceId}
                server={server}
                onRefresh={handleRefresh}
                onOAuthSignIn={handleOAuthSignIn}
                onNotify={handleNotify}
              />
            ))}
          </Grid>

          {hasMore && (
            <Flex justify="center" style={{ paddingTop: 8 }}>
              <Button
                size="2"
                variant="outline"
                color="gray"
                onClick={handleLoadMore}
                disabled={isLoadingMore}
              >
                {isLoadingMore ? <LottieLoader variant="still" size={16} /> : null}
                {isLoadingMore ? 'Loading...' : 'Load more'}
              </Button>
            </Flex>
          )}
        </Flex>
      )}
    </Flex>
  );
}

// ============================================================================
// Empty state
// ============================================================================

function EmptyState({
  filter,
  searchQuery,
}: {
  filter: AuthFilter;
  searchQuery: string;
}) {
  let icon = 'dns';
  let title = 'No MCP servers available';
  let description = 'Your administrator has not configured any MCP servers yet.';

  if (searchQuery.trim()) {
    icon = 'search_off';
    title = 'No results found';
    description = `No MCP servers match "${searchQuery}".`;
  } else if (filter === 'authenticated') {
    icon = 'lock_open';
    title = 'No authenticated MCP servers';
    description = 'You have not authenticated any MCP servers yet. Switch to "All" to see available servers.';
  } else if (filter === 'not-authenticated') {
    icon = 'check_circle';
    title = 'All MCPs authenticated';
    description = 'You have authenticated all available MCP servers.';
  }

  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="2"
      style={{ width: '100%', paddingTop: 80 }}
    >
      <MaterialIcon name={icon} size={48} color="var(--gray-9)" />
      <Text size="3" weight="medium" style={{ color: 'var(--gray-11)' }}>
        {title}
      </Text>
      <Text size="2" style={{ color: 'var(--gray-9)', textAlign: 'center', maxWidth: 400 }}>
        {description}
      </Text>
    </Flex>
  );
}

// ============================================================================
// Export
// ============================================================================

export default function YourMcpsPage() {
  return (
    <Suspense>
      <YourMcpsPageContent />
    </Suspense>
  );
}
