/**
 * Toolsets Management Page
 *
 * Architecture highlights:
 * - Server-side search AND auth-status filtering (never filtered on the frontend)
 * - Infinite scroll via IntersectionObserver **callback refs** — the observer is
 *   attached the instant the sentinel node enters the DOM, regardless of when
 *   that happens relative to React's effect scheduling.
 * - `loadMoreConfigured` / `loadMoreRegistry` are stable (zero deps) useCallbacks
 *   that read all runtime state from refs, so the IntersectionObserver closure is
 *   never stale.
 * - Debounced search directly calls the page-1 fetcher — no intermediate useEffect
 *   watching `activeSearchQuery`, which would cause double fetches on mount.
 * - LinearProgress during search/filter changes; full skeleton only on initial load
 *   or tab switch.
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Box,
  Container,
  Typography,
  Grid,
  Button,
  Chip,
  Alert,
  Snackbar,
  Stack,
  alpha,
  useTheme,
  Paper,
  IconButton,
  Tooltip,
  InputAdornment,
  TextField,
  Fade,
  Tabs,
  Tab,
  Skeleton,
  LinearProgress,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import { RegistryToolset } from 'src/types/agent';
import ToolsetApiService, { MyToolset } from 'src/services/toolset-api';
import { useAdmin } from 'src/context/AdminContext';

// Icons
import toolIcon from '@iconify-icons/mdi/tools';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle';
import refreshIcon from '@iconify-icons/mdi/refresh';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import linkIcon from '@iconify-icons/mdi/link-variant';
import appsIcon from '@iconify-icons/mdi/apps';
import listIcon from '@iconify-icons/mdi/format-list-bulleted';

import { useSearchParams } from 'react-router-dom';
import ToolsetRegistryCard from './components/toolset-registry-card';
import ToolsetCard from './components/toolset-card';

// ============================================================================
// Constants
// ============================================================================

const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 8;
const LOAD_MORE_SKELETON_COUNT = 4;

// ============================================================================
// Types
// ============================================================================

interface SnackbarState {
  open: boolean;
  message: string;
  severity: 'success' | 'error' | 'info' | 'warning';
}

type TabValue = 'my-toolsets' | 'available';
type FilterType = 'all' | 'authenticated' | 'not-authenticated';

/** Frontend shape for filter chip counts (mapped from BackendFilterCounts). */
interface FilterCounts {
  all: number;
  authenticated: number;
  'not-authenticated': number;
}

// ============================================================================
// Component
// ============================================================================

