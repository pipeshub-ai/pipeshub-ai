/**
 * Connector Registry Page - Complete Rewrite
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
  IconButton,
  Tabs,
  Tab,
  CircularProgress,
} from '@mui/material';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import appsIcon from '@iconify-icons/mdi/apps';
import magniferIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import accountIcon from '@iconify-icons/mdi/account';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import { SnackbarState } from 'src/types/chat-sidebar';
import { useAccountType } from 'src/hooks/use-account-type';
import { ConnectorApiService } from '../services/api';
import { ConnectorRegistry as ConnectorRegistryType } from '../types/types';
import ConnectorRegistryCard from '../components/connector-registry-card';

// Constants
const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 12;

// Types
interface PageState {
  personal: number;
  team: number;
}

interface PaginationInfo {
  totalPages?: number;
  currentPage: number;
  totalItems?: number;
}

/**
 * Main Connector Registry Component
 */
const ConnectorRegistry: React.FC = () => {
  // Hooks
  const [searchParams] = useSearchParams();
  const theme = useTheme();
  const navigate = useNavigate();
  const { isBusiness } = useAccountType();
  const isDark = theme.palette.mode === 'dark';

  const initialScope = (searchParams.get('scope') as 'personal' | 'team') || 'personal';

  // ============================================================================
  // STATE MANAGEMENT
  // ============================================================================

  // Data State
  const [connectors, setConnectors] = useState<ConnectorRegistryType[]>([]);
  const [pagination, setPagination] = useState<PaginationInfo>({
    currentPage: INITIAL_PAGE,
    totalPages: undefined,
    totalItems: undefined,
  });

  // Loading States
  const [isFirstLoad, setIsFirstLoad] = useState(true);
  const [isLoadingData, setIsLoadingData] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSwitchingScope, setIsSwitchingScope] = useState(false);
  const [hasMorePages, setHasMorePages] = useState(true);

  // Filter State
  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [selectedScope, setSelectedScope] = useState<'personal' | 'team'>(
    isBusiness ? initialScope : 'personal'
  );
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

  // ============================================================================
  // COMPUTED VALUES
  // ============================================================================

  const effectiveScope = useMemo(
    () => (isBusiness ? selectedScope : 'personal'),
    [isBusiness, selectedScope]
  );

  const currentPage = useMemo(
    () => pageByScope[effectiveScope],
    [pageByScope, effectiveScope]
  );

  // Extract unique categories
  const categories = useMemo(() => {
    const categorySet = new Set<string>();
    connectors.forEach((connector) => {
      connector.appCategories?.forEach((category) => {
        if (category) categorySet.add(category);
      });
    });
    return ['all', ...Array.from(categorySet).sort()];
  }, [connectors]);

  // Filter by category (client-side)
  const filteredConnectors = useMemo(() => {
    if (selectedCategory === 'all') return connectors;
    return connectors.filter((c) => c.appCategories?.includes(selectedCategory));
  }, [connectors, selectedCategory]);

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);

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
  // RESET PAGINATION
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
  }, [effectiveScope, activeSearchQuery, selectedCategory]);

  // ============================================================================
  // DATA FETCHING
  // ============================================================================

  const fetchConnectorRegistry = useCallback(
    async (page: number, isLoadMore = false) => {
      if (isRequestInProgressRef.current) {
        return;
      }

      // eslint-disable-next-line no-plusplus
      const currentRequestId = ++requestIdRef.current;
      isRequestInProgressRef.current = true;

      try {
        if (isLoadMore) {
          setIsLoadingMore(true);
        } else if (isFirstLoad) {
          setIsFirstLoad(true);
        } else {
          setIsLoadingData(true);
        }

        const result = await ConnectorApiService.getConnectorRegistry(
          effectiveScope,
          page,
          ITEMS_PER_PAGE,
          activeSearchQuery || undefined
        );

        if (currentRequestId !== requestIdRef.current) {
          return;
        }

        const newConnectors = result.connectors || [];
        const paginationData = result.pagination || {};

        setConnectors((prev) => {
          if (page === INITIAL_PAGE) {
            return newConnectors;
          }

          const existingIds = new Set(prev.map((c) => c.type));
          const uniqueNew = newConnectors.filter((c) => !existingIds.has(c.type));
          return [...prev, ...uniqueNew];
        });

        setPagination({
          currentPage: page,
          totalPages: paginationData.totalPages,
          totalItems: paginationData.totalItems,
        });

        const hasMore =
          paginationData.hasNext === true ||
          (typeof paginationData.totalPages === 'number'
            ? page < paginationData.totalPages
            : newConnectors.length === ITEMS_PER_PAGE);

        setHasMorePages(hasMore);
      } catch (error) {
        console.error('Error fetching connector registry:', error);

        if (page === INITIAL_PAGE || connectors.length === 0) {
          setSnackbar({
            open: true,
            message: 'Failed to fetch connector registry. Please try again.',
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

  useEffect(() => {
    const isLoadMore = currentPage > INITIAL_PAGE;
    fetchConnectorRegistry(currentPage, isLoadMore);
  }, [currentPage, fetchConnectorRegistry]);

  // ============================================================================
  // INFINITE SCROLL
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
      if (!isBusiness && newScope === 'team') return;

      setIsSwitchingScope(true);

      if (observerRef.current) {
        observerRef.current.disconnect();
      }

      setSelectedScope(newScope);
      setSearchInput('');
      setActiveSearchQuery('');
      setSelectedCategory('all');

      setTimeout(() => {
        setIsSwitchingScope(false);
      }, 400);
    },
    [isBusiness]
  );

  const handleCategoryChange = useCallback((category: string) => {
    setSelectedCategory(category);
  }, []);

  const handleClearSearch = useCallback(() => {
    setSearchInput('');
    setActiveSearchQuery('');
  }, []);

  const handleSearchSubmit = useCallback(() => {
    setActiveSearchQuery(searchInput.trim());
  }, [searchInput]);

  const handleBackToInstances = useCallback(() => {
    const basePath = isBusiness
      ? '/account/company-settings/settings/connector'
      : '/account/individual/settings/connector';
    navigate(basePath);
  }, [isBusiness, navigate]);

  const handleCloseSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  // ============================================================================
  // RENDER
  // ============================================================================

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
        {/* Header Section */}
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
                    icon={appsIcon}
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
                      mb: 0.5,
                    }}
                  >
                    Available Connectors
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      color: theme.palette.text.secondary,
                      fontSize: '0.875rem',
                    }}
                  >
                    Browse and configure new connector instances
                  </Typography>
                </Box>
              </Stack>

              <Button
                variant="contained"
                color="primary"
                startIcon={<Iconify icon={arrowLeftIcon} width={18} height={18} />}
                onClick={handleBackToInstances}
                sx={{
                  textTransform: 'none',
                  fontWeight: 600,
                  borderRadius: 1.5,
                  px: 3,
                  height: 40,
                }}
              >
                Back to My Connectors
              </Button>
            </Stack>

            {/* Scope Tabs */}
            <Box sx={{ borderBottom: 1, borderColor: 'divider', mt: 2 }}>
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
                  label="Personal Connectors"
                  value="personal"
                  sx={{ mr: 1 }}
                />
                {isBusiness && (
                  <Tab
                    icon={<Iconify icon={accountGroupIcon} width={18} height={18} />}
                    iconPosition="start"
                    label="Team Connectors"
                    value="team"
                  />
                )}
              </Tabs>
            </Box>
          </Stack>
        </Box>

        {/* Content Section */}
        <Box sx={{ p: 3 }}>
          {/* Search and Filters - Always Visible */}
          <Stack spacing={2} sx={{ mb: 3 }}>
            {/* Search Bar */}
            <TextField
              placeholder="Search connectors by name, category, or description..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleSearchSubmit();
                }
              }}
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
                endAdornment: (
                  <InputAdornment position="end">
                    {searchInput && (
                      <IconButton
                        size="small"
                        onClick={handleClearSearch}
                        sx={{
                          color: theme.palette.text.secondary,
                          mr: 0.5,
                          '&:hover': {
                            backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                          },
                        }}
                      >
                        <Iconify icon={clearIcon} width={16} height={16} />
                      </IconButton>
                    )}
                    <Button
                      onClick={handleSearchSubmit}
                      variant="contained"
                      size="small"
                      sx={{
                        ml: 1,
                        borderRadius: 1,
                        textTransform: 'none',
                        fontWeight: 700,
                        minWidth: 80,
                      }}
                    >
                      Search
                    </Button>
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

            {/* Category Chips */}
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Typography
                variant="body2"
                sx={{
                  color: theme.palette.text.secondary,
                  fontWeight: 500,
                  mr: 1,
                }}
              >
                Category:
              </Typography>
              {categories.map((category) => {
                const isSelected = selectedCategory === category;
                return (
                  <Chip
                    key={category}
                    label={category === 'all' ? 'All' : category}
                    onClick={() => handleCategoryChange(category)}
                    sx={{
                      textTransform: 'capitalize',
                      fontWeight: 600,
                      fontSize: '0.8125rem',
                      cursor: 'pointer',
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
                            backgroundColor: 'transparent',
                            borderColor: theme.palette.divider,
                            color: theme.palette.text.primary,
                            '&:hover': {
                              borderColor: theme.palette.primary.main,
                              backgroundColor: alpha(theme.palette.primary.main, 0.04),
                            },
                          }),
                    }}
                    variant={isSelected ? 'filled' : 'outlined'}
                  />
                );
              })}
            </Stack>
          </Stack>

          {/* Results Area */}
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

            {/* Content */}
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
                    <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
                      No connectors found
                    </Typography>
                    <Typography variant="body2" sx={{ color: theme.palette.text.secondary, mb: 2 }}>
                      Try adjusting your search or category filter
                    </Typography>
                    {(activeSearchQuery || selectedCategory !== 'all') && (
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => {
                          handleClearSearch();
                          setSelectedCategory('all');
                        }}
                        sx={{
                          textTransform: 'none',
                          fontWeight: 600,
                          borderRadius: 1,
                        }}
                      >
                        Clear Filters
                      </Button>
                    )}
                  </Paper>
                </Fade>
              ) : (
                /* Results Grid */
                <Stack spacing={2}>
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
                        : selectedCategory === 'all'
                          ? `All Connectors`
                          : `${selectedCategory} (${filteredConnectors.length})`}
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
                      <Grid item xs={12} sm={6} md={4} lg={3} key={connector.type}>
                        <Fade
                          in
                          timeout={300}
                          style={{ transitionDelay: `${Math.min(index * 30, 300)}ms` }}
                        >
                          <Box>
                            <ConnectorRegistryCard connector={connector} scope={selectedScope} />
                          </Box>
                        </Fade>
                      </Grid>
                    ))}
                  </Grid>

                  {/* Sentinel */}
                  <Box ref={sentinelRef} sx={{ height: 1 }} />

                  {/* Loading More */}
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
          sx={{ borderRadius: 1.5, fontWeight: 600 }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default ConnectorRegistry;