/**
 * Toolsets Management Page
 *
 * Features:
 * - Zero flickering during any operation
 * - Persistent UI elements (search, filters never disappear)
 * - Infinite scroll with perfect pagination
 * - Debounced search without UI disruption
 * - My Toolsets tab (configured/authenticated instances)
 * - Available tab (registry toolsets ready to configure)
 * - Follows connectors page UI/UX patterns
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
  CircularProgress,
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

import ToolsetRegistryCard from './components/toolset-registry-card';
import ToolsetCard from './components/toolset-card';

// ============================================================================
// Constants
// ============================================================================

const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 8;

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

  // ============================================================================
  // STATE
  // ============================================================================

  const [activeTab, setActiveTab] = useState<TabValue>('my-toolsets');

  // My Toolsets (configured instances)
  const [configuredToolsets, setConfiguredToolsets] = useState<MyToolset[]>([]);
  const [configuredPage, setConfiguredPage] = useState(INITIAL_PAGE);
  const [hasMoreConfigured, setHasMoreConfigured] = useState(true);
  const [configuredTotal, setConfiguredTotal] = useState(0);

  // Available tab (registry)
  const [registryToolsets, setRegistryToolsets] = useState<RegistryToolset[]>([]);
  const [registryPage, setRegistryPage] = useState(INITIAL_PAGE);
  const [hasMoreRegistry, setHasMoreRegistry] = useState(true);
  const [registryTotal, setRegistryTotal] = useState(0);

  // Loading
  const [isFirstLoad, setIsFirstLoad] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSwitchingTab, setIsSwitchingTab] = useState(false);

  // Filters
  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedFilter, setSelectedFilter] = useState<FilterType>('all');

  // UI
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  // ============================================================================
  // REFS
  // ============================================================================

  const configuredSentinelRef = useRef<HTMLDivElement | null>(null);
  const registrySentinelRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isRequestInProgressRef = useRef(false);
  const requestIdRef = useRef(0);

  // ============================================================================
  // COMPUTED VALUES
  // ============================================================================

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

  const filterCounts = useMemo<FilterCounts>(() => {
    const counts: FilterCounts = { all: configuredTotal, authenticated: 0, 'not-authenticated': 0 };
    configuredToolsets.forEach((t) => {
      if (t.isAuthenticated) counts.authenticated += 1;
      else counts['not-authenticated'] += 1;
    });
    return counts;
  }, [configuredToolsets, configuredTotal]);

  const filterOptions = useMemo(
    () => [
      { key: 'all' as FilterType, label: 'All', icon: listIcon },
      { key: 'authenticated' as FilterType, label: 'Authenticated', icon: checkCircleIcon },
      { key: 'not-authenticated' as FilterType, label: 'Not Authenticated', icon: alertCircleIcon },
    ],
    []
  );

  // Client-side filter (applied on top of already-loaded data)
  const filteredConfiguredToolsets = useMemo(() => {
    if (selectedFilter === 'all') return configuredToolsets;
    return configuredToolsets.filter((t) =>
      selectedFilter === 'authenticated' ? t.isAuthenticated : !t.isAuthenticated
    );
  }, [configuredToolsets, selectedFilter]);

  // ============================================================================
  // DEBOUNCED SEARCH
  // ============================================================================

  useEffect(() => {
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => {
      const trimmed = searchInput.trim();
      if (trimmed !== activeSearchQuery) {
        setActiveSearchQuery(trimmed);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, [searchInput, activeSearchQuery]);

  // ============================================================================
  // RESET ON FILTER/SEARCH CHANGE
  // ============================================================================

  useEffect(() => {
    // When search changes, reset the active tab's data
    if (activeTab === 'my-toolsets') {
      setConfiguredToolsets([]);
      setConfiguredPage(INITIAL_PAGE);
      setHasMoreConfigured(true);
    } else {
      setRegistryToolsets([]);
      setRegistryPage(INITIAL_PAGE);
      setHasMoreRegistry(true);
    }
  }, [activeSearchQuery, activeTab]);

  // ============================================================================
  // DATA FETCHING
  // ============================================================================

  const fetchConfigured = useCallback(
    async (page: number, isLoadMore = false) => {
      if (isRequestInProgressRef.current) return;

      // eslint-disable-next-line no-plusplus
      const currentRequestId = ++requestIdRef.current;
      isRequestInProgressRef.current = true;

      try {
        if (isLoadMore) setIsLoadingMore(true);
        else if (!isLoadMore && page === INITIAL_PAGE) setIsFirstLoad(true);

        const result = await ToolsetApiService.getMyToolsets();

        if (currentRequestId !== requestIdRef.current) return;

        const toolsets = result.toolsets || [];

        // Apply client-side search filter
        const filtered = activeSearchQuery
          ? toolsets.filter(
              (t) =>
                t.instanceName?.toLowerCase().includes(activeSearchQuery.toLowerCase()) ||
                t.displayName?.toLowerCase().includes(activeSearchQuery.toLowerCase()) ||
                t.toolsetType?.toLowerCase().includes(activeSearchQuery.toLowerCase())
            )
          : toolsets;

        setConfiguredTotal(filtered.length);

        // Since the API returns all toolsets at once, paginate client-side
        const pageSlice = filtered.slice(0, page * ITEMS_PER_PAGE);
        setConfiguredToolsets(pageSlice);
        setHasMoreConfigured(pageSlice.length < filtered.length);
      } catch (error) {
        console.error('Failed to load configured toolsets:', error);
        setSnackbar({ open: true, message: 'Failed to load toolsets. Please try again.', severity: 'error' });
      } finally {
        setIsFirstLoad(false);
        setIsLoadingMore(false);
        isRequestInProgressRef.current = false;
      }
    },
    [activeSearchQuery]
  );

  const fetchRegistry = useCallback(
    async (page: number, isLoadMore = false) => {
      if (isRequestInProgressRef.current) return;

      // eslint-disable-next-line no-plusplus
      const currentRequestId = ++requestIdRef.current;
      isRequestInProgressRef.current = true;

      try {
        if (isLoadMore) setIsLoadingMore(true);
        else if (!isLoadMore && page === INITIAL_PAGE) setIsFirstLoad(true);

        const result = await ToolsetApiService.getRegistryToolsets({
          includeTools: false,
          includeToolCount: true,
          search: activeSearchQuery || undefined,
          page,
          limit: ITEMS_PER_PAGE,
        });

        if (currentRequestId !== requestIdRef.current) return;

        const newToolsets = result.toolsets || [];
        const pagination = (result as any).pagination || {};

        setRegistryToolsets((prev) => {
          if (page === INITIAL_PAGE) return newToolsets;
          const existingNames = new Set(prev.map((t) => t.name));
          return [...prev, ...newToolsets.filter((t) => !existingNames.has(t.name))];
        });

        setRegistryTotal(pagination.totalItems ?? newToolsets.length);

        const hasMore =
          pagination.hasNext === true ||
          (typeof pagination.totalPages === 'number'
            ? page < pagination.totalPages
            : newToolsets.length === ITEMS_PER_PAGE);
        setHasMoreRegistry(hasMore);
      } catch (error) {
        console.error('Failed to load registry toolsets:', error);
        setSnackbar({ open: true, message: 'Failed to load toolsets. Please try again.', severity: 'error' });
      } finally {
        setIsFirstLoad(false);
        setIsLoadingMore(false);
        isRequestInProgressRef.current = false;
      }
    },
    [activeSearchQuery]
  );

  // Preload registry count for admins (background load, no UI impact)
  const preloadRegistryCount = useCallback(async () => {
    try {
      const result = await ToolsetApiService.getRegistryToolsets({
        includeTools: false,
        includeToolCount: true,
        page: 1,
        limit: 1, // Just fetch 1 item to get total count
      });
      
      const pagination = (result as any).pagination || {};
      setRegistryTotal(pagination.totalItems ?? result.toolsets?.length ?? 0);
    } catch (error) {
      console.error('Failed to preload registry count:', error);
      // Silently fail - this is just for UX improvement
    }
  }, []);

  // Initial load
  useEffect(() => {
    setIsFirstLoad(true);
    if (activeTab === 'my-toolsets') {
      fetchConfigured(INITIAL_PAGE, false);
      // Preload registry count for admins to show in Available tab badge
      if (isAdmin) {
        preloadRegistryCount();
      }
    } else {
      fetchRegistry(INITIAL_PAGE, false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch when page changes (load more)
  useEffect(() => {
    if (configuredPage > INITIAL_PAGE && activeTab === 'my-toolsets') {
      fetchConfigured(configuredPage, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configuredPage]);

  useEffect(() => {
    if (registryPage > INITIAL_PAGE && activeTab === 'available') {
      fetchRegistry(registryPage, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registryPage]);

  // Reload first page when search changes (already resets state via the other effect)
  useEffect(() => {
    if (activeTab === 'my-toolsets') {
      fetchConfigured(INITIAL_PAGE, false);
    } else {
      fetchRegistry(INITIAL_PAGE, false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSearchQuery]);

  // ============================================================================
  // INFINITE SCROLL
  // ============================================================================

  useEffect(() => {
    if (observerRef.current) observerRef.current.disconnect();

    const sentinel =
      activeTab === 'my-toolsets' ? configuredSentinelRef.current : registrySentinelRef.current;
    const hasMore = activeTab === 'my-toolsets' ? hasMoreConfigured : hasMoreRegistry;

    if (!sentinel || !hasMore || isFirstLoad) return undefined;

    observerRef.current = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting && !isRequestInProgressRef.current && hasMore && !isFirstLoad) {
          if (activeTab === 'my-toolsets') {
            setConfiguredPage((prev) => prev + 1);
          } else {
            setRegistryPage((prev) => prev + 1);
          }
        }
      },
      { root: null, rootMargin: '200px', threshold: 0 }
    );

    observerRef.current.observe(sentinel);

    return () => {
      if (observerRef.current) observerRef.current.disconnect();
    };
  }, [activeTab, hasMoreConfigured, hasMoreRegistry, isFirstLoad]);

  // ============================================================================
  // EVENT HANDLERS
  // ============================================================================

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newTab: TabValue) => {
      if (newTab === activeTab) return;
      setIsSwitchingTab(true);
      setActiveTab(newTab);
      setSearchInput('');
      setActiveSearchQuery('');
      setSelectedFilter('all');

      // Reset page state for the new tab
      if (newTab === 'my-toolsets') {
        setConfiguredToolsets([]);
        setConfiguredPage(INITIAL_PAGE);
        setHasMoreConfigured(true);
        setTimeout(() => {
          fetchConfigured(INITIAL_PAGE, false);
          setIsSwitchingTab(false);
        }, 50);
      } else {
        setRegistryToolsets([]);
        setRegistryPage(INITIAL_PAGE);
        setHasMoreRegistry(true);
        setTimeout(() => {
          fetchRegistry(INITIAL_PAGE, false);
          setIsSwitchingTab(false);
        }, 50);
      }
    },
    [activeTab, fetchConfigured, fetchRegistry]
  );

  const refreshAllData = useCallback(async () => {
    if (isRequestInProgressRef.current) return;

    if (activeTab === 'my-toolsets') {
      setConfiguredToolsets([]);
      setConfiguredPage(INITIAL_PAGE);
      setHasMoreConfigured(true);
      await fetchConfigured(INITIAL_PAGE, false);
    } else {
      setRegistryToolsets([]);
      setRegistryPage(INITIAL_PAGE);
      setHasMoreRegistry(true);
      await fetchRegistry(INITIAL_PAGE, false);
    }
  }, [activeTab, fetchConfigured, fetchRegistry]);

  const handleFilterChange = useCallback((filter: FilterType) => {
    setSelectedFilter(filter);
  }, []);

  const handleClearSearch = useCallback(() => {
    setSearchInput('');
    setActiveSearchQuery('');
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

  // ============================================================================
  // RENDER HELPERS
  // ============================================================================

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

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);

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
                <Iconify icon={toolIcon} width={20} height={20} sx={{ color: theme.palette.primary.main }} />
              </Box>
              <Box>
                <Typography variant="h5" sx={{ fontWeight: 700, fontSize: '1.5rem', color: theme.palette.text.primary }}>
                  Toolsets Management
                </Typography>
                <Typography variant="body2" sx={{ color: theme.palette.text.secondary, fontSize: '0.875rem' }}>
                  Configure and manage your toolset integrations
                </Typography>
              </Box>
            </Stack>

            <Tooltip title="Refresh">
              <IconButton onClick={refreshAllData} disabled={isFirstLoad || isLoadingMore}>
                <Iconify
                  icon={refreshIcon}
                  width={20}
                  height={20}
                  sx={{
                    animation: isFirstLoad || isLoadingMore ? 'spin 1s linear infinite' : 'none',
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
            </Tabs>
          </Box>
        </Box>

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
                    <IconButton size="small" onClick={handleClearSearch} sx={{ color: theme.palette.text.secondary }}>
                      <Iconify icon={clearIcon} width={16} height={16} />
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

            {/* Filter Buttons — Only on My Toolsets */}
            {activeTab === 'my-toolsets' && (
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="body2" sx={{ color: theme.palette.text.secondary, fontWeight: 500, mr: 1 }}>
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
                          ? { backgroundColor: theme.palette.primary.main, color: theme.palette.primary.contrastText }
                          : { borderColor: theme.palette.divider, color: theme.palette.text.primary }),
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
              {activeTab === 'my-toolsets'
                ? "Authenticate against your organization's toolset instances. Authenticated toolsets can be added to your agents."
                : 'Browse available toolset types. Administrators can create toolset instances from here.'}
            </Typography>
          </Alert>

          {/* Tab Content */}
          {isFirstLoad || isSwitchingTab ? (
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
          ) : activeTab === 'my-toolsets' ? (
            /* My Toolsets Tab */
            filteredConfiguredToolsets.length === 0 ? (
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
                    {activeSearchQuery ? 'No toolsets found' : 'No configured toolsets'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {activeSearchQuery
                      ? `No toolsets match "${activeSearchQuery}"`
                      : 'No toolset instances are available. Ask your administrator to create toolset instances.'}
                  </Typography>
                </Paper>
              </Fade>
            ) : (
              <>
                <Grid container spacing={2.5}>
                  {filteredConfiguredToolsets.map((toolset) => (
                    <Grid item xs={12} sm={6} md={4} lg={3} key={toolset.instanceId}>
                      {renderConfiguredToolsetCard(toolset)}
                    </Grid>
                  ))}
                </Grid>

                {/* Infinite scroll sentinel */}
                <Box ref={configuredSentinelRef} sx={{ height: 1 }} />

                {/* Loading more indicator */}
                {isLoadingMore && activeTab === 'my-toolsets' && (
                  <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
                    <CircularProgress size={28} />
                  </Box>
                )}

                {/* End of list */}
                {!hasMoreConfigured && filteredConfiguredToolsets.length > 0 && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    textAlign="center"
                    sx={{ py: 2, opacity: 0.6 }}
                  >
                    All {filteredConfiguredToolsets.length} toolset
                    {filteredConfiguredToolsets.length !== 1 ? 's' : ''} loaded
                  </Typography>
                )}
              </>
            )
          ) : (
            /* Available Tab */
            registryToolsets.length === 0 ? (
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
                <Grid container spacing={2.5}>
                  {registryToolsets.map((toolset) => (
                    <Grid item xs={12} sm={6} md={4} lg={3} key={toolset.name}>
                      {renderRegistryToolsetCard(toolset)}
                    </Grid>
                  ))}
                </Grid>

                {/* Infinite scroll sentinel */}
                <Box ref={registrySentinelRef} sx={{ height: 1 }} />

                {/* Loading more indicator */}
                {isLoadingMore && activeTab === 'available' && (
                  <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
                    <CircularProgress size={28} />
                  </Box>
                )}

                {/* End of list */}
                {!hasMoreRegistry && registryToolsets.length > 0 && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    textAlign="center"
                    sx={{ py: 2, opacity: 0.6 }}
                  >
                    All {registryToolsets.length} toolset{registryToolsets.length !== 1 ? 's' : ''} loaded
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