const ToolsetsPage: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { isAdmin } = useAdmin();
  const [searchParams, setSearchParams] = useSearchParams();

  // ──────────────────────────────────────────────────────────────────────────
  // STATE
  // ──────────────────────────────────────────────────────────────────────────

  const [activeTab, setActiveTab] = useState<TabValue>('my-toolsets');

  // Accumulated lists (appended on load-more)
  const [configuredToolsets, setConfiguredToolsets] = useState<MyToolset[]>([]);
  const [registryToolsets, setRegistryToolsets] = useState<RegistryToolset[]>([]);

  // Totals (from backend pagination)
  const [configuredTotal, setConfiguredTotal] = useState(0);
  const [registryTotal, setRegistryTotal] = useState(0);

  // Pagination flags (drive "end of list" text and enable load-more)
  const [hasMoreConfigured, setHasMoreConfigured] = useState(false);
  const [hasMoreRegistry, setHasMoreRegistry] = useState(false);

  // Filter counts from backend (computed before auth_status filter, so chips
  // always show correct numbers regardless of active filter)
  const [filterCounts, setFilterCounts] = useState<FilterCounts>({
    all: 0,
    authenticated: 0,
    'not-authenticated': 0,
  });

  // Loading states
  const [isInitialLoad, setIsInitialLoad] = useState(true); // full-skeleton load
  const [isRefetching, setIsRefetching] = useState(false);  // LinearProgress load
  const [isLoadingMore, setIsLoadingMore] = useState(false); // load-more skeleton

  // Search & filter (state drives UI; refs drive stable callbacks)
  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedFilter, setSelectedFilter] = useState<FilterType>('all');

  // UI
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  // ──────────────────────────────────────────────────────────────────────────
  // REFS — stable values for zero-dep callbacks (IntersectionObserver closures)
  // ──────────────────────────────────────────────────────────────────────────

  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const requestIdRef = useRef(0);

  // Pagination (mirrors state — used by loadMore which cannot depend on state)
  const configuredPageRef = useRef(INITIAL_PAGE);
  const registryPageRef = useRef(INITIAL_PAGE);

  // Loading mirrors
  const isLoadingMoreRef = useRef(false);
  const isRefetchingRef = useRef(false);
  const hasMoreConfiguredRef = useRef(false);
  const hasMoreRegistryRef = useRef(false);

  // Search / filter / tab mirrors
  const activeSearchRef = useRef('');
  const selectedFilterRef = useRef<FilterType>('all');
  const activeTabRef = useRef<TabValue>('my-toolsets');

  // Guard: loadMore must not fire before the first successful fetch
  const isReadyRef = useRef(false);

  // IntersectionObserver instances (for explicit cleanup on node detach)
  const configuredObserverRef = useRef<IntersectionObserver | null>(null);
  const registryObserverRef = useRef<IntersectionObserver | null>(null);

  // ──────────────────────────────────────────────────────────────────────────
  // COMPUTED VALUES
  // ──────────────────────────────────────────────────────────────────────────

  /** Build a map of toolsetType → instances for quick "isConfigured" lookups. */
  const configuredToolsetsMap = useMemo(() => {
    const map = new Map<string, MyToolset[]>();
    configuredToolsets.forEach((t) => {
      const key = t.toolsetType?.toLowerCase() || '';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(t);
    });
    return map;
  }, [configuredToolsets]);

  const isToolsetConfigured = useCallback(
    (toolsetName: string): boolean => {
      const instances = configuredToolsetsMap.get(toolsetName.toLowerCase());
      return !!(instances && instances.some((inst) => inst.isAuthenticated));
    },
    [configuredToolsetsMap]
  );

  const filterOptions = useMemo(
    () => [
      { key: 'all' as FilterType, label: 'All', icon: listIcon },
      { key: 'authenticated' as FilterType, label: 'Authenticated', icon: checkCircleIcon },
      { key: 'not-authenticated' as FilterType, label: 'Not Authenticated', icon: alertCircleIcon },
    ],
    []
  );

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);
  const loadMoreSkeletons = useMemo(
    () => Array.from({ length: LOAD_MORE_SKELETON_COUNT }, (_, i) => i),
    []
  );

  // ──────────────────────────────────────────────────────────────────────────
  // CORE FETCHERS — stable (zero deps), read runtime values from refs
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Fetch page 1 of My Toolsets.
   * 'initial' → full skeleton (first mount / tab switch)
   * 'refetch' → LinearProgress above existing content (search / filter change)
   */
  const fetchConfiguredPage1 = useCallback(async (mode: 'initial' | 'refetch') => {
    if (mode === 'initial') {
      isReadyRef.current = false;
      setIsInitialLoad(true);
    } else {
      isRefetchingRef.current = true;
      setIsRefetching(true);
    }

    requestIdRef.current += 1;
    const reqId = requestIdRef.current;
    configuredPageRef.current = INITIAL_PAGE;

    try {
      const authStatus =
        selectedFilterRef.current !== 'all'
          ? (selectedFilterRef.current as 'authenticated' | 'not-authenticated')
          : undefined;

      const { toolsets, pagination, filterCounts: fc } = await ToolsetApiService.getMyToolsets({
        search: activeSearchRef.current || undefined,
        authStatus,
        page: 1,
        limit: ITEMS_PER_PAGE,
      });

      if (reqId !== requestIdRef.current) return; // superseded by a newer request

      setConfiguredToolsets(toolsets);
      setConfiguredTotal(pagination.total);
      setFilterCounts({
        all: fc.all,
        authenticated: fc.authenticated,
        'not-authenticated': fc.notAuthenticated,
      });
      hasMoreConfiguredRef.current = pagination.hasNext;
      setHasMoreConfigured(pagination.hasNext);
      isReadyRef.current = true;
    } catch (error) {
      console.error('Failed to load configured toolsets:', error);
      setSnackbar({
        open: true,
        message: 'Failed to load toolsets. Please try again.',
        severity: 'error',
      });
    } finally {
      setIsInitialLoad(false);
      isRefetchingRef.current = false;
      setIsRefetching(false);
    }
  }, []); // no deps — reads refs

  /**
   * Fetch page 1 of Registry toolsets.
   */
  const fetchRegistryPage1 = useCallback(async (mode: 'initial' | 'refetch') => {
    if (mode === 'initial') {
      isReadyRef.current = false;
      setIsInitialLoad(true);
    } else {
      isRefetchingRef.current = true;
      setIsRefetching(true);
    }

    requestIdRef.current += 1;
    const reqId = requestIdRef.current;
    registryPageRef.current = INITIAL_PAGE;

    try {
      const result = await ToolsetApiService.getRegistryToolsets({
        includeTools: false,
        includeToolCount: true,
        search: activeSearchRef.current || undefined,
        page: 1,
        limit: ITEMS_PER_PAGE,
      });

      if (reqId !== requestIdRef.current) return;

      setRegistryToolsets(result.toolsets || []);
      setRegistryTotal(result.pagination.total ?? (result.toolsets || []).length);
      hasMoreRegistryRef.current = result.pagination.hasNext;
      setHasMoreRegistry(result.pagination.hasNext);
      isReadyRef.current = true;
    } catch (error) {
      console.error('Failed to load registry toolsets:', error);
      setSnackbar({
        open: true,
        message: 'Failed to load toolsets. Please try again.',
        severity: 'error',
      });
    } finally {
      setIsInitialLoad(false);
      isRefetchingRef.current = false;
      setIsRefetching(false);
    }
  }, []); // no deps — reads refs

  // ──────────────────────────────────────────────────────────────────────────
  // LOAD MORE — stable (zero deps), called by IntersectionObserver
  // ──────────────────────────────────────────────────────────────────────────

  const loadMoreConfigured = useCallback(async () => {
    if (
      !isReadyRef.current ||
      isLoadingMoreRef.current ||
      isRefetchingRef.current ||
      !hasMoreConfiguredRef.current ||
      activeTabRef.current !== 'my-toolsets'
    )
      return;

    isLoadingMoreRef.current = true;
    setIsLoadingMore(true);

    const nextPage = configuredPageRef.current + 1;
    try {
      const authStatus =
        selectedFilterRef.current !== 'all'
          ? (selectedFilterRef.current as 'authenticated' | 'not-authenticated')
          : undefined;

      const { toolsets, pagination, filterCounts: fc } = await ToolsetApiService.getMyToolsets({
        search: activeSearchRef.current || undefined,
        authStatus,
        page: nextPage,
        limit: ITEMS_PER_PAGE,
      });

      configuredPageRef.current = nextPage;
      setConfiguredToolsets((prev) => [...prev, ...toolsets]);
      setConfiguredTotal(pagination.total);
      // Update counts with latest from server (may differ if data changed)
      setFilterCounts({
        all: fc.all,
        authenticated: fc.authenticated,
        'not-authenticated': fc.notAuthenticated,
      });
      hasMoreConfiguredRef.current = pagination.hasNext;
      setHasMoreConfigured(pagination.hasNext);
    } catch (error) {
      console.error('Failed to load more configured toolsets:', error);
    } finally {
      isLoadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, []); // no deps

  const loadMoreRegistry = useCallback(async () => {
    if (
      !isReadyRef.current ||
      isLoadingMoreRef.current ||
      isRefetchingRef.current ||
      !hasMoreRegistryRef.current ||
      activeTabRef.current !== 'available'
    )
      return;

    isLoadingMoreRef.current = true;
    setIsLoadingMore(true);

    const nextPage = registryPageRef.current + 1;
    try {
      const result = await ToolsetApiService.getRegistryToolsets({
        includeTools: false,
        includeToolCount: true,
        search: activeSearchRef.current || undefined,
        page: nextPage,
        limit: ITEMS_PER_PAGE,
      });

      registryPageRef.current = nextPage;
      const newToolsets = result.toolsets || [];
      setRegistryToolsets((prev) => {
        const existingNames = new Set(prev.map((t) => t.name));
        return [...prev, ...newToolsets.filter((t) => !existingNames.has(t.name))];
      });
      setRegistryTotal(result.pagination.total ?? newToolsets.length);
      hasMoreRegistryRef.current = result.pagination.hasNext;
      setHasMoreRegistry(result.pagination.hasNext);
    } catch (error) {
      console.error('Failed to load more registry toolsets:', error);
    } finally {
      isLoadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, []); // no deps

  // ──────────────────────────────────────────────────────────────────────────
  // SENTINEL CALLBACK REFS
  //
  // Using callback refs instead of useRef + useEffect is the correct pattern:
  // the callback fires the instant the node enters the DOM (even inside
  // conditionally rendered branches), so the observer is always created.
  // Since loadMoreConfigured / loadMoreRegistry are stable, these callbacks
  // are also stable and never cause unnecessary re-renders.
  // ──────────────────────────────────────────────────────────────────────────

  const setSentinelConfigured = useCallback(
    (node: HTMLDivElement | null) => {
      if (configuredObserverRef.current) {
        configuredObserverRef.current.disconnect();
        configuredObserverRef.current = null;
      }
      if (!node) return;
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting) loadMoreConfigured();
        },
        { rootMargin: '300px', threshold: 0 }
      );
      observer.observe(node);
      configuredObserverRef.current = observer;
    },
    [loadMoreConfigured]
  );

  const setSentinelRegistry = useCallback(
    (node: HTMLDivElement | null) => {
      if (registryObserverRef.current) {
        registryObserverRef.current.disconnect();
        registryObserverRef.current = null;
      }
      if (!node) return;
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting) loadMoreRegistry();
        },
        { rootMargin: '300px', threshold: 0 }
      );
      observer.observe(node);
      registryObserverRef.current = observer;
    },
    [loadMoreRegistry]
  );

  // ──────────────────────────────────────────────────────────────────────────
  // INITIAL LOAD
  // ──────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    const tabParam = searchParams.get('tab');
    const initialTab: TabValue = tabParam === 'available' ? 'available' : 'my-toolsets';
    activeTabRef.current = initialTab;
    setActiveTab(initialTab);

    if (!tabParam) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('tab', initialTab);
          return next;
        },
        { replace: true }
      );
    }

    if (initialTab === 'my-toolsets') {
      fetchConfiguredPage1('initial');
      // Silently preload the Available tab badge count
      if (isAdmin) {
        ToolsetApiService.getRegistryToolsets({ page: 1, limit: 1, includeTools: false })
          .then((r) => setRegistryTotal(r.pagination.total))
          .catch(() => {});
      }
    } else {
      fetchRegistryPage1('initial');
      // Silently preload the My Toolsets badge count
      ToolsetApiService.getMyToolsets({ page: 1, limit: 1 })
        .then((r) => {
          setConfiguredTotal(r.pagination.total);
          setFilterCounts({
            all: r.filterCounts.all,
            authenticated: r.filterCounts.authenticated,
            'not-authenticated': r.filterCounts.notAuthenticated,
          });
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ──────────────────────────────────────────────────────────────────────────
  // DEBOUNCED SEARCH → directly calls fetcher (no secondary useEffect)
  //
  // Calling fetchConfiguredPage1 / fetchRegistryPage1 directly here avoids
  // the double-fetch problem that arises when a `useEffect([activeSearchQuery])`
  // also fires on mount with the initial empty string.
  // ──────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => {
      const trimmed = searchInput.trim();
      const prev = activeSearchRef.current;
      activeSearchRef.current = trimmed;
      setActiveSearchQuery(trimmed);

      if (trimmed !== prev) {
        isReadyRef.current = false;
        if (activeTabRef.current === 'my-toolsets') {
          fetchConfiguredPage1('refetch');
        } else {
          fetchRegistryPage1('refetch');
        }
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
    // fetchConfiguredPage1 / fetchRegistryPage1 are stable (zero deps)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  // ──────────────────────────────────────────────────────────────────────────
  // EVENT HANDLERS
  // ──────────────────────────────────────────────────────────────────────────

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newTab: TabValue) => {
      if (newTab === activeTabRef.current) return;

      // Sync URL
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('tab', newTab);
          return next;
        },
        { replace: true }
      );

      // Reset search & filter
      setSearchInput('');
      setActiveSearchQuery('');
      activeSearchRef.current = '';
      setSelectedFilter('all');
      selectedFilterRef.current = 'all';

      // Update tab ref first so loadMore guards work immediately
      activeTabRef.current = newTab;
      setActiveTab(newTab);
      isReadyRef.current = false;

      if (newTab === 'my-toolsets') {
        setConfiguredToolsets([]);
        hasMoreConfiguredRef.current = false;
        setHasMoreConfigured(false);
        fetchConfiguredPage1('initial');
      } else {
        setRegistryToolsets([]);
        hasMoreRegistryRef.current = false;
        setHasMoreRegistry(false);
        fetchRegistryPage1('initial');
      }
    },
    [fetchConfiguredPage1, fetchRegistryPage1, setSearchParams]
  );

  const refreshAllData = useCallback(
    async (showLoader = true, forceRefreshBoth = false) => {
      if (isRefetchingRef.current || isLoadingMoreRef.current) return;
      const mode = showLoader ? 'initial' : 'refetch';

      if (forceRefreshBoth || activeTabRef.current === 'my-toolsets') {
        await fetchConfiguredPage1(mode);
      } else {
        await fetchRegistryPage1(mode);
      }
    },
    [fetchConfiguredPage1, fetchRegistryPage1]
  );

  /**
   * Auth filter change — updates the ref and triggers a server-side refetch.
   * No client-side filtering: the backend is the single source of truth.
   */
  const handleFilterChange = useCallback(
    (filter: FilterType) => {
      setSelectedFilter(filter);
      selectedFilterRef.current = filter;
      isReadyRef.current = false;
      fetchConfiguredPage1('refetch');
    },
    [fetchConfiguredPage1]
  );

  const handleClearSearch = useCallback(() => {
    setSearchInput('');
    // The debounce effect fires after SEARCH_DEBOUNCE_MS and triggers a refetch
  }, []);

  const handleCloseSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  const handleShowToast = useCallback(
    (message: string, severity: 'success' | 'error' | 'info' | 'warning' = 'success') => {
      setSnackbar({ open: true, message, severity });
    },
    []
  );

  // ──────────────────────────────────────────────────────────────────────────
  // RENDER HELPERS
  // ──────────────────────────────────────────────────────────────────────────

  const renderConfiguredToolsetCard = useCallback(
    (toolset: MyToolset) => (
      <ToolsetCard
        key={toolset.instanceId}
        toolset={toolset}
        isAdmin={isAdmin}
        onRefresh={refreshAllData}
        onShowToast={handleShowToast}
      />
    ),
    [isAdmin, refreshAllData, handleShowToast]
  );

  const renderRegistryToolsetCard = useCallback(
    (toolset: RegistryToolset) => {
      const isConfigured = isToolsetConfigured(toolset.name);
      return (
        <ToolsetRegistryCard
          key={toolset.name}
          toolset={toolset}
          isConfigured={isConfigured}
          isAdmin={isAdmin}
          onRefresh={refreshAllData}
          onShowToast={handleShowToast}
        />
      );
    },
    [isToolsetConfigured, isAdmin, refreshAllData, handleShowToast]
  );

  // ============================================================================
  // RENDER
  // ============================================================================

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <Box
          sx={{
            p: 3,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDark
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.grey[50], 0.5),
          }}
        >
          <Stack direction="row" alignItems="center" justifyContent="space-between">
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 1.5,
                  backgroundColor: alpha(theme.palette.primary.main, 0.1),
                  border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Iconify
                  icon={toolIcon}
                  width={20}
                  height={20}
                  sx={{ color: theme.palette.primary.main }}
                />
              </Box>
              <Box>
                <Typography
                  variant="h5"
                  sx={{ fontWeight: 700, fontSize: '1.5rem', color: theme.palette.text.primary }}
                >
                  Toolsets Management
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ color: theme.palette.text.secondary, fontSize: '0.875rem' }}
                >
                  Configure and manage your toolset integrations
                </Typography>
              </Box>
            </Stack>

            <Tooltip title="Refresh">
              <IconButton
                onClick={(e) => {
                  e.preventDefault();
                  refreshAllData();
                }}
                disabled={isInitialLoad || isLoadingMore}
              >
                <Iconify
                  icon={refreshIcon}
                  width={20}
                  height={20}
                  sx={{
                    animation:
                      isInitialLoad || isLoadingMore ? 'spin 1s linear infinite' : 'none',
                    '@keyframes spin': {
                      '0%': { transform: 'rotate(0deg)' },
                      '100%': { transform: 'rotate(360deg)' },
                    },
                  }}
                />
              </IconButton>
            </Tooltip>
          </Stack>

          {/* Tabs */}
          <Box sx={{ borderBottom: 1, borderColor: 'divider', mt: 2 }}>
            <Tabs
              value={activeTab}
              onChange={handleTabChange}
              sx={{ '& .MuiTab-root': { textTransform: 'none', fontWeight: 600, minHeight: 48 } }}
            >
              <Tab
                icon={<Iconify icon={checkCircleIcon} width={18} height={18} />}
                iconPosition="start"
                label={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <span>My Toolsets</span>
                    {configuredTotal > 0 && (
                      <Chip
                        label={configuredTotal}
                        size="small"
                        sx={{
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 700,
                          minWidth: 20,
                          '& .MuiChip-label': { px: 0.75 },
                          backgroundColor:
                            activeTab === 'my-toolsets'
                              ? isDark
                                ? alpha(theme.palette.primary.contrastText, 0.9)
                                : alpha(theme.palette.primary.main, 0.8)
                              : isDark
                              ? alpha(theme.palette.text.primary, 0.4)
                              : alpha(theme.palette.text.primary, 0.12),
                          color:
                            activeTab === 'my-toolsets'
                              ? theme.palette.primary.contrastText
                              : theme.palette.text.primary,
                          border:
                            activeTab === 'my-toolsets'
                              ? `1px solid ${alpha(theme.palette.primary.contrastText, 0.3)}`
                              : `1px solid ${alpha(theme.palette.text.primary, 0.2)}`,
                        }}
                      />
                    )}
                  </Box>
                }
                value="my-toolsets"
                sx={{ mr: 1 }}
              />
              {isAdmin && (
                <Tab
                  icon={<Iconify icon={appsIcon} width={18} height={18} />}
                  iconPosition="start"
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <span>Available</span>
                      {registryTotal > 0 && (
                        <Chip
                          label={registryTotal}
                          size="small"
                          sx={{
                            height: 20,
                            fontSize: '0.6875rem',
                            fontWeight: 700,
                            minWidth: 20,
                            '& .MuiChip-label': { px: 0.75 },
                            backgroundColor:
                              activeTab === 'available'
                                ? isDark
                                  ? alpha(theme.palette.primary.contrastText, 0.9)
                                  : alpha(theme.palette.primary.main, 0.8)
                                : isDark
                                ? alpha(theme.palette.text.primary, 0.4)
                                : alpha(theme.palette.text.primary, 0.12),
                            color:
                              activeTab === 'available'
                                ? theme.palette.primary.contrastText
                                : theme.palette.text.primary,
                            border:
                              activeTab === 'available'
                                ? `1px solid ${alpha(theme.palette.primary.contrastText, 0.3)}`
                                : `1px solid ${alpha(theme.palette.text.primary, 0.2)}`,
                          }}
                        />
                      )}
                    </Box>
                  }
                  value="available"
                />
              )}
            </Tabs>
          </Box>
        </Box>

        {/* LinearProgress — shown during search / filter change (not initial load) */}
        <LinearProgress
          sx={{
            height: 2,
            opacity: isRefetching ? 1 : 0,
            transition: 'opacity 0.2s ease',
          }}
        />

        {/* Content */}
        <Box sx={{ p: 3 }}>
          {/* Search and Filters */}
          <Stack spacing={2} sx={{ mb: 3 }}>
            <TextField
              placeholder={
                activeTab === 'my-toolsets'
                  ? 'Search configured toolsets...'
                  : 'Search available toolsets...'
              }
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              size="small"
              fullWidth
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon={magnifyIcon}
                      width={20}
                      height={20}
                      sx={{ color: theme.palette.text.secondary }}
                    />
                  </InputAdornment>
                ),
                endAdornment: searchInput && (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      onClick={handleClearSearch}
                      edge="end"
                      sx={{
                        color: theme.palette.text.secondary,
                        '&:hover': { color: theme.palette.text.primary },
                      }}
                    >
                      <Iconify icon={clearIcon} width={18} height={18} />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  height: 48,
                  borderRadius: 1.5,
                  backgroundColor: isDark
                    ? alpha(theme.palette.background.default, 0.4)
                    : theme.palette.background.paper,
                },
              }}
            />

            {/* Auth filter — My Toolsets only; filtering is server-side */}
            {activeTab === 'my-toolsets' && (
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography
                  variant="body2"
                  sx={{ color: theme.palette.text.secondary, fontWeight: 500, mr: 1 }}
                >
                  Filter:
                </Typography>
                {filterOptions.map((option) => {
                  const isSelected = selectedFilter === option.key;
                  const count = filterCounts[option.key];
                  return (
                    <Button
                      key={option.key}
                      variant={isSelected ? 'contained' : 'outlined'}
                      size="small"
                      onClick={() => handleFilterChange(option.key)}
                      startIcon={<Iconify icon={option.icon} width={16} height={16} />}
                      sx={{
                        textTransform: 'none',
                        borderRadius: 1.5,
                        fontWeight: 600,
                        fontSize: '0.8125rem',
                        height: 32,
                        ...(isSelected
                          ? {
                              backgroundColor: theme.palette.primary.main,
                              color: theme.palette.primary.contrastText,
                            }
                          : {
                              borderColor: theme.palette.divider,
                              color: theme.palette.text.primary,
                            }),
                      }}
                    >
                      {option.label}
                      {count > 0 && (
                        <Chip
                          label={count}
                          size="small"
                          sx={{
                            ml: 1,
                            height: 18,
                            fontSize: '0.6875rem',
                            fontWeight: 700,
                            '& .MuiChip-label': { px: 0.75 },
                          }}
                        />
                      )}
                    </Button>
                  );
                })}
              </Stack>
            )}
          </Stack>

          {/* Info Alert */}
          <Alert
            severity="info"
            sx={{
              mb: 3,
              borderRadius: 1.5,
              borderColor: alpha(theme.palette.info.main, 0.2),
              backgroundColor: alpha(theme.palette.info.main, 0.04),
            }}
          >
            <Typography variant="body2">
              {activeTab === 'my-toolsets' || !isAdmin
                ? "Authenticate against your organization's toolset instances. Authenticated toolsets can be added to your agents."
                : 'Browse available toolset types. Administrators can create toolset instances from here.'}
            </Typography>
          </Alert>

          {/* Tab Content */}
          {(!isAdmin && activeTab !== 'my-toolsets') ? null : isInitialLoad ? (
            /* Loading Skeletons */
            <Grid container spacing={2.5}>
              {loadingSkeletons.map((i) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={i}>
                  <Skeleton
                    variant="rectangular"
                    height={220}
                    sx={{ borderRadius: 2, animation: 'pulse 1.5s ease-in-out infinite' }}
                  />
                </Grid>
              ))}
            </Grid>
          ) : activeTab === 'my-toolsets' || !isAdmin ? (
            /* My Toolsets Tab */
            configuredToolsets.length === 0 && !isRefetching  ? (
              <Fade in timeout={300}>
                <Paper
                  elevation={0}
                  sx={{
                    py: 6,
                    px: 4,
                    textAlign: 'center',
                    borderRadius: 2,
                    border: `1px solid ${theme.palette.divider}`,
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  }}
                >
                  <Box
                    sx={{
                      width: 80,
                      height: 80,
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mx: 'auto',
                      mb: 3,
                    }}
                  >
                    <Iconify
                      icon={activeSearchQuery ? magnifyIcon : linkIcon}
                      width={32}
                      height={32}
                      sx={{ color: theme.palette.text.disabled }}
                    />
                  </Box>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    {activeSearchQuery
                      ? 'No toolsets found'
                      : selectedFilter !== 'all'
                      ? `No ${selectedFilter} toolsets`
                      : 'No configured toolsets'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {activeSearchQuery
                      ? `No toolsets match "${activeSearchQuery}"`
                      : selectedFilter !== 'all'
                      ? `No toolsets with status "${selectedFilter}" exist yet.`
                      : 'No toolset instances are available. Ask your administrator to create toolset instances.'}
                  </Typography>
                </Paper>
              </Fade>
            ) : (
              <>
                {/* Keep existing items visible while refetching (LinearProgress above) */}
                <Box sx={{ opacity: isRefetching ? 0.55 : 1, transition: 'opacity 0.2s ease' }}>
                  <Grid container spacing={2.5}>
                    {configuredToolsets.map((toolset) => (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={toolset.instanceId}>
                        {renderConfiguredToolsetCard(toolset)}
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                {/*
                  Sentinel — callback ref fires as soon as this node enters the DOM.
                  Positioned after the grid so the observer fires when the user
                  scrolls close to the bottom (rootMargin: '300px').
                */}
                <Box ref={setSentinelConfigured} sx={{ height: 0 }} />

                {/* Load-more skeleton (visible while fetching next page) */}
                {isLoadingMore && activeTab === 'my-toolsets' && (
                  <Fade in timeout={150}>
                    <Grid container spacing={2.5} sx={{ mt: 1 }}>
                      {loadMoreSkeletons.map((i) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={`lm-cfg-${i}`}>
                          <Skeleton
                            variant="rectangular"
                            height={220}
                            sx={{ borderRadius: 2 }}
                          />
                        </Grid>
                      ))}
                    </Grid>
                  </Fade>
                )}

                {/* End of list */}
                {!hasMoreConfigured && configuredToolsets.length > 0 && !isLoadingMore && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    textAlign="center"
                    sx={{ py: 2, opacity: 0.6 }}
                  >
                    All {configuredTotal} toolset{configuredTotal !== 1 ? 's' : ''} loaded
                  </Typography>
                )}
              </>
            )
          ) : (
            /* ── Available Tab ─────────────────────────────────────────────── */
            registryToolsets.length === 0 && !isRefetching ? (
              <Fade in timeout={300}>
                <Paper
                  elevation={0}
                  sx={{
                    py: 6,
                    px: 4,
                    textAlign: 'center',
                    borderRadius: 2,
                    border: `1px solid ${theme.palette.divider}`,
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  }}
                >
                  <Box
                    sx={{
                      width: 80,
                      height: 80,
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mx: 'auto',
                      mb: 3,
                    }}
                  >
                    <Iconify
                      icon={activeSearchQuery ? magnifyIcon : appsIcon}
                      width={32}
                      height={32}
                      sx={{ color: theme.palette.text.disabled }}
                    />
                  </Box>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    {activeSearchQuery ? 'No toolsets found' : 'No toolsets available'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {activeSearchQuery
                      ? `No toolsets match "${activeSearchQuery}"`
                      : 'No toolsets have been registered in the system yet'}
                  </Typography>
                </Paper>
              </Fade>
            ) : (
              <>
                <Box sx={{ opacity: isRefetching ? 0.55 : 1, transition: 'opacity 0.2s ease' }}>
                  <Grid container spacing={2.5}>
                    {registryToolsets.map((toolset) => (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={toolset.name}>
                        {renderRegistryToolsetCard(toolset)}
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                {/* Sentinel — callback ref */}
                <Box ref={setSentinelRegistry} sx={{ height: 0 }} />

                {/* Load-more skeleton */}
                {isLoadingMore && activeTab === 'available' && (
                  <Fade in timeout={150}>
                    <Grid container spacing={2.5} sx={{ mt: 1 }}>
                      {loadMoreSkeletons.map((i) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={`lm-reg-${i}`}>
                          <Skeleton
                            variant="rectangular"
                            height={220}
                            sx={{ borderRadius: 2 }}
                          />
                        </Grid>
                      ))}
                    </Grid>
                  </Fade>
                )}

                {/* End of list */}
                {!hasMoreRegistry && registryToolsets.length > 0 && !isLoadingMore && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    textAlign="center"
                    sx={{ py: 2, opacity: 0.6 }}
                  >
                    All {registryTotal} toolset{registryTotal !== 1 ? 's' : ''} loaded
                  </Typography>
                )}
              </>
            )
          )}
        </Box>
      </Box>

      {/* Page-level Snackbar */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{ borderRadius: 1.5, fontWeight: 600 }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default ToolsetsPage;
