'use client';

import { useEffect, useCallback, useRef, Suspense, useMemo } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
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
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { McpServersApi } from './api';
import { useMcpServersStore } from './store';
import {
  McpServerCard,
  McpCatalogCard,
  McpServerConfigDialog,
  CustomMcpServerDialog,
} from './components';
import type { MCPServerInstance, MCPServerTemplate, TabValue } from './types';

// ============================================================================
// Constants
// ============================================================================

const ITEMS_PER_PAGE = 20;

const TABS = [
  { value: 'my-servers', label: 'My Servers' },
  { value: 'available', label: 'Available' },
] as const;

// ============================================================================
// Page content
// ============================================================================

function McpServersPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);

  // Tracks whether the initial load has already run so the debounced search
  // effect does not fire a second fetch on mount.
  const isInitialMount = useRef(true);

  const {
    catalogItems,
    myServers,
    myServersPagination,
    activeTab,
    searchQuery,
    isLoadingCatalog,
    isLoadingMyServers,
    isConfigDialogOpen,
    selectedInstance,
    selectedTemplate,
    isCustomDialogOpen,
    setCatalogItems,
    setCatalogPagination,
    setMyServers,
    appendMyServers,
    setMyServersPagination,
    setActiveTab,
    setSearchQuery,
    setIsLoadingCatalog,
    setIsLoadingMyServers,
    setError,
    openConfigDialog,
    closeConfigDialog,
    openCustomDialog,
    closeCustomDialog,
    reset,
  } = useMcpServersStore();

  // ── Sync tab from URL ─────────────────────────────────────────────────────
  useEffect(() => {
    const tab = searchParams.get('tab') as TabValue | null;
    if (tab === 'my-servers' || tab === 'available') {
      setActiveTab(tab);
    } else {
      setActiveTab('my-servers');
    }
  }, [searchParams, setActiveTab]);

  // ── Fetch My Servers ──────────────────────────────────────────────────────
  // `silent = true` skips the full-page loader (used for background refreshes).
  const fetchMyServers = useCallback(
    async (page = 1, search = '', silent = false) => {
      if (page === 1 && !silent) setIsLoadingMyServers(true);
      setError(null);
      try {
        const result = await McpServersApi.getMyMcpServers({
          page,
          limit: ITEMS_PER_PAGE,
          search: search || undefined,
        });
        if (page === 1) {
          setMyServers(result.mcpServers);
        } else {
          appendMyServers(result.mcpServers);
        }
        setMyServersPagination(result.pagination);
      } catch {
        setError('Failed to load MCP servers');
        addToast({ variant: 'error', title: 'Failed to load MCP servers' });
      } finally {
        setIsLoadingMyServers(false);
      }
    },
    [
      setIsLoadingMyServers,
      setError,
      setMyServers,
      appendMyServers,
      setMyServersPagination,
      addToast,
    ]
  );

  // ── Fetch Catalog ─────────────────────────────────────────────────────────
  const fetchCatalog = useCallback(
    async (search = '') => {
      setIsLoadingCatalog(true);
      setError(null);
      try {
        const result = await McpServersApi.getCatalog({
          page: 1,
          limit: 50,
          search: search || undefined,
        });
        setCatalogItems(result.items);
        setCatalogPagination({
          page: result.page,
          limit: result.limit,
          total: result.total,
          totalPages: result.totalPages,
          hasNext: result.page < result.totalPages,
          hasPrev: result.page > 1,
        });
      } catch {
        setError('Failed to load catalog');
        addToast({ variant: 'error', title: 'Failed to load MCP catalog' });
      } finally {
        setIsLoadingCatalog(false);
      }
    },
    [setIsLoadingCatalog, setError, setCatalogItems, setCatalogPagination, addToast]
  );

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    fetchMyServers(1);
    fetchCatalog();
    return () => {
      // Reset the guard so StrictMode's remount (or a future real unmount +
      // remount) skips the debounced-search effect on its first run too.
      isInitialMount.current = true;
      reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Debounced search ──────────────────────────────────────────────────────
  // Fires only when the user actually changes the search query.
  // - The first run is skipped via `isInitialMount` (data already loaded above).
  // - `activeTab` is intentionally NOT a dependency: switching tabs triggers an
  //   immediate fetch directly inside `handleTabChange` instead, avoiding an
  //   extra round-trip through the debounce timer.
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    const timer = setTimeout(() => {
      if (activeTab === 'my-servers') {
        fetchMyServers(1, searchQuery);
      } else {
        fetchCatalog(searchQuery);
      }
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery]); // activeTab excluded — tab switches handled by handleTabChange

  // ── Tab change ────────────────────────────────────────────────────────────
  const handleTabChange = useCallback(
    (val: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set('tab', val);
      router.replace(`/workspace/mcp-servers/?${params.toString()}`);
      const hadSearch = searchQuery.trim() !== '';
      setSearchQuery('');
      // Only refetch when a search was active — clearing it means the
      // stored data is stale filtered results. If no search was active,
      // both tabs' data is already present from the initial load.
      if (hadSearch) {
        if (val === 'my-servers') {
          fetchMyServers(1, '');
        } else {
          fetchCatalog('');
        }
      }
    },
    [router, searchParams, searchQuery, setSearchQuery, fetchMyServers, fetchCatalog]
  );

  // ── Load more (my servers) ────────────────────────────────────────────────
  const handleLoadMore = useCallback(() => {
    if (!myServersPagination?.hasNext) return;
    fetchMyServers(myServersPagination.page + 1, searchQuery);
  }, [myServersPagination, fetchMyServers, searchQuery]);

  // ── Configured types set (for catalog) ───────────────────────────────────
  const configuredTypeIds = useMemo(
    () => new Set(myServers.map((s) => s.serverType)),
    [myServers]
  );

  // ── Dialog handlers ───────────────────────────────────────────────────────
  const handleManage = useCallback(
    (server: MCPServerInstance) => {
      openConfigDialog({ instance: server });
    },
    [openConfigDialog]
  );

  const handleAddFromCatalog = useCallback(
    (template: MCPServerTemplate) => {
      openConfigDialog({ template });
    },
    [openConfigDialog]
  );

  const handleConfigDialogClose = () => {
    closeConfigDialog();
  };

  // Silent background refresh — used after dialog actions so no full-page
  // loader flashes while the dialog is open.
  const handleRefresh = useCallback(() => {
    fetchMyServers(1, searchQuery, true);
  }, [fetchMyServers, searchQuery]);

  const handleCustomCreated = (_instance: MCPServerInstance) => {
    closeCustomDialog();
    fetchMyServers(1, searchQuery, true);
  };

  const isLoading =
    activeTab === 'my-servers' ? isLoadingMyServers : isLoadingCatalog;

  return (
    <>
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
              MCP Servers
            </Heading>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              Manage Model Context Protocol servers and their authentication
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

        {/* ── Tabs + Add Custom ── */}
        <Flex align="center" justify="between" style={{ width: '100%' }}>
          <SegmentedControl.Root
            value={activeTab}
            onValueChange={handleTabChange}
            size="2"
          >
            {TABS.map((tab) => (
              <SegmentedControl.Item key={tab.value} value={tab.value}>
                {tab.label}
              </SegmentedControl.Item>
            ))}
          </SegmentedControl.Root>

          {isAdmin && (
            <Button
              size="2"
              variant="outline"
              color="gray"
              onClick={openCustomDialog}
            >
              <MaterialIcon name="add" size={16} color="var(--gray-11)" />
              Add Custom Server
            </Button>
          )}
        </Flex>

        {/* ── Content ── */}
        {isLoading ? (
          <Flex
            align="center"
            justify="center"
            style={{ width: '100%', flex: 1, paddingTop: 80 }}
          >
            <LottieLoader
              variant="loader"
              size={48}
              showLabel
              label="Loading..."
            />
          </Flex>
        ) : activeTab === 'my-servers' ? (
          <MyServersGrid
            servers={myServers}
            onManage={handleManage}
            hasMore={myServersPagination?.hasNext ?? false}
            onLoadMore={handleLoadMore}
          />
        ) : (
          <CatalogGrid
            templates={catalogItems}
            configuredTypeIds={configuredTypeIds}
            onAdd={handleAddFromCatalog}
          />
        )}
      </Flex>

      {/* ── Dialogs ── */}
      <McpServerConfigDialog
        open={isConfigDialogOpen}
        instance={selectedInstance ?? undefined}
        template={selectedTemplate ?? undefined}
        onClose={handleConfigDialogClose}
        onRefresh={handleRefresh}
      />

      <CustomMcpServerDialog
        open={isCustomDialogOpen}
        onClose={closeCustomDialog}
        onCreated={handleCustomCreated}
      />
    </>
  );
}

