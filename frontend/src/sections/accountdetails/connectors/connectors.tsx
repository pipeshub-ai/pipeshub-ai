/**
 * Connectors Page - Complete Rewrite
 *
 * Features:
 * - Zero flickering during any operation
 * - Persistent UI elements (search, filters never disappear)
 * - Smooth transitions for all state changes
 * - Proper state management with clear separation
 * - Infinite scroll with perfect pagination
 * - Debounced search without UI disruption
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import {
  Paper,
  Container,
  Box,
  Typography,
  alpha,
  useTheme,
  Grid,
  InputAdornment,
  TextField,
  Skeleton,
  Alert,
  Snackbar,
  Button,
  Chip,
  Fade,
  Stack,
  Divider,
  IconButton,
  Tabs,
  Tab,
  CircularProgress,
} from '@mui/material';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import infoIcon from '@iconify-icons/mdi/info-circle';
import plusCircleIcon from '@iconify-icons/mdi/plus-circle';
import magniferIcon from '@iconify-icons/mdi/magnify';
import linkBrokenIcon from '@iconify-icons/mdi/link-off';
import linkIcon from '@iconify-icons/mdi/link-variant';
import listIcon from '@iconify-icons/mdi/format-list-bulleted';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import clockCircleIcon from '@iconify-icons/mdi/clock-outline';
import clearIcon from '@iconify-icons/mdi/close-circle';
import appsIcon from '@iconify-icons/mdi/apps';
import accountIcon from '@iconify-icons/mdi/account';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import { SnackbarState } from 'src/types/chat-sidebar';
import { useAccountType } from 'src/hooks/use-account-type';
import { useAdmin } from 'src/context/AdminContext';
import { ConnectorApiService } from './services/api';
import { Connector } from './types/types';
import ConnectorCard from './components/connector-card';

// Constants
const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 8;

// Types
type FilterType = 'all' | 'active' | 'configured' | 'not-configured';

interface PageState {
  personal: number;
  team: number;
}

interface FilterCounts {
  all: number;
  active: number;
  configured: number;
  'not-configured': number;
}

interface PaginationInfo {
  totalPages?: number;
  currentPage: number;
  totalItems?: number;
}

/**
 * Main Connectors Component
 */
