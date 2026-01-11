/**
 * OAuth Registry Component
 * 
 * Displays OAuth-enabled connector/tool types from the registry
 * Allows users to create OAuth app configurations
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import {
  Container,
  Paper,
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
  Fade,
  Stack,
  IconButton,
  CircularProgress,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import magniferIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import plusCircleIcon from '@iconify-icons/mdi/plus-circle';
import linkBrokenIcon from '@iconify-icons/mdi/link-off';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import appsIcon from '@iconify-icons/mdi/apps';
import { SnackbarState } from 'src/types/chat-sidebar';
import { ConnectorApiService } from '../../services/api';
import OAuthRegistryCard from './oauth-registry-card';
import OAuthAppDialog from './oauth-app-dialog';

// Constants
const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 12;

interface PaginationInfo {
  totalPages?: number;
  currentPage: number;
  totalItems?: number;
}

interface OAuthRegistryConnector {
  connectorType: string;
  name: string;
  appGroup?: string;
  appDescription?: string;
  appCategories?: string[];
  iconPath?: string;
}

interface OAuthRegistryProps {
  onBack?: () => void;
}

/**
 * OAuth Registry Component
 */
const OAuthRegistry: React.FC<OAuthRegistryProps> = ({ onBack }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // State Management
  const [connectors, setConnectors] = useState<OAuthRegistryConnector[]>([]);
  const [pagination, setPagination] = useState<PaginationInfo>({
    currentPage: INITIAL_PAGE,
    totalPages: undefined,
    totalItems: undefined,
  });
  const [isFirstLoad, setIsFirstLoad] = useState(true);
  const [isLoadingData, setIsLoadingData] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMorePages, setHasMorePages] = useState(true);
  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [selectedConnectorType, setSelectedConnectorType] = useState<string | null>(null);

  // Refs
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const requestIdRef = useRef(0);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isRequestInProgressRef = useRef(false);

  // Debounced search
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

  // Reset pagination when search changes
  useEffect(() => {
    setPagination({
      currentPage: INITIAL_PAGE,
      totalPages: undefined,
      totalItems: undefined,
    });
    setHasMorePages(true);
    setConnectors([]);
  }, [activeSearchQuery]);

  // Fetch connectors
  const fetchConnectors = useCallback(
    async (page: number, isLoadMore = false) => {
      if (isRequestInProgressRef.current) {
        return;
      }

      requestIdRef.current += 1;
      const currentRequestId = requestIdRef.current;
      isRequestInProgressRef.current = true;

      try {
        if (isLoadMore) {
          setIsLoadingMore(true);
        } else if (isFirstLoad) {
          setIsFirstLoad(true);
        } else {
          setIsLoadingData(true);
        }

        const result = await ConnectorApiService.getOAuthConfigRegistry(
          page,
          ITEMS_PER_PAGE,
          activeSearchQuery || undefined
        );

        if (currentRequestId !== requestIdRef.current) {
          return;
        }

        const newConnectors = (result.connectors || []).map((c: any) => ({
          ...c,
          connectorType: c.type || c.connectorType, // Map 'type' to 'connectorType' for consistency
        }));
        const paginationData = result.pagination || {};

        setConnectors((prev) => {
          if (page === INITIAL_PAGE) {
            return newConnectors;
          }

          const existingIds = new Set(prev.map((c) => c.connectorType));
          const uniqueNew = newConnectors.filter(
            (c) => !existingIds.has(c.connectorType)
          );
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
        console.error('Error fetching OAuth registry:', error);

        if (page === INITIAL_PAGE || connectors.length === 0) {
          setSnackbar({
            open: true,
            message: 'Failed to fetch OAuth registry. Please try again.',
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
    [activeSearchQuery, connectors.length, isFirstLoad]
  );

  // Fetch when page changes
  useEffect(() => {
    const isLoadMore = pagination.currentPage > INITIAL_PAGE;
    fetchConnectors(pagination.currentPage, isLoadMore);
  }, [pagination.currentPage, fetchConnectors]);

  // Infinite scroll
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
          setPagination((prev) => ({
            ...prev,
            currentPage: prev.currentPage + 1,
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
  }, [hasMorePages, isFirstLoad, isLoadingData]);

  // Event handlers
  const handleClearSearch = useCallback(() => {
    setSearchInput('');
    setActiveSearchQuery('');
  }, []);

  const handleCreateOAuthApp = useCallback((connectorType: string) => {
    setSelectedConnectorType(connectorType);
    setCreateDialogOpen(true);
  }, []);

  const handleCloseDialog = useCallback(() => {
    setCreateDialogOpen(false);
    setSelectedConnectorType(null);
  }, []);

  const handleCreateSuccess = useCallback(() => {
    setSnackbar({
      open: true,
      message: 'OAuth app created successfully!',
      severity: 'success',
    });
    handleCloseDialog();
  }, [handleCloseDialog]);

  const handleCloseSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);

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
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                    <Typography
                      variant="h5"
                      sx={{
                        fontWeight: 700,
                        fontSize: '1.5rem',
                        color: theme.palette.text.primary,
                      }}
                    >
                      OAuth Registry
                    </Typography>
                  </Box>
                  <Typography
                    variant="body2"
                    sx={{
                      color: theme.palette.text.secondary,
                      fontSize: '0.875rem',
                    }}
                  >
                    Browse and create OAuth app configurations for connectors and toolsets
                  </Typography>
                </Box>
              </Stack>

              {onBack && (
                <Button
                  variant="contained"
                  color="primary"
                  startIcon={<Iconify icon={arrowLeftIcon} width={18} height={18} />}
                  onClick={onBack}
                  sx={{
                    textTransform: 'none',
                    fontWeight: 600,
                    borderRadius: 1.5,
                    px: 3,
                    height: 40,
                  }}
                >
                  Back to My OAuth Apps
                </Button>
              )}
            </Stack>
          </Stack>
        </Box>

        {/* Content Section */}
        <Box sx={{ p: 3 }}>
          {/* Search Bar */}
          <Stack spacing={2} sx={{ mb: 3 }}>
        <TextField
          placeholder="Search OAuth-enabled connectors and tools..."
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
            },
          }}
        />
      </Stack>

      {/* Results Area */}
      <Box sx={{ position: 'relative', minHeight: 400 }}>
        {isFirstLoad ? (
          /* First Load Skeletons */
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
        ) : connectors.length === 0 ? (
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
                  ? 'No OAuth-enabled connectors found'
                  : 'No OAuth-enabled connectors available'}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  color: theme.palette.text.secondary,
                  maxWidth: 400,
                  mx: 'auto',
                }}
              >
                {activeSearchQuery
                  ? `No connectors match "${activeSearchQuery}". Try adjusting your search terms.`
                  : 'OAuth-enabled connectors will appear here once they are registered in the system.'}
              </Typography>
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
                  ? `Search Results (${connectors.length})`
                  : `Available OAuth Connectors (${connectors.length})`}
              </Typography>
              {pagination.totalItems !== undefined && pagination.totalItems > 0 && (
                <Typography
                  variant="caption"
                  sx={{
                    color: theme.palette.text.secondary,
                    fontWeight: 500,
                  }}
                >
                  Showing {connectors.length} of {pagination.totalItems}
                </Typography>
              )}
            </Stack>

            <Grid container spacing={2.5}>
              {connectors.map((connector, index) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={connector.connectorType}>
                  <Fade
                    in
                    timeout={300}
                    style={{ transitionDelay: `${Math.min(index * 30, 300)}ms` }}
                  >
                    <Box>
                      <OAuthRegistryCard
                        connector={connector}
                        onCreateClick={() => handleCreateOAuthApp(connector.connectorType)}
                      />
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
          </Stack>
        )}
      </Box>

      {/* OAuth App Dialog */}
      <OAuthAppDialog
        open={createDialogOpen}
        onClose={handleCloseDialog}
        onSuccess={handleCreateSuccess}
        mode="create"
        connectorType={selectedConnectorType || undefined}
      />

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
        </Box>
      </Box>
    </Container>
  );
};

export default OAuthRegistry;

