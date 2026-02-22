/**
 * Toolsets Management Page
 * 
 * Comprehensive toolsets management interface with:
 * - My Toolsets tab (configured/authenticated instances)
 * - Available tab (registry toolsets ready to configure)
 * - Search and filtering
 * - OAuth configuration support
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
import { RegistryToolset, Toolset } from 'src/types/agent';
import ToolsetApiService from 'src/services/toolset-api';

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

import ToolsetConfigDialog from './components/toolset-config-dialog';
import ToolsetRegistryCard from './components/toolset-registry-card';
import ToolsetCard from './components/toolset-card';

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

const SEARCH_DEBOUNCE_MS = 500;
const SKELETON_COUNT = 8;

const ToolsetsPage: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // State
  const [activeTab, setActiveTab] = useState<TabValue>('my-toolsets');
  const [registryToolsets, setRegistryToolsets] = useState<RegistryToolset[]>([]);
  const [configuredToolsets, setConfiguredToolsets] = useState<Toolset[]>([]);
  const [isFirstLoad, setIsFirstLoad] = useState(true);
  const [isLoadingData, setIsLoadingData] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedFilter, setSelectedFilter] = useState<FilterType>('all');
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  // Refs
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isFirstLoadRef = useRef(true);
  const isLoadingRef = useRef(false);
  const hasLoadedRef = useRef(false);
  const lastActiveTabRef = useRef<TabValue>(activeTab);

  // User ID is extracted from auth token on backend - no need to pass it

  // ============================================================================
  // DEBOUNCED SEARCH
  // ============================================================================

  useEffect(() => {
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    searchTimeoutRef.current = setTimeout(() => {
      const trimmed = searchInput.trim();
      if (trimmed !== activeSearchQuery) {
        setActiveSearchQuery(trimmed);
      }
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, [searchInput, activeSearchQuery]);

  // ============================================================================
  // DATA FETCHING
  // ============================================================================

  // Initial load - load both tabs in parallel
  useEffect(() => {
    // Prevent duplicate calls (React StrictMode, re-renders)
    if (isLoadingRef.current || hasLoadedRef.current) {
      return;
    }

    const loadInitialData = async () => {
      isLoadingRef.current = true;
      setIsFirstLoad(true);
      isFirstLoadRef.current = false;

      try {
        // Load both tabs in parallel for initial load
        // Note: includeTools=false for performance (we only need count on toolsets page)
        const [configured, registry] = await Promise.all([
          ToolsetApiService.getConfiguredToolsets(),
          ToolsetApiService.getRegistryToolsets({ includeTools: false, includeToolCount: true }),
        ]);

        setConfiguredToolsets(configured.toolsets);
        setRegistryToolsets(registry.toolsets);
        hasLoadedRef.current = true;
      } catch (error) {
        console.error('Failed to load toolsets:', error);
        setSnackbar({
          open: true,
          message: 'Failed to load toolsets. Please try again.',
          severity: 'error',
        });
      } finally {
        setIsFirstLoad(false);
        isLoadingRef.current = false;
      }
    };

    loadInitialData();
  }, []);

  // Handle tab changes after initial load
  const loadToolsetsForTab = useCallback(async (tab: TabValue, isRefresh = false) => {
    // Skip if initial load is still in progress
    if (!hasLoadedRef.current) return;

    try {
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setIsLoadingData(true);
      }

      // Load only the data needed for the current tab
      if (tab === 'my-toolsets') {
        const configured = await ToolsetApiService.getConfiguredToolsets();
        setConfiguredToolsets(configured.toolsets);
      } else {
        const registry = await ToolsetApiService.getRegistryToolsets({ includeTools: false, includeToolCount: true });
        setRegistryToolsets(registry.toolsets);
      }
    } catch (error) {
      console.error('Failed to load toolsets:', error);
      setSnackbar({
        open: true,
        message: 'Failed to load toolsets. Please try again.',
        severity: 'error',
      });
    } finally {
      setIsLoadingData(false);
      setRefreshing(false);
    }
  }, []);

  // Reload when tab changes (after initial load)
  useEffect(() => {
    if (hasLoadedRef.current && activeTab !== lastActiveTabRef.current) {
      lastActiveTabRef.current = activeTab;
      loadToolsetsForTab(activeTab);
    }
  }, [activeTab, loadToolsetsForTab]);

  // ============================================================================
  // COMPUTED VALUES
  // ============================================================================

  // Map configured toolsets by name for quick lookup
  const configuredToolsetsMap = useMemo(() => {
    const map = new Map<string, Toolset>();
    configuredToolsets.forEach((t) => {
      map.set(t.name, t);
    });
    return map;
  }, [configuredToolsets]);

  // Check if a toolset is configured
  const isToolsetConfigured = useCallback(
    (toolsetName: string): boolean => {
      const configured = configuredToolsetsMap.get(toolsetName);
      return !!(configured && configured.isAuthenticated);
    },
    [configuredToolsetsMap]
  );

  // Filter registry toolsets for "Available" tab based on search
  const filteredRegistryToolsets = useMemo(() => {
    let filtered = registryToolsets;

    if (activeSearchQuery) {
      const query = activeSearchQuery.toLowerCase();
      filtered = filtered.filter(
        (t) =>
          t.name.toLowerCase().includes(query) ||
          t.displayName.toLowerCase().includes(query) ||
          t.description?.toLowerCase().includes(query) ||
          t.category.toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [registryToolsets, activeSearchQuery]);

  // Filter configured toolsets for "My Toolsets" tab
  const filteredConfiguredToolsets = useMemo(() => {
    let filtered = configuredToolsets;

    // Apply status filter
    if (selectedFilter === 'authenticated') {
      filtered = filtered.filter((t) => t.isAuthenticated);
    } else if (selectedFilter === 'not-authenticated') {
      filtered = filtered.filter((t) => !t.isAuthenticated);
    }

    // Apply search
    if (activeSearchQuery) {
      const query = activeSearchQuery.toLowerCase();
      filtered = filtered.filter(
        (t) =>
          t.name.toLowerCase().includes(query) ||
          t.displayName?.toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [configuredToolsets, selectedFilter, activeSearchQuery]);

  // Calculate filter counts for "My Toolsets" tab
  const filterCounts = useMemo<FilterCounts>(() => {
    const counts: FilterCounts = {
      all: configuredToolsets.length,
      authenticated: 0,
      'not-authenticated': 0,
    };

    configuredToolsets.forEach((toolset) => {
      if (toolset.isAuthenticated) {
        counts.authenticated += 1;
      } else {
        counts['not-authenticated'] += 1;
      }
    });

    return counts;
  }, [configuredToolsets]);

  // Filter options for "My Toolsets" tab
  const filterOptions = useMemo(
    () => [
      { key: 'all' as FilterType, label: 'All', icon: listIcon },
      { key: 'authenticated' as FilterType, label: 'Authenticated', icon: checkCircleIcon },
      {
        key: 'not-authenticated' as FilterType,
        label: 'Not Authenticated',
        icon: alertCircleIcon,
      },
    ],
    []
  );


  // ============================================================================
  // EVENT HANDLERS
  // ============================================================================

  const handleTabChange = useCallback((_event: React.SyntheticEvent, newTab: TabValue) => {
    setActiveTab(newTab);
    setSearchInput('');
    setActiveSearchQuery('');
    setSelectedFilter('all');
    // Data will be loaded by the useEffect that watches activeTab
  }, []);

  // Refresh all data (for refresh button)
  const refreshAllData = useCallback(async () => {
    if (isLoadingRef.current) return;
    
    isLoadingRef.current = true;
    setRefreshing(true);

    try {
      const [configured, registry] = await Promise.all([
        ToolsetApiService.getConfiguredToolsets(),
        ToolsetApiService.getRegistryToolsets({ includeTools: false, includeToolCount: true }),
      ]);

      setConfiguredToolsets(configured.toolsets);
      setRegistryToolsets(registry.toolsets);
    } catch (error) {
      console.error('Failed to refresh toolsets:', error);
      setSnackbar({
        open: true,
        message: 'Failed to refresh toolsets. Please try again.',
        severity: 'error',
      });
    } finally {
      setRefreshing(false);
      isLoadingRef.current = false;
    }
  }, []);

  const handleDelete = useCallback(
    async (toolsetName: string) => {
      if (!window.confirm(`Delete configuration for ${toolsetName}?`)) return;

      try {
        await ToolsetApiService.deleteToolsetConfig(toolsetName);
        setSnackbar({
          open: true,
          message: 'Toolset configuration deleted successfully',
          severity: 'success',
        });
        // Refresh both tabs after delete
        await refreshAllData();
      } catch (error: any) {
        console.error('Failed to delete toolset config:', error);
        const errorMessage =
          error.response?.status === 409
            ? error.response?.data?.message || 'Cannot delete: toolset is in use by agents'
            : 'Failed to delete toolset configuration';
        setSnackbar({
          open: true,
          message: errorMessage,
          severity: 'error',
        });
      }
    },
    [refreshAllData]
  );

  const handleRefresh = useCallback(() => {
    refreshAllData();
  }, [refreshAllData]);

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

  // ============================================================================
  // RENDER HELPERS
  // ============================================================================

  const handleShowToast = useCallback((message: string, severity: 'success' | 'error' | 'info' | 'warning' = 'success') => {
    setSnackbar({
      open: true,
      message,
      severity,
    });
  }, []);

  const renderRegistryToolsetCard = useCallback(
    (toolset: RegistryToolset) => {
      const isConfigured = isToolsetConfigured(toolset.name);
      return (
        <ToolsetRegistryCard
          key={toolset.name}
          toolset={toolset}
          isConfigured={isConfigured}
          onRefresh={refreshAllData}
          onShowToast={handleShowToast}
        />
      );
    },
    [isToolsetConfigured, refreshAllData, handleShowToast]
  );

  const renderConfiguredToolsetCard = useCallback(
    (toolset: Toolset) => (
      <ToolsetCard 
        key={toolset._id || toolset.name} 
        toolset={toolset} 
        onRefresh={refreshAllData}
        onShowToast={handleShowToast}
      />
    ),
    [refreshAllData, handleShowToast]
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
                  sx={{
                    fontWeight: 700,
                    fontSize: '1.5rem',
                    color: theme.palette.text.primary,
                  }}
                >
                  Toolsets Management
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    color: theme.palette.text.secondary,
                    fontSize: '0.875rem',
                  }}
                >
                  Configure and manage your toolset integrations
                </Typography>
              </Box>
            </Stack>

            <Tooltip title="Refresh">
              <IconButton onClick={handleRefresh} disabled={refreshing}>
                <Iconify
                  icon={refreshIcon}
                  width={20}
                  height={20}
                  sx={{
                    animation: refreshing ? 'spin 1s linear infinite' : 'none',
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
              sx={{
                '& .MuiTab-root': {
                  textTransform: 'none',
                  fontWeight: 600,
                  minHeight: 48,
                },
              }}
            >
              <Tab
                icon={<Iconify icon={checkCircleIcon} width={18} height={18} />}
                iconPosition="start"
                label={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <span>My Toolsets</span>
                    {configuredToolsets.length > 0 && (
                      <Chip
                        label={configuredToolsets.length}
                        size="small"
                        sx={{
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 700,
                          minWidth: 20,
                          '& .MuiChip-label': {
                            px: 0.75,
                          },
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
                    {registryToolsets.length > 0 && (
                      <Chip
                        label={registryToolsets.length}
                        size="small"
                        sx={{
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 700,
                          minWidth: 20,
                          '& .MuiChip-label': {
                            px: 0.75,
                          },
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
            {/* Search Bar */}
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
                    {isLoadingData ? (
                      <CircularProgress size={20} sx={{ color: theme.palette.primary.main }} />
                    ) : (
                      <Iconify
                        icon={magnifyIcon}
                        width={20}
                        height={20}
                        sx={{ color: theme.palette.text.secondary }}
                      />
                    )}
                  </InputAdornment>
                ),
                endAdornment: searchInput && (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      onClick={handleClearSearch}
                      sx={{
                        color: theme.palette.text.secondary,
                        '&:hover': {
                          backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                        },
                      }}
                    >
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
                  transition: theme.transitions.create(['border-color', 'box-shadow']),
                  '&:hover': {
                    borderColor: alpha(theme.palette.primary.main, 0.4),
                  },
                  '&.Mui-focused': {
                    boxShadow: `0 0 0 2px ${alpha(theme.palette.primary.main, 0.1)}`,
                  },
                },
              }}
            />

            {/* Filter Buttons - Only show on My Toolsets tab */}
            {activeTab === 'my-toolsets' && (
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography
                  variant="body2"
                  sx={{
                    color: theme.palette.text.secondary,
                    fontWeight: 500,
                    mr: 1,
                  }}
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
                        transition: theme.transitions.create(['background-color', 'border-color']),
                        ...(isSelected
                          ? {
                              backgroundColor: theme.palette.primary.main,
                              color: theme.palette.primary.contrastText,
                              '&:hover': {
                                backgroundColor: theme.palette.primary.dark,
                              },
                            }
                          : {
                              borderColor: theme.palette.divider,
                              color: theme.palette.text.primary,
                              backgroundColor: 'transparent',
                              '&:hover': {
                                borderColor: theme.palette.primary.main,
                                backgroundColor: alpha(theme.palette.primary.main, 0.04),
                              },
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
                            '& .MuiChip-label': {
                              px: 0.75,
                            },
                            ...(isSelected
                              ? {
                                  backgroundColor: isDark
                                    ? alpha(theme.palette.common.black, 0.3)
                                    : alpha(theme.palette.primary.contrastText, 0.4),
                                  color: isDark
                                    ? alpha(theme.palette.primary.main, 0.6)
                                    : alpha(theme.palette.primary.contrastText, 0.8),
                                }
                              : {
                                  backgroundColor: isDark
                                    ? alpha(theme.palette.common.white, 0.48)
                                    : alpha(theme.palette.primary.main, 0.1),
                                  color: isDark
                                    ? alpha(theme.palette.primary.main, 0.6)
                                    : theme.palette.primary.main,
                                }),
                          }}
                        />
                      )}
                    </Button>
                  );
                })}

                {activeSearchQuery && (
                  <>
                    <Box
                      sx={{
                        width: 1,
                        height: 24,
                        backgroundColor: theme.palette.divider,
                        mx: 1,
                      }}
                    />
                    <Typography
                      variant="caption"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontWeight: 500,
                      }}
                    >
                      {filteredConfiguredToolsets.length} result
                      {filteredConfiguredToolsets.length !== 1 ? 's' : ''}
                    </Typography>
                  </>
                )}
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
                ? 'Manage your configured toolsets. Configure authentication once and use across multiple agents.'
                : 'Browse available toolsets and configure them to use with your agents.'}
            </Typography>
          </Alert>

          {/* Tab Content */}
          {isFirstLoad || isLoadingData ? (
            /* Loading Skeletons */
            <Grid container spacing={2.5}>
              {Array.from({ length: SKELETON_COUNT }, (_, i) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={i}>
                  <Skeleton
                    variant="rectangular"
                    height={220}
                    sx={{
                      borderRadius: 2,
                      animation: 'pulse 1.5s ease-in-out infinite',
                      '@keyframes pulse': {
                        '0%, 100%': { opacity: 1 },
                        '50%': { opacity: 0.4 },
                      },
                    }}
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
                      : 'Get started by configuring toolsets from the Available tab'}
                  </Typography>
                </Paper>
              </Fade>
            ) : (
              <Grid container spacing={2.5}>
                {filteredConfiguredToolsets.map((toolset) => (
                  <Grid item xs={12} sm={6} md={4} lg={3} key={toolset._id || toolset.name}>
                    {renderConfiguredToolsetCard(toolset)}
                  </Grid>
                ))}
              </Grid>
            )
          ) : (
            /* Available Tab */
            filteredRegistryToolsets.length === 0 ? (
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
              <Grid container spacing={2.5}>
                {filteredRegistryToolsets.map((toolset) => (
                  <Grid item xs={12} sm={6} md={4} lg={3} key={toolset.name}>
                    {renderRegistryToolsetCard(toolset)}
                  </Grid>
                ))}
              </Grid>
            )
          )}
        </Box>
      </Box>

      {/* Configuration Dialog - handled by ToolsetRegistryCard */}

      {/* Page-level Snackbar (errors, refresh failures, deletes — no dialog conflict) */}
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
          sx={{
            borderRadius: 1.5,
            fontWeight: 600,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default ToolsetsPage;