const Connectors: React.FC = () => {
  // Hooks
  const theme = useTheme();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { isBusiness } = useAccountType();
  const { isAdmin, loading: adminLoading } = useAdmin();
  const isDark = theme.palette.mode === 'dark';

  // ============================================================================
  // STATE MANAGEMENT - Organized by concern
  // ============================================================================

  // Data State
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [pagination, setPagination] = useState<PaginationInfo>({
    currentPage: INITIAL_PAGE,
    totalPages: undefined,
    totalItems: undefined,
  });
  const [scopeCounts, setScopeCounts] = useState<{ personal: number; team: number }>({
    personal: 0,
    team: 0,
  });

  // Loading States - Separate and clear
  const [isFirstLoad, setIsFirstLoad] = useState(true); // Only true on very first load
  const [isLoadingData, setIsLoadingData] = useState(false); // Data fetching in progress
  const [isLoadingMore, setIsLoadingMore] = useState(false); // Infinite scroll loading
  const [isSwitchingScope, setIsSwitchingScope] = useState(false); // Tab switch in progress
  const [hasMorePages, setHasMorePages] = useState(true);

  // Filter State
  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState(''); // What's actually being searched
  // Initialize scope from URL params, fallback to default based on admin status
  const [selectedScope, setSelectedScope] = useState<'personal' | 'team'>(() => {
    const scopeParam = searchParams.get('scope');
    if (scopeParam === 'team' || scopeParam === 'personal') {
      return scopeParam;
    }
    // Fallback: admins default to 'team', others to 'personal'
    if (isAdmin) {
      return 'team';
    }
    return 'personal';
  });
  const [selectedFilter, setSelectedFilter] = useState<FilterType>('all');
  const [pageByScope, setPageByScope] = useState<PageState>({
    personal: INITIAL_PAGE,
    team: INITIAL_PAGE,
  });

  // UI State
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  // Refs
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const requestIdRef = useRef(0);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isRequestInProgressRef = useRef(false);
  const scopeInitializedRef = useRef(false);

  // ============================================================================
  // COMPUTED VALUES
  // ============================================================================

  const effectiveScope = useMemo(() => {
    // Use selectedScope for admins (they can switch), otherwise default to personal
    if (isBusiness && isAdmin) {
      return selectedScope; // Admins can switch between personal and team
    }
    if (isBusiness && !isAdmin) {
      return 'personal'; // Everyone else is locked to personal
    }
    if(!isBusiness){
      return 'team';
    }
    return 'personal'; // Everyone else is locked to personal
  }, [isBusiness, isAdmin, selectedScope]);

  const showScopeTabs = useMemo(
    () => isBusiness && isAdmin, // Only admins can switch between personal/team
    [isBusiness, isAdmin]
  );

  const currentPage = useMemo(() => pageByScope[effectiveScope], [pageByScope, effectiveScope]);

  // Filter connectors by scope
  const currentScopeConnectors = useMemo(
    () =>
      connectors.filter((c) =>
        effectiveScope === 'personal' ? c.scope === 'personal' || !c.scope : c.scope === 'team'
      ),
    [connectors, effectiveScope]
  );

  // Calculate filter counts
  const filterCounts = useMemo<FilterCounts>(() => {
    const counts: FilterCounts = {
      all: currentScopeConnectors.length,
      active: 0,
      configured: 0,
      'not-configured': 0,
    };

    currentScopeConnectors.forEach((connector) => {
      if (connector.isConfigured && connector.isActive) {
        counts.active += 1;
      } else if (connector.isConfigured && !connector.isActive) {
        counts.configured += 1;
      } else if (!connector.isConfigured) {
        counts['not-configured'] += 1;
      }
    });

    return counts;
  }, [currentScopeConnectors]);

  // Filter by status
  const filteredConnectors = useMemo(() => {
    if (selectedFilter === 'all') return currentScopeConnectors;

    return currentScopeConnectors.filter((connector) => {
      switch (selectedFilter) {
        case 'active':
          return connector.isConfigured && connector.isActive;
        case 'configured':
          return connector.isConfigured && !connector.isActive;
        case 'not-configured':
          return !connector.isConfigured;
        default:
          return true;
      }
    });
  }, [currentScopeConnectors, selectedFilter]);

  // Filter options
  const filterOptions = useMemo(
    () => [
      { key: 'all' as FilterType, label: 'All', icon: listIcon },
      { key: 'active' as FilterType, label: 'Active', icon: checkCircleIcon },
      { key: 'configured' as FilterType, label: 'Configured', icon: clockCircleIcon },
    ],
    []
  );

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);

  // ============================================================================
  // DEBOUNCED SEARCH - Updates activeSearchQuery after delay
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
  // AUTO-SET SCOPE - Based on admin status and URL params
  // ============================================================================

  // Auto-set scope when admin status changes
  // Admins default to team (but can switch to personal)
  // Non-admins are always locked to personal
  useEffect(() => {
    // Only initialize once to avoid overriding user selections or causing duplicate API calls
    if (scopeInitializedRef.current) {
      return;
    }

    const scopeParam = searchParams.get('scope');
    let targetScope: 'personal' | 'team' = 'personal';

    if (isAdmin) {
      // Admins: use URL param if valid, otherwise default to team
      targetScope = scopeParam === 'personal' ? 'personal' : 'team';
    } else if (!isBusiness || !isAdmin) {
      // Non-admins: always personal
      targetScope = 'personal';
    }

    setSelectedScope(targetScope);

    // Update URL if it doesn't match
    if (scopeParam !== targetScope) {
      setSearchParams({ scope: targetScope }, { replace: true });
    }

    scopeInitializedRef.current = true;
  }, [isBusiness, isAdmin, searchParams, setSearchParams]);

  // ============================================================================
  // RESET PAGINATION - When filters change
  // ============================================================================

  useEffect(() => {
    setPageByScope((prev) => ({
      ...prev,
      [effectiveScope]: INITIAL_PAGE,
    }));
    setHasMorePages(true);
    setConnectors([]);
    setPagination({
      currentPage: INITIAL_PAGE,
      totalPages: undefined,
      totalItems: undefined,
    });
  }, [effectiveScope, activeSearchQuery]);

  // ============================================================================
  // DATA FETCHING - Clean and organized
  // ============================================================================

  const fetchConnectors = useCallback(
    async (page: number, isLoadMore = false) => {
      // Prevent duplicate requests
      if (isRequestInProgressRef.current) {
        return;
      }

      // eslint-disable-next-line no-plusplus
      const currentRequestId = ++requestIdRef.current;
      isRequestInProgressRef.current = true;

      try {
        // Set appropriate loading state
        if (isLoadMore) {
          setIsLoadingMore(true);
        } else if (isFirstLoad) {
          setIsFirstLoad(true);
        } else {
          setIsLoadingData(true);
        }

        const result = await ConnectorApiService.getConnectorInstances(
          effectiveScope,
          page,
          ITEMS_PER_PAGE,
          activeSearchQuery || undefined
        );

        // Check if this request is still valid
        if (currentRequestId !== requestIdRef.current) {
          return;
        }

        const newConnectors = result.connectors || [];
        const paginationData = result.pagination || {};

        // Update connectors
        setConnectors((prev) => {
          if (page === INITIAL_PAGE) {
            return newConnectors;
          }

          // Prevent duplicates
          const existingIds = new Set(prev.map((c) => c._key || `${c.type}:${c.name}`));
          const uniqueNew = newConnectors.filter(
            (c) => !existingIds.has(c._key || `${c.type}:${c.name}`)
          );
          return [...prev, ...uniqueNew];
        });

        // Update pagination
        setPagination({
          currentPage: page,
          totalPages: paginationData.totalPages,
          totalItems: paginationData.totalItems,
        });

        // Update scope counts (only on first page to avoid unnecessary updates)
        if (page === INITIAL_PAGE && result.scopeCounts) {
          setScopeCounts(result.scopeCounts);
        }

        // Check if more pages exist
        const hasMore =
          paginationData.hasNext === true ||
          (typeof paginationData.totalPages === 'number'
            ? page < paginationData.totalPages
            : newConnectors.length === ITEMS_PER_PAGE);

        setHasMorePages(hasMore);
      } catch (error) {
        console.error('Error fetching connectors:', error);

        if (page === INITIAL_PAGE || connectors.length === 0) {
          setSnackbar({
            open: true,
            message: 'Failed to fetch connectors. Please try again.',
            severity: 'error',
          });
        }
      } finally {
        setIsFirstLoad(false);
        setIsLoadingData(false);
        setIsLoadingMore(false);
        isRequestInProgressRef.current = false;
      }
    },
    [effectiveScope, activeSearchQuery, connectors.length, isFirstLoad]
  );

  // Fetch when page changes
  useEffect(() => {
    const isLoadMore = currentPage > INITIAL_PAGE;
    fetchConnectors(currentPage, isLoadMore);
  }, [currentPage, fetchConnectors]);

  // ============================================================================
  // INFINITE SCROLL - Clean observer setup
  // ============================================================================

  useEffect(() => {
    if (observerRef.current) {
      observerRef.current.disconnect();
    }

    if (!sentinelRef.current || !hasMorePages || isFirstLoad) {
      return undefined;
    }

    observerRef.current = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;

        if (
          entry.isIntersecting &&
          !isRequestInProgressRef.current &&
          hasMorePages &&
          !isFirstLoad &&
          !isLoadingData
        ) {
          setPageByScope((prev) => ({
            ...prev,
            [effectiveScope]: prev[effectiveScope] + 1,
          }));
        }
      },
      {
        root: null,
        rootMargin: '200px',
        threshold: 0,
      }
    );

    observerRef.current.observe(sentinelRef.current);

    return () => {
      if (observerRef.current) {
        observerRef.current.disconnect();
      }
    };
  }, [hasMorePages, isFirstLoad, isLoadingData, effectiveScope]);

  // ============================================================================
  // EVENT HANDLERS
  // ============================================================================

  const handleScopeChange = useCallback(
    (_event: React.SyntheticEvent, newScope: 'personal' | 'team') => {
      // Only admins can switch scopes
      // Non-admin business users and individual users are locked to personal
      if (!isBusiness || !isAdmin) return;
      if (!isBusiness && newScope === 'team') return;

      setIsSwitchingScope(true);

      if (observerRef.current) {
        observerRef.current.disconnect();
      }

      setSelectedScope(newScope);
      setSearchInput('');
      setActiveSearchQuery('');
      setSelectedFilter('all');

      // Update URL with new scope
      setSearchParams({ scope: newScope }, { replace: true });

      // Clear switching state after transition
      setTimeout(() => {
        setIsSwitchingScope(false);
      }, 400);
    },
    [isBusiness, isAdmin, setSearchParams]
  );

  const handleFilterChange = useCallback((filter: FilterType) => {
    setSelectedFilter(filter);
  }, []);

  const handleClearSearch = useCallback(() => {
    setSearchInput('');
    setActiveSearchQuery('');
  }, []);

  const handleBrowseRegistry = useCallback(() => {
    const basePath = isBusiness
      ? '/account/company-settings/settings/connector/registry'
      : '/account/individual/settings/connector/registry';
    if (isBusiness && isAdmin) {
      navigate(`${basePath}?scope=${effectiveScope}`);
    } else {
      navigate(`${basePath}`);
    }
  }, [isBusiness, isAdmin, effectiveScope, navigate]);

  const handleCloseSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  // ============================================================================
  // RENDER
  // ============================================================================

  // Show loading screen while admin status is being determined
  // This must be after all hooks to comply with Rules of Hooks
  if (adminLoading) {
    return (
      <Container maxWidth="xl" sx={{ py: 2, display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 400 }}>
        <CircularProgress size={48} />
      </Container>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
        }}
      >
        {/* Header Section - Always Visible */}
        <Box
          sx={{
            p: 3,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDark
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.grey[50], 0.5),
          }}
        >
          <Stack spacing={2}>
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
                    icon={linkIcon}
                    width={20}
                    height={20}
                    sx={{ color: theme.palette.primary.main }}
                  />
                </Box>
                <Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                    <Typography
                      variant="h5"
                      sx={{
                        fontWeight: 700,
                        fontSize: '1.5rem',
                        color: theme.palette.text.primary,
                      }}
                    >
                      {isBusiness && isAdmin && effectiveScope === 'team'
                        ? 'Team Connectors'
                        : 'My Connectors'}
                    </Typography>
                    {!showScopeTabs && scopeCounts.personal > 0 && (
                      <Chip
                        label={scopeCounts.personal}
                        size="small"
                        icon={<Iconify icon={accountIcon} width={14} height={14} />}
                        sx={{
                          height: 24,
                          fontSize: '0.75rem',
                          fontWeight: 700,
                          backgroundColor: isDark
                            ?  alpha(theme.palette.primary.contrastText, 0.9)
                            : alpha(theme.palette.primary.main, 0.12),
                          color: theme.palette.primary.main,
                          border: `1px solid ${alpha(theme.palette.primary.main, 0.3)}`,
                          '& .MuiChip-icon': {
                            color: theme.palette.primary.main,
                          },
                        }}
                      />
                    )}
                  </Box>
                  <Typography
                    variant="body2"
                    sx={{
                      color: theme.palette.text.secondary,
                      fontSize: '0.875rem',
                    }}
                  >
                    {isBusiness && isAdmin && effectiveScope === 'team'
                      ? 'Manage team connector instances for your organization'
                      : 'Manage your configured connector instances'}
                    {filterCounts.active > 0 && (
                      <Chip
                        label={`${filterCounts.active} active`}
                        size="small"
                        sx={{
                          ml: 1,
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 600,
                          backgroundColor: isDark
                            ? alpha(theme.palette.common.white, 0.48)
                            : alpha(theme.palette.success.main, 0.1),
                          color: isDark
                            ? alpha(theme.palette.primary.main, 0.6)
                            : theme.palette.success.main,
                          border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                        }}
                      />
                    )}
                  </Typography>
                </Box>
              </Stack>

              <Button
                variant="contained"
                color="primary"
                startIcon={<Iconify icon={plusCircleIcon} width={18} height={18} />}
                onClick={handleBrowseRegistry}
                sx={{
                  textTransform: 'none',
                  fontWeight: 600,
                  borderRadius: 1.5,
                  px: 3,
                  height: 40,
                }}
              >
                Add New Connectors
              </Button>
            </Stack>

            {/* Scope Tabs - Only show for non-admin business users */}
            {showScopeTabs && (
              <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
                <Tabs
                  value={effectiveScope}
                  onChange={handleScopeChange}
                  sx={{
                    '& .MuiTab-root': {
                      textTransform: 'none',
                      fontWeight: 600,
                      minHeight: 48,
                    },
                  }}
                >
                  <Tab
                    icon={<Iconify icon={accountIcon} width={18} height={18} />}
                    iconPosition="start"
                    label={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                        <span>Personal</span>
                        {scopeCounts.personal > 0 && (
                          <Chip
                            label={scopeCounts.personal}
                            size="small"
                            sx={{
                              height: 20,
                              fontSize: '0.6875rem',
                              fontWeight: 700,
                              minWidth: 20,
                              '& .MuiChip-label': {
                                px: 0.75,
                              },
                              backgroundColor: effectiveScope === 'personal'
                              ? isDark
                              ? alpha(theme.palette.primary.contrastText, 0.9)
                              : alpha(theme.palette.primary.main, 0.8)
                            : isDark
                              ? alpha(theme.palette.text.primary, 0.4)
                              : alpha(theme.palette.text.primary, 0.12),
                              color: effectiveScope === 'personal'
                                ? theme.palette.primary.contrastText
                                : theme.palette.text.primary,
                              border: effectiveScope === 'personal'
                                ? `1px solid ${alpha(theme.palette.primary.contrastText, 0.3)}`
                                : `1px solid ${alpha(theme.palette.text.primary, 0.2)}`,
                            }}
                          />
                        )}
                      </Box>
                    }
                    value="personal"
                    sx={{ mr: 1 }}
                  />
                  <Tab
                    icon={<Iconify icon={accountGroupIcon} width={18} height={18} />}
                    iconPosition="start"
                    label={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                        <span>Team</span>
                        {scopeCounts.team > 0 && (
                          <Chip
                            label={scopeCounts.team}
                            size="small"
                            sx={{
                              height: 20,
                              fontSize: '0.6875rem',
                              fontWeight: 700,
                              minWidth: 20,
                              '& .MuiChip-label': {
                                px: 0.75,
                              },
                              backgroundColor: effectiveScope === 'team'
                                ? isDark
                                  ? alpha(theme.palette.primary.contrastText, 0.9)
                                  : alpha(theme.palette.primary.main, 0.8)
                                : isDark
                                  ? alpha(theme.palette.text.primary, 0.4)
                                  : alpha(theme.palette.text.primary, 0.12),
                              color: effectiveScope === 'team'
                                ? theme.palette.primary.contrastText
                                : theme.palette.text.primary,
                              border: effectiveScope === 'team'
                                ? `1px solid ${alpha(theme.palette.primary.contrastText, 0.3)}`
                                : `1px solid ${alpha(theme.palette.text.primary, 0.2)}`,
                            }}
                          />
                        )}
                      </Box>
                    }
                    value="team"
                  />
                </Tabs>
              </Box>
            )}
          </Stack>
        </Box>

        {/* Content Section */}
        <Box sx={{ p: 3 }}>
          {/* Search and Filters - Always Visible, Never Disappear */}
          <Stack spacing={2} sx={{ mb: 3 }}>
            {/* Search Bar */}
            <TextField
              placeholder="Search connectors by name, type, or category..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              size="small"
              fullWidth
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    {isLoadingData && !isFirstLoad ? (
                      <CircularProgress size={20} sx={{ color: theme.palette.primary.main }} />
                    ) : (
                      <Iconify
                        icon={magniferIcon}
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

            {/* Filter Buttons */}
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
                  <Divider orientation="vertical" sx={{ height: 24, mx: 1 }} />
                  <Typography
                    variant="caption"
                    sx={{
                      color: theme.palette.text.secondary,
                      fontWeight: 500,
                    }}
                  >
                    {filteredConnectors.length} result{filteredConnectors.length !== 1 ? 's' : ''}
                  </Typography>
                </>
              )}
            </Stack>
          </Stack>

          {/* Results Area with Overlay for Scope Switching */}
          <Box sx={{ position: 'relative', minHeight: 400 }}>
            {/* Scope Switch Overlay */}
            {isSwitchingScope && (
              <Fade in timeout={200}>
                <Box
                  sx={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    backgroundColor: alpha(
                      isDark ? theme.palette.background.default : theme.palette.background.paper,
                      0.8
                    ),
                    backdropFilter: 'blur(8px)',
                    zIndex: 10,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderRadius: 2,
                  }}
                >
                  <Stack alignItems="center" spacing={2}>
                    <CircularProgress size={48} thickness={4} />
                    <Typography
                      variant="h6"
                      sx={{
                        fontWeight: 600,
                        color: theme.palette.text.primary,
                      }}
                    >
                      Switching to {selectedScope === 'personal' ? 'Personal' : 'Team'}{' '}
                      Connectors...
                    </Typography>
                  </Stack>
                </Box>
              </Fade>
            )}

            {/* Content Area */}
            <Box
              sx={{
                opacity: isSwitchingScope ? 0.3 : 1,
                transition: 'opacity 0.3s ease-in-out',
                pointerEvents: isSwitchingScope ? 'none' : 'auto',
              }}
            >
              {isFirstLoad ? (
                /* First Load Skeletons */
                <Stack spacing={2}>
                  <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1.5 }} />
                  <Grid container spacing={2.5}>
                    {loadingSkeletons.map((index) => (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={index}>
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
                </Stack>
              ) : filteredConnectors.length === 0 ? (
                /* Empty State */
                <Fade in timeout={300}>
                  <Paper
                    elevation={0}
                    sx={{
                      py: 6,
                      px: 4,
                      textAlign: 'center',
                      borderRadius: 2,
                      border: `1px solid ${theme.palette.divider}`,
                      backgroundColor: alpha(
                        isDark ? theme.palette.background.default : theme.palette.grey[50],
                        0.5
                      ),
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
                        icon={activeSearchQuery ? magniferIcon : linkBrokenIcon}
                        width={32}
                        height={32}
                        sx={{ color: theme.palette.text.disabled }}
                      />
                    </Box>
                    <Typography
                      variant="h6"
                      sx={{
                        mb: 1,
                        fontWeight: 600,
                        color: theme.palette.text.primary,
                      }}
                    >
                      {activeSearchQuery
                        ? 'No connectors found'
                        : 'No connector instances configured'}
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        color: theme.palette.text.secondary,
                        maxWidth: 400,
                        mx: 'auto',
                        mb: 3,
                      }}
                    >
                      {activeSearchQuery ? (
                        <>
                          No connectors match &quot;{activeSearchQuery}&quot;. Try adjusting your
                          search terms or{' '}
                          <Button
                            variant="text"
                            size="small"
                            onClick={handleClearSearch}
                            sx={{
                              textTransform: 'none',
                              p: 0,
                              minWidth: 'auto',
                              fontWeight: 600,
                              verticalAlign: 'baseline',
                            }}
                          >
                            clear the search
                          </Button>
                        </>
                      ) : (
                        'Get started by browsing available connectors and creating your first instance.'
                      )}
                    </Typography>
                    {!activeSearchQuery && (
                      <Button
                        variant="outlined"
                        startIcon={<Iconify icon={appsIcon} width={20} height={20} />}
                        onClick={handleBrowseRegistry}
                        sx={{
                          textTransform: 'none',
                          fontWeight: 600,
                          borderRadius: 1.5,
                        }}
                      >
                        Browse Available Connectors
                      </Button>
                    )}
                  </Paper>
                </Fade>
              ) : (
                /* Results Grid */
                <Stack spacing={2}>
                  {/* Results Header */}
                  <Stack direction="row" alignItems="center" justifyContent="space-between">
                    <Typography
                      variant="h6"
                      sx={{
                        fontWeight: 600,
                        fontSize: '1.125rem',
                        color: theme.palette.text.primary,
                      }}
                    >
                      {activeSearchQuery
                        ? `Search Results (${filteredConnectors.length})`
                        : selectedFilter === 'all'
                          ? `All Instances (${filteredConnectors.length})`
                          : selectedFilter === 'active'
                            ? `Active Instances (${filteredConnectors.length})`
                            : selectedFilter === 'configured'
                              ? `Ready Instances (${filteredConnectors.length})`
                              : `Setup Required (${filteredConnectors.length})`}
                    </Typography>
                    {pagination.totalItems !== undefined && pagination.totalItems > 0 && (
                      <Typography
                        variant="caption"
                        sx={{
                          color: theme.palette.text.secondary,
                          fontWeight: 500,
                        }}
                      >
                        Showing {filteredConnectors.length} of {pagination.totalItems}
                      </Typography>
                    )}
                  </Stack>

                  {/* Connectors Grid */}
                  <Grid container spacing={2.5}>
                    {filteredConnectors.map((connector, index) => (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={connector._key}>
                        <Fade
                          in
                          timeout={300}
                          style={{ transitionDelay: `${Math.min(index * 30, 300)}ms` }}
                        >
                          <Box>
                            <ConnectorCard connector={connector} isBusiness={isBusiness} />
                          </Box>
                        </Fade>
                      </Grid>
                    ))}
                  </Grid>

                  {/* Infinite Scroll Sentinel */}
                  <Box ref={sentinelRef} sx={{ height: 1 }} />

                  {/* Loading More Indicator */}
                  {isLoadingMore && (
                    <Fade in>
                      <Paper
                        elevation={0}
                        sx={{
                          py: 3,
                          px: 2,
                          textAlign: 'center',
                          borderRadius: 2,
                          backgroundColor: alpha(theme.palette.primary.main, 0.04),
                          border: `1px solid ${alpha(theme.palette.primary.main, 0.1)}`,
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="center"
                          justifyContent="center"
                          spacing={2}
                        >
                          <CircularProgress size={24} thickness={4} />
                          <Typography
                            variant="body2"
                            sx={{
                              color: theme.palette.text.primary,
                              fontWeight: 500,
                            }}
                          >
                            Loading more connectors...
                          </Typography>
                        </Stack>
                      </Paper>
                    </Fade>
                  )}

                  {/* End of Results */}
                  {!hasMorePages && connectors.length > ITEMS_PER_PAGE && (
                    <Fade in>
                      <Paper
                        elevation={0}
                        sx={{
                          py: 2,
                          px: 2,
                          textAlign: 'center',
                          borderRadius: 2,
                          backgroundColor: alpha(theme.palette.success.main, 0.04),
                          border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="center"
                          justifyContent="center"
                          spacing={1}
                        >
                          <Iconify
                            icon={checkCircleIcon}
                            width={18}
                            height={18}
                            sx={{ color: theme.palette.success.main }}
                          />
                          <Typography
                            variant="body2"
                            sx={{
                              color: theme.palette.success.main,
                              fontWeight: 600,
                            }}
                          >
                            All connectors loaded
                          </Typography>
                        </Stack>
                      </Paper>
                    </Fade>
                  )}
                </Stack>
              )}
            </Box>
          </Box>

          {/* Info Alert */}
          {!isFirstLoad && !isSwitchingScope && connectors.length > 0 && (
            <Fade in timeout={600}>
              <Alert
                variant="outlined"
                severity="info"
                icon={<Iconify icon={infoIcon} width={20} height={20} />}
                sx={{
                  mt: 3,
                  borderRadius: 1.5,
                  borderColor: alpha(theme.palette.info.main, 0.2),
                  backgroundColor: alpha(theme.palette.info.main, 0.04),
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  Click any connector to configure settings and start syncing data automatically.
                  Refer to{' '}
                  <a
                    href="https://docs.pipeshub.com/connectors/overview"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      color: theme.palette.primary.main,
                      textDecoration: 'none',
                      fontWeight: 600,
                    }}
                  >
                    the documentation
                  </a>{' '}
                  for more information.
                </Typography>
              </Alert>
            </Fade>
          )}
        </Box>
      </Box>

      {/* Snackbar */}
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

export default Connectors;
