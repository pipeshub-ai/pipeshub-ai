/**
 * My OAuth Apps Component
 * 
 * Displays all created OAuth app configurations
 * Allows users to view, edit, and delete their OAuth apps
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import {
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
  Chip,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import magniferIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import { SnackbarState } from 'src/types/chat-sidebar';
import { ConnectorApiService } from '../../services/api';
import OAuthAppCard from './oauth-app-card';
import OAuthAppDialog, { OAuthAppDialogMode } from './oauth-app-dialog';

// Constants
const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 8;

interface PaginationInfo {
  totalPages?: number;
  currentPage: number;
  totalItems?: number;
}

interface OAuthApp {
  _id: string;
  oauthInstanceName: string;
  connectorType: string;
  iconPath?: string;
  appGroup?: string;
  appDescription?: string;
  appCategories?: string[];
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
}

/**
 * My OAuth Apps Component
 */
const MyOAuthApps: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // State Management
  const [oauthApps, setOAuthApps] = useState<OAuthApp[]>([]);
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
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<OAuthAppDialogMode>('create');
  const [selectedConnectorType, setSelectedConnectorType] = useState<string | null>(null);
  const [selectedOAuthConfigId, setSelectedOAuthConfigId] = useState<string | null>(null);

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
    setOAuthApps([]);
  }, [activeSearchQuery]);

  // Fetch all OAuth apps using the aggregated API
  const fetchOAuthApps = useCallback(
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

        // Use the new getAllOAuthConfigs method that aggregates across all connector types
        const result = await ConnectorApiService.getAllOAuthConfigs(
          page,
          ITEMS_PER_PAGE,
          activeSearchQuery || undefined
        );

        if (currentRequestId !== requestIdRef.current) {
          return;
        }

        const newApps = result.oauthConfigs || [];
        const paginationData = result.pagination || {};

        setOAuthApps((prev) => {
          if (page === INITIAL_PAGE) {
            return newApps;
          }
          // Avoid duplicates
          const existingIds = new Set(prev.map((app) => app._id));
          const uniqueNew = newApps.filter((app) => !existingIds.has(app._id));
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
            : newApps.length === ITEMS_PER_PAGE);

        setHasMorePages(hasMore);
      } catch (error) {
        console.error('Error fetching OAuth apps:', error);

        if (page === INITIAL_PAGE || oauthApps.length === 0) {
          setSnackbar({
            open: true,
            message: 'Failed to fetch OAuth apps. Please try again.',
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
    [activeSearchQuery, oauthApps.length, isFirstLoad]
  );

  // Fetch when page changes
  useEffect(() => {
    const isLoadMore = pagination.currentPage > INITIAL_PAGE;
    fetchOAuthApps(pagination.currentPage, isLoadMore);
  }, [pagination.currentPage, fetchOAuthApps]);

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

  const handleCreateOAuthApp = useCallback(() => {
    setSelectedConnectorType(null);
    setSelectedOAuthConfigId(null);
    setDialogMode('create');
    setDialogOpen(true);
  }, []);

  const handleViewOAuthApp = useCallback((app: OAuthApp) => {
    setSelectedConnectorType(app.connectorType);
    setSelectedOAuthConfigId(app._id);
    setDialogMode('view');
    setDialogOpen(true);
  }, []);

  const handleDialogModeChange = useCallback((newMode: OAuthAppDialogMode) => {
    setDialogMode(newMode);
  }, []);

  const handleCloseDialog = useCallback(() => {
    setDialogOpen(false);
    setSelectedConnectorType(null);
    setSelectedOAuthConfigId(null);
  }, []);

  const handleDialogSuccess = useCallback(() => {
    const messages = {
      create: 'OAuth app created successfully!',
      edit: 'OAuth app updated successfully!',
      view: '',
    };
    if (messages[dialogMode]) {
      setSnackbar({
        open: true,
        message: messages[dialogMode],
        severity: 'success',
      });
    }
    handleCloseDialog();
    // Refresh the list
    setPagination({ currentPage: INITIAL_PAGE, totalPages: undefined, totalItems: undefined });
    setOAuthApps([]);
  }, [dialogMode, handleCloseDialog]);


  const handleCloseSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);

  return (
    <>
      {/* Header */}
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 3 }}>
        <Typography
          variant="h6"
          sx={{
            fontWeight: 600,
            fontSize: '1.125rem',
            color: theme.palette.text.primary,
          }}
        >
          My OAuth Apps
        </Typography>
      </Stack>

      {/* Search Bar */}
      <Stack spacing={2} sx={{ mb: 3 }}>
        <TextField
          placeholder="Search OAuth apps by name or connector type..."
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
                  height={200}
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
        ) : oauthApps.length === 0 ? (
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
                  icon="mdi:link-off"
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
                  ? 'No OAuth apps found'
                  : 'No OAuth apps configured'}
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
                {activeSearchQuery
                  ? `No OAuth apps match "${activeSearchQuery}". Try adjusting your search terms.`
                  : 'Get started by creating your first OAuth app configuration from the Registry tab.'}
              </Typography>
              {!activeSearchQuery && (
                <Button
                  variant="outlined"
                  startIcon={<Iconify icon="mdi:plus-circle" width={20} height={20} />}
                  onClick={handleCreateOAuthApp}
                  sx={{
                    textTransform: 'none',
                    fontWeight: 600,
                    borderRadius: 1.5,
                  }}
                >
                  Create OAuth App
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
                  ? `Search Results (${oauthApps.length})`
                  : `All OAuth Apps (${oauthApps.length})`}
              </Typography>
              {pagination.totalItems !== undefined && pagination.totalItems > 0 && (
                <Typography
                  variant="caption"
                  sx={{
                    color: theme.palette.text.secondary,
                    fontWeight: 500,
                  }}
                >
                  Showing {oauthApps.length} of {pagination.totalItems}
                </Typography>
              )}
            </Stack>

            <Grid container spacing={2.5}>
              {oauthApps.map((app, index) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={app._id}>
                  <Fade
                    in
                    timeout={300}
                    style={{ transitionDelay: `${Math.min(index * 30, 300)}ms` }}
                  >
                      <Box>
                        <OAuthAppCard
                          app={app}
                          onClick={() => handleViewOAuthApp(app)}
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
                      Loading more apps...
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
        open={dialogOpen}
        onClose={handleCloseDialog}
        onSuccess={handleDialogSuccess}
        mode={dialogMode}
        connectorType={selectedConnectorType || undefined}
        oauthConfigId={selectedOAuthConfigId || undefined}
        onModeChange={handleDialogModeChange}
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
    </>
  );
};

export default MyOAuthApps;