// ============================================================================
// My Servers Grid
// ============================================================================

function MyServersGrid({
  servers,
  onManage,
  hasMore,
  onLoadMore,
}: {
  servers: MCPServerInstance[];
  onManage: (s: MCPServerInstance) => void;
  hasMore: boolean;
  onLoadMore: () => void;
}) {

  if (servers.length === 0) {
    return (
      <Flex
        direction="column"
        align="center"
        justify="center"
        gap="2"
        style={{ width: '100%', paddingTop: 80 }}
      >
        <MaterialIcon name="dns" size={48} color="var(--gray-9)" />
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          No MCP servers added yet
        </Text>
        <Text size="1" style={{ color: 'var(--gray-9)' }}>
          Browse the Available tab to add MCP servers to your workspace.
        </Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap="4">
      <Grid
        columns={{ initial: '2', md: '3', lg: '3' }}
        gap="4"
        style={{ width: '100%' }}
      >
        {servers.map((server) => (
          <McpServerCard
            key={server.instanceId}
            server={server}
            onManage={onManage}
          />
        ))}
      </Grid>

      {hasMore && (
        <Flex justify="center" style={{ paddingTop: 8 }}>
          <Button
            size="2"
            variant="outline"
            color="gray"
            onClick={onLoadMore}
          >
            Load more
          </Button>
        </Flex>
      )}
    </Flex>
  );
}

// ============================================================================
// Catalog Grid
// ============================================================================

function CatalogGrid({
  templates,
  configuredTypeIds,
  onAdd,
}: {
  templates: MCPServerTemplate[];
  configuredTypeIds: Set<string>;
  onAdd: (t: MCPServerTemplate) => void;
}) {
  if (templates.length === 0) {
    return (
      <Flex
        direction="column"
        align="center"
        justify="center"
        gap="2"
        style={{ width: '100%', paddingTop: 80 }}
      >
        <MaterialIcon name="apps" size={48} color="var(--gray-9)" />
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          No MCP servers available
        </Text>
      </Flex>
    );
  }

  return (
    <Grid
      columns={{ initial: '2', md: '3', lg: '3' }}
      gap="4"
      style={{ width: '100%' }}
    >
      {templates.map((template) => (
        <McpCatalogCard
          key={template.typeId}
          template={template}
          isConfigured={configuredTypeIds.has(template.typeId)}
          onAdd={onAdd}
        />
      ))}
    </Grid>
  );
}

// ============================================================================
// Export
// ============================================================================

export default function McpServersPage() {
  return (
    <Suspense>
      <McpServersPageContent />
    </Suspense>
  );
}
